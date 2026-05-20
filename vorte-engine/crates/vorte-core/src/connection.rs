use std::future::Future;
use std::net::SocketAddr;
use std::pin::Pin;
use std::sync::Arc;

use bytes::Bytes;
use hyper::body::Incoming;
use hyper::service::Service;
use hyper::Request;
use http_body_util::Full;
use crate::pipeline::{ResponseBody, box_full_response};
use tokio::net::TcpStream;
use tokio::sync::watch;
use tracing::{trace, warn};

use vorte_http::{HttpResponse, Method};
use vorte_router::Router;

use crate::pipeline::Pipeline;

pub struct ConnectionHandler {
    router: Arc<Router>,
    pipeline: Arc<Pipeline>,
    keep_alive: bool,
    enable_http2: bool,
}

impl ConnectionHandler {
    pub fn new(
        router: Arc<Router>,
        pipeline: Arc<Pipeline>,
        _keep_alive_timeout: std::time::Duration,
        enable_http2: bool,
    ) -> Self {
        Self {
            router,
            pipeline,
            keep_alive: true,
            enable_http2,
        }
    }

    pub async fn handle(
        &self,
        stream: TcpStream,
        peer_addr: SocketAddr,
        _shutdown: watch::Receiver<bool>,
    ) {
        let local_addr = stream.local_addr().ok();
        let io = hyper_util::rt::TokioIo::new(stream);

        let service = VorteService {
            router: self.router.clone(),
            pipeline: self.pipeline.clone(),
            peer_addr,
            server_addr: local_addr,
        };

        let result = if self.enable_http2 {
            self.serve_http2(io, service).await
        } else {
            self.serve_http1(io, service).await
        };

        if let Err(e) = result {
            let err_str = e.to_string();
            if err_str.contains("connection reset") || err_str.contains("broken pipe") {
                trace!("Connection closed by peer {}: {}", peer_addr, err_str);
            } else {
                warn!("Connection error for {}: {}", peer_addr, err_str);
            }
        }
    }

    fn serve_http1(
        &self,
        io: hyper_util::rt::TokioIo<TcpStream>,
        service: VorteService,
    ) -> impl std::future::Future<Output = Result<(), Box<dyn std::error::Error + Send + Sync>>> {
        let ka = self.keep_alive;
        async move {
            hyper::server::conn::http1::Builder::new()
                .keep_alive(ka)
                .serve_connection(io, service)
                .await
                .map_err(|e| Box::new(e) as Box<dyn std::error::Error + Send + Sync>)
        }
    }

    fn serve_http2(
        &self,
        io: hyper_util::rt::TokioIo<TcpStream>,
        service: VorteService,
    ) -> impl std::future::Future<Output = Result<(), Box<dyn std::error::Error + Send + Sync>>>
    {
        #[cfg(feature = "http2")]
        {
            async move {
                hyper::server::conn::http2::Builder::new(hyper_util::rt::TokioExecutor::new())
                    .serve_connection(io, service)
                    .await
                    .map_err(|e| Box::new(e) as Box<dyn std::error::Error + Send + Sync>)
            }
        }
        #[cfg(not(feature = "http2"))]
        {
            let _ = (io, service);
            async move {
                Err("HTTP/2 not enabled (compile with --features http2)".into())
            }
        }
    }
}

#[derive(Clone)]
struct VorteService {
    router: Arc<Router>,
    pipeline: Arc<Pipeline>,
    peer_addr: SocketAddr,
    server_addr: Option<SocketAddr>,
}

impl Service<Request<Incoming>> for VorteService {
    type Response = hyper::Response<ResponseBody>;
    type Error = hyper::Error;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, Self::Error>> + Send>>;

    fn call(&self, req: Request<Incoming>) -> Self::Future {
        let method = Method::from_standard(req.method().clone());
        let path = req.uri().path().to_owned();
        let router = self.router.clone();
        let pipeline = self.pipeline.clone();
        let peer_addr = self.peer_addr;
        let server_addr = self.server_addr;

        Box::pin(async move {
            let match_result = router.match_route(method, &path);

            let response = if match_result.matched {
                trace!(
                    "Route matched: {} {} -> handler {} ({} params)",
                    method, path, match_result.handler_id, match_result.params.len()
                );
                pipeline.execute(req, method, &path, &match_result, peer_addr, server_addr).await
            } else {
                trace!("No route match: {} {}", method, path);
                box_full_response(HttpResponse::not_found().into_hyper())
            };

            Ok(response)
        })
    }
}
