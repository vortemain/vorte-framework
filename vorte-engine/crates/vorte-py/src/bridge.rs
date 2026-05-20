use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyTuple, PyAny};

use vorte_http::Method;
use vorte_router::Params;

pub struct AsgiStart {
    pub status: u16,
    pub headers: Vec<(String, Vec<u8>)>,
}

#[pyclass]
pub struct AsgiReceive {
    body: Vec<u8>,
    consumed: std::sync::atomic::AtomicBool,
    ws_rx: Option<crossbeam_channel::Receiver<PyObject>>,
}

#[pymethods]
impl AsgiReceive {
    #[pyo3(signature = ())]
    fn __call__(&self, py: Python) -> PyResult<PyObject> {
        let asyncio = py.import_bound("asyncio")?;
        let loop_ = asyncio.call_method0("get_running_loop")?;
        let fut = loop_.call_method0("create_future")?;

        if let Some(ref rx) = self.ws_rx {
            if let Ok(msg) = rx.try_recv() {
                fut.call_method1("set_result", (msg,))?;
            } else {
                let rx_clone = rx.clone();
                let fut_clone: Py<PyAny> = fut.clone().unbind();
                let loop_clone: Py<PyAny> = loop_.clone().unbind();

                std::thread::spawn(move || {
                    if let Ok(msg) = rx_clone.recv() {
                        Python::with_gil(|py| {
                            let f = fut_clone.bind(py);
                            let l = loop_clone.bind(py);
                            let set_result = f.getattr("set_result").unwrap();
                            let _ = l.call_method1("call_soon_threadsafe", (set_result, msg));
                        });
                    }
                });
            }
        } else {
            let dict = PyDict::new_bound(py);
            if !self.consumed.swap(true, std::sync::atomic::Ordering::SeqCst) {
                dict.set_item("type", "http.request")?;
                dict.set_item("body", PyBytes::new_bound(py, &self.body))?;
                dict.set_item("more_body", false)?;
            } else {
                dict.set_item("type", "http.disconnect")?;
            }
            fut.call_method1("set_result", (dict,))?;
        }

        Ok(fut.into_any().unbind())
    }
}

#[pyclass]
pub struct AsgiSend {
    tx_start: std::sync::Mutex<Option<tokio::sync::oneshot::Sender<AsgiStart>>>,
    tx_body: tokio::sync::mpsc::Sender<Result<http_body::Frame<bytes::Bytes>, hyper::Error>>,
    ws_tx: Option<crossbeam_channel::Sender<PyObject>>,
}

#[pymethods]
impl AsgiSend {
    fn __call__(&self, py: Python, message: &Bound<'_, PyDict>) -> PyResult<()> {
        let msg_type: String = message
            .get_item("type")?
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("Missing 'type' in ASGI message"))?
            .extract()?;

        match msg_type.as_str() {
            "http.response.start" => {
                let status: u16 = message
                    .get_item("status")?
                    .ok_or_else(|| {
                        pyo3::exceptions::PyValueError::new_err("Missing 'status' in response.start")
                    })?
                    .extract()?;

                let mut headers = Vec::new();
                if let Some(raw_headers) = message.get_item("headers")? {
                    let header_list = raw_headers.downcast::<PyList>()?;
                    for item in header_list.iter() {
                        let tuple = item.downcast::<PyTuple>()?;
                        let name_bound = tuple.get_item(0)?;
                        let name: &[u8] = name_bound.extract()?;
                        let value_bound = tuple.get_item(1)?;
                        let value: &[u8] = value_bound.extract()?;
                        headers.push((
                            std::str::from_utf8(name)
                                .unwrap_or("unknown")
                                .to_owned(),
                            value.to_vec(),
                        ));
                    }
                }

                if let Some(tx) = self.tx_start.lock().unwrap().take() {
                    let _ = tx.send(AsgiStart { status, headers });
                }
            }
            "http.response.body" => {
                let body_val = message.get_item("body")?;
                let body_data: &[u8] = if let Some(ref b) = body_val {
                    b.extract()?
                } else {
                    &[]
                };

                if !body_data.is_empty() {
                    let frame = http_body::Frame::data(bytes::Bytes::copy_from_slice(body_data));
                    let _ = self.tx_body.blocking_send(Ok(frame));
                }
            }
            "websocket.accept" => {
                if let Some(tx) = self.tx_start.lock().unwrap().take() {
                    let _ = tx.send(AsgiStart {
                        status: 101,
                        headers: Vec::new(),
                    });
                }
                if let Some(ref tx) = self.ws_tx {
                    let _ = tx.send(message.clone().into_any().unbind());
                }
            }
            "websocket.send" | "websocket.close" => {
                if let Some(ref tx) = self.ws_tx {
                    let _ = tx.send(message.clone().into_any().unbind());
                }
            }
            _ => {}
        }

        Ok(())
    }
}

pub fn create_asgi_callables(
    py: Python,
    body: &[u8],
    tx_start: tokio::sync::oneshot::Sender<AsgiStart>,
    tx_body: tokio::sync::mpsc::Sender<Result<http_body::Frame<bytes::Bytes>, hyper::Error>>,
    ws_rx: Option<crossbeam_channel::Receiver<PyObject>>,
    ws_tx: Option<crossbeam_channel::Sender<PyObject>>,
) -> PyResult<(
    Py<AsgiReceive>,
    Py<AsgiSend>,
)> {
    let receive = Py::new(
        py,
        AsgiReceive {
            body: body.to_vec(),
            consumed: std::sync::atomic::AtomicBool::new(false),
            ws_rx,
        },
    )?;

    let send = Py::new(
        py,
        AsgiSend {
            tx_start: std::sync::Mutex::new(Some(tx_start)),
            tx_body,
            ws_tx,
        },
    )?;

    Ok((receive, send))
}

