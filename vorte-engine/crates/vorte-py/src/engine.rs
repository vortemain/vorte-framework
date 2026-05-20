use std::net::SocketAddr;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

use vorte_http::Method;
use vorte_router::Router;

use crate::bridge::EventLoopHandle;
use crate::handler::create_python_handler;
use crate::metrics::{MetricsBuffer, MetricsCollector};

#[pyclass]
pub struct VorteEngine {
    router: Option<Router>,
    next_handler_id: AtomicU32,
    running: AtomicBool,
    metrics_buffer: MetricsBuffer,
}

#[pymethods]
impl VorteEngine {
    #[new]
    fn new() -> Self {
        VorteEngine {
            router: Some(Router::new()),
            next_handler_id: AtomicU32::new(1),
            running: AtomicBool::new(false),
            metrics_buffer: MetricsBuffer::new(),
        }
    }

    #[pyo3(signature = (method, path))]
    fn add_route(&self, method: &str, path: &str) -> PyResult<()> {
        if self.running.load(Ordering::SeqCst) {
            return Err(PyRuntimeError::new_err(
                "Cannot add routes while server is running",
            ));
        }

        let router = self
            .router
            .as_ref()
            .ok_or_else(|| PyRuntimeError::new_err("Router already consumed"))?;

        let m = parse_method(method)?;
        let handler_id = self.next_handler_id.fetch_add(1, Ordering::SeqCst);

        router
            .add_route(m, path, handler_id)
            .map_err(|e| PyValueError::new_err(e))
    }

    #[pyo3(signature = (app, host="0.0.0.0".to_string(), port=8000u16, workers=0usize))]
    fn run(
        &mut self,
        py: Python,
        app: Bound<'_, PyAny>,
        host: String,
        port: u16,
        workers: usize,
    ) -> PyResult<()> {
        if self.running.swap(true, Ordering::SeqCst) {
            return Err(PyRuntimeError::new_err("Server is already running"));
        }

        let mut router = self
            .router
            .take()
            .ok_or_else(|| PyRuntimeError::new_err("Router already consumed"))?;

        for route_info in extract_routes_from_app(py, &app)? {
            let m = parse_method(&route_info.method)?;
            let id = self.next_handler_id.fetch_add(1, Ordering::SeqCst);
            let _ = router.add_route(m, &route_info.path, id);
        }

        router.freeze();

        let event_loop_handle = EventLoopHandle::start(py)?;
        let event_loop_shutdown = event_loop_handle.clone();

        let app_py: Py<PyAny> = app.unbind();
        let addr: SocketAddr = format!("{}:{}", host, port)
            .parse()
            .map_err(|e| PyValueError::new_err(format!("Invalid address: {}", e)))?;

        let worker_count = if workers > 0 {
            workers
        } else {
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(4)
        };

        let metrics = self.metrics_buffer.clone();

        py.allow_threads(|| {
            let rt = tokio::runtime::Builder::new_multi_thread()
                .worker_threads(worker_count)
                .enable_all()
                .build()
                .map_err(|e| {
                    PyRuntimeError::new_err(format!("Failed to create runtime: {}", e))
                })?;

            let handler = create_python_handler(app_py, metrics, event_loop_handle);

            let server = vorte_core::Server::builder()
                .addr(addr)
                .worker_threads(worker_count)
                .build_with_router_and_handler(router, handler);

            rt.block_on(async {
                if let Err(e) = server.run().await {
                    eprintln!("Server error: {}", e);
                }
            });

            Python::with_gil(|py| {
                event_loop_shutdown.stop(py);
            });

            Ok(())
        })
    }

    #[getter]
    fn route_count(&self) -> usize {
        self.next_handler_id.load(Ordering::SeqCst) as usize
    }

    #[getter]
    fn is_running(&self) -> bool {
        return self.running.load(Ordering::SeqCst);
    }

    #[getter]
    fn metrics(&self, py: Python) -> PyResult<Py<MetricsCollector>> {
        let collector = MetricsCollector::from_buffer(self.metrics_buffer.clone());
        Py::new(py, collector)
    }
}

fn parse_method(method: &str) -> PyResult<Method> {
    match method.to_uppercase().as_str() {
        "GET" => Ok(Method::Get),
        "POST" => Ok(Method::Post),
        "PUT" => Ok(Method::Put),
        "DELETE" => Ok(Method::Delete),
        "PATCH" => Ok(Method::Patch),
        "HEAD" => Ok(Method::Head),
        "OPTIONS" => Ok(Method::Options),
        "TRACE" => Ok(Method::Trace),
        "CONNECT" => Ok(Method::Connect),
        _ => Err(PyValueError::new_err(format!(
            "Unsupported HTTP method: {}",
            method
        ))),
    }
}

struct RouteInfo {
    method: String,
    path: String,
}

fn extract_routes_from_app(_py: Python, app: &Bound<'_, PyAny>) -> PyResult<Vec<RouteInfo>> {
    let mut routes = Vec::new();

    let app_routes = if app.hasattr("fastapi")? {
        app.getattr("fastapi")?.getattr("routes")?
    } else if app.hasattr("routes")? {
        app.getattr("routes")?
    } else {
        return Ok(routes);
    };

    let route_list = app_routes.iter()?;

    for item in route_list {
        let route = item?;
        if route.hasattr("methods")? && route.hasattr("path")? {
            let path: String = route.getattr("path")?.extract()?;
            let methods: Option<Vec<String>> = route.getattr("methods")?.extract()?;

            if let Some(methods) = methods {
                for method in methods {
                    routes.push(RouteInfo {
                        method: method.to_uppercase(),
                        path: path.clone(),
                    });
                }
            }
        }
    }

    Ok(routes)
}
