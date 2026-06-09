use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Duration;

use tokio::net::TcpListener;
use tokio::sync::watch;
use tracing::{error, info, warn};

use vorte_router::Router;

use crate::connection::ConnectionHandler;
use crate::pipeline::{HandlerFn, Pipeline};

pub struct Server {
    addr: SocketAddr,
    router: Arc<Router>,
    pipeline: Arc<Pipeline>,
    worker_threads: usize,
    max_connections: usize,
    keep_alive: Duration,
    tcp_nodelay: bool,
    _tcp_reuseaddr: bool,
    shutdown_timeout: Duration,
    enable_http2: bool,
    listener: Option<std::net::TcpListener>,
}

impl Server {
    pub fn builder() -> ServerBuilder {
        ServerBuilder::default()
    }

    pub fn new(addr: SocketAddr) -> Self {
        Self::builder().addr(addr).build()
    }

    pub async fn run(self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let listener = if let Some(std_listener) = self.listener {
            std_listener.set_nonblocking(true)?;
            TcpListener::from_std(std_listener)?
        } else {
            TcpListener::bind(self.addr).await?
        };
        let local_addr = listener.local_addr()?;

        info!(
            "VORTE engine listening on {} (workers: {}, http2: {})",
            local_addr, self.worker_threads, self.enable_http2,
        );

        let (shutdown_tx, shutdown_rx) = watch::channel(false);
        let handler = Arc::new(ConnectionHandler::new(
            self.router.clone(),
            self.pipeline.clone(),
            self.keep_alive,
            self.enable_http2,
        ));

        let connection_count = Arc::new(std::sync::atomic::AtomicU64::new(0));
        let max_conn = self.max_connections;

        loop {
            tokio::select! {
                accept_result = listener.accept() => {
                    match accept_result {
                        Ok((stream, peer_addr)) => {
                            let current = connection_count.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                            if current as usize >= max_conn {
                                connection_count.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                                warn!("Max connections reached, rejecting {}", peer_addr);
                                continue;
                            }

                            if self.tcp_nodelay {
                                let _ = stream.set_nodelay(true);
                            }

                            let handler = handler.clone();
                            let counter = connection_count.clone();
                            let shutdown = shutdown_rx.clone();

                            tokio::spawn(async move {
                                handler.handle(stream, peer_addr, shutdown).await;
                                counter.fetch_sub(1, std::sync::atomic::Ordering::Relaxed);
                            });
                        }
                        Err(e) => {
                            error!("Accept error: {}", e);
                        }
                    }
                }
                _ = tokio::signal::ctrl_c() => {
                    info!("Shutdown signal received, draining connections...");
                    let _ = shutdown_tx.send(true);
                    tokio::time::sleep(self.shutdown_timeout).await;
                    info!("Server shutdown complete");
                    break;
                }
            }
        }

        Ok(())
    }
}

#[derive(Debug)]
pub struct ServerBuilder {
    addr: SocketAddr,
    worker_threads: usize,
    max_connections: usize,
    keep_alive: Duration,
    tcp_nodelay: bool,
    _tcp_reuseaddr: bool,
    shutdown_timeout: Duration,
    enable_http2: bool,
    listener: Option<std::net::TcpListener>,
}

impl Default for ServerBuilder {
    fn default() -> Self {
        Self {
            addr: SocketAddr::from(([0, 0, 0, 0], 8000)),
            worker_threads: num_cpus(),
            max_connections: 65536,
            keep_alive: Duration::from_secs(75),
            tcp_nodelay: true,
            _tcp_reuseaddr: true,
            shutdown_timeout: Duration::from_secs(30),
            enable_http2: false,
            listener: None,
        }
    }
}

impl ServerBuilder {
    pub fn addr(mut self, addr: SocketAddr) -> Self {
        self.addr = addr;
        self
    }

    pub fn host(self, host: &str) -> Self {
        let port = self.addr.port();
        let addr: SocketAddr = format!("{}:{}", host, port).parse().unwrap_or(self.addr);
        self.addr(addr)
    }

    pub fn port(mut self, port: u16) -> Self {
        let ip = self.addr.ip();
        self.addr = SocketAddr::new(ip, port);
        self
    }

    pub fn worker_threads(mut self, n: usize) -> Self {
        self.worker_threads = n.max(1);
        self
    }

    pub fn max_connections(mut self, n: usize) -> Self {
        self.max_connections = n;
        self
    }

    pub fn keep_alive(mut self, dur: Duration) -> Self {
        self.keep_alive = dur;
        self
    }

    pub fn tcp_nodelay(mut self, enabled: bool) -> Self {
        self.tcp_nodelay = enabled;
        self
    }

    pub fn http2(mut self, enabled: bool) -> Self {
        self.enable_http2 = enabled;
        self
    }

    pub fn shutdown_timeout(mut self, dur: Duration) -> Self {
        self.shutdown_timeout = dur;
        self
    }

    pub fn listener(mut self, listener: std::net::TcpListener) -> Self {
        self.listener = Some(listener);
        self
    }

    pub fn build(self) -> Server {
        Server {
            addr: self.addr,
            router: Arc::new(Router::new()),
            pipeline: Arc::new(Pipeline::new()),
            worker_threads: self.worker_threads,
            max_connections: self.max_connections,
            keep_alive: self.keep_alive,
            tcp_nodelay: self.tcp_nodelay,
            _tcp_reuseaddr: self._tcp_reuseaddr,
            shutdown_timeout: self.shutdown_timeout,
            enable_http2: self.enable_http2,
            listener: self.listener,
        }
    }

    pub fn build_with_router(self, router: Router) -> Server {
        Server {
            addr: self.addr,
            router: Arc::new(router),
            pipeline: Arc::new(Pipeline::new()),
            worker_threads: self.worker_threads,
            max_connections: self.max_connections,
            keep_alive: self.keep_alive,
            tcp_nodelay: self.tcp_nodelay,
            _tcp_reuseaddr: self._tcp_reuseaddr,
            shutdown_timeout: self.shutdown_timeout,
            enable_http2: self.enable_http2,
            listener: self.listener,
        }
    }

    pub fn build_with_router_and_handler(self, router: Router, handler: HandlerFn) -> Server {
        let pipeline = Pipeline::new();
        pipeline.set_handler(handler);
        Server {
            addr: self.addr,
            router: Arc::new(router),
            pipeline: Arc::new(pipeline),
            worker_threads: self.worker_threads,
            max_connections: self.max_connections,
            keep_alive: self.keep_alive,
            tcp_nodelay: self.tcp_nodelay,
            _tcp_reuseaddr: self._tcp_reuseaddr,
            shutdown_timeout: self.shutdown_timeout,
            enable_http2: self.enable_http2,
            listener: self.listener,
        }
    }
}

fn num_cpus() -> usize {
    std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(4)
}