pub fn build_asgi_scope(
    py: Python,
    method: Method,
    path: &str,
    query: &str,
    headers: &[(String, Vec<u8>)],
    peer_addr: Option<std::net::SocketAddr>,
    server_addr: Option<std::net::SocketAddr>,
    http_version: (u8, u8),
    params: &Params,
    is_websocket: bool,
) -> PyResult<Py<PyDict>> {
    let scope = PyDict::new_bound(py);

    if is_websocket {
        scope.set_item("type", "websocket")?;
    } else {
        scope.set_item("type", "http")?;
    }

    let asgi = PyDict::new_bound(py);
    asgi.set_item("version", "3.0")?;
    asgi.set_item("spec_version", "2.3")?;
    scope.set_item("asgi", asgi)?;

    scope.set_item(
        "http_version",
        format!("{}.{}", http_version.0, http_version.1),
    )?;
    
    if !is_websocket {
        scope.set_item("method", method.as_str())?;
    }
    
    scope.set_item("scheme", "ws")?;
    scope.set_item("path", path)?;
    scope.set_item("query_string", PyBytes::new_bound(py, query.as_bytes()))?;
    scope.set_item("root_path", "")?;

    let header_list = PyList::empty_bound(py);
    for (name, value) in headers {
        let mut name_lower = name.as_bytes().to_vec();
        name_lower.make_ascii_lowercase();
        header_list.append((
            PyBytes::new_bound(py, &name_lower),
            PyBytes::new_bound(py, value),
        ))?;
    }
    scope.set_item("headers", header_list)?;

    if let Some(addr) = server_addr {
        scope.set_item("server", (addr.ip().to_string(), addr.port()))?;
    }

    if let Some(addr) = peer_addr {
        scope.set_item("client", (addr.ip().to_string(), addr.port()))?;
    }

    let path_params = PyDict::new_bound(py);
    let normalized = if path.starts_with('/') {
        &path[1..]
    } else {
        path
    };
    let trimmed = if normalized.ends_with('/') && normalized.len() > 1 {
        &normalized[..normalized.len() - 1]
    } else {
        normalized
    };
    for param in params.iter() {
        let value = param.value(trimmed);
        path_params.set_item(&param.key, value)?;
    }
    scope.set_item("path_params", path_params)?;

    if is_websocket {
        let subprotocols = PyList::empty_bound(py);
        scope.set_item("subprotocols", subprotocols)?;
    }

    Ok(scope.unbind())
}

pub fn run_asgi_on_loop(
    py: Python,
    app: &Py<PyAny>,
    scope: Py<PyDict>,
    receive: Py<AsgiReceive>,
    send: Py<AsgiSend>,
    event_loop: &Py<PyAny>,
) -> PyResult<()> {
    let asyncio = py.import_bound("asyncio")?;

    let coro = app
        .bind(py)
        .call1((scope.bind(py), receive.bind(py), send.bind(py)))?;

    let future =
        asyncio.call_method1("run_coroutine_threadsafe", (coro, event_loop.bind(py)))?;

    // Return immediately! We run asynchronously.
    let _ = future;

    Ok(())
}

impl Clone for EventLoopHandle {
    fn clone(&self) -> Self {
        Python::with_gil(|py| EventLoopHandle {
            loop_ref: self.loop_ref.clone_ref(py),
            ready: self.ready.clone(),
        })
    }
}

pub struct EventLoopHandle {
    loop_ref: Py<PyAny>,
    ready: Arc<AtomicBool>,
}

impl EventLoopHandle {
    pub fn start(py: Python) -> PyResult<Self> {
        let asyncio = py.import_bound("asyncio")?;
        let loop_ = asyncio.call_method0("new_event_loop")?;
        let loop_ref: Py<PyAny> = loop_.unbind();

        let ready = Arc::new(AtomicBool::new(false));
        let ready_clone = ready.clone();
        let loop_for_thread = loop_ref.clone_ref(py);

        thread::Builder::new()
            .name("vorte-asyncio-loop".to_string())
            .spawn(move || {
                Python::with_gil(|py| {
                    let l = loop_for_thread.bind(py);
                    if let Ok(asyncio_mod) = py.import_bound("asyncio") {
                        let _ = asyncio_mod.call_method1("set_event_loop", (l,));
                    }
                    ready_clone.store(true, Ordering::SeqCst);
                    let _ = l.call_method0("run_forever");
                });
            })
            .map_err(|e| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "Failed to start event loop thread: {}",
                    e
                ))
            })?;

        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while !ready.load(Ordering::SeqCst) {
            if std::time::Instant::now() > deadline {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "Event loop thread failed to start within 5 seconds",
                ));
            }
            std::thread::yield_now();
        }

        Ok(EventLoopHandle { loop_ref, ready })
    }

    pub fn loop_ref(&self) -> &Py<PyAny> {
        &self.loop_ref
    }

    pub fn stop(&self, py: Python) {
        if self.ready.load(Ordering::SeqCst) {
            let _ = self.loop_ref.bind(py).call_method0("stop");
        }
    }
}
