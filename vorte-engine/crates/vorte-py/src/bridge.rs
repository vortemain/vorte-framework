use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList, PyTuple, PyAny};
use pyo3::sync::GILOnceCell;

use vorte_http::Method;
use vorte_router::Params;
use parking_lot::RwLock;

use std::cell::RefCell;

static ASYNCIO_MOD: GILOnceCell<PyObject> = GILOnceCell::new();
pub static TOKIO_HANDLE: RwLock<Option<tokio::runtime::Handle>> = RwLock::new(None);

thread_local! {
    static COMPLETED_FUTURE: RefCell<Option<PyObject>> = RefCell::new(None);
    static EMPTY_REQUEST_FUTURE: RefCell<Option<PyObject>> = RefCell::new(None);
    static DISCONNECT_FUTURE: RefCell<Option<PyObject>> = RefCell::new(None);
}

fn get_completed_future(py: Python) -> PyResult<PyObject> {
    COMPLETED_FUTURE.with(|cell| {
        if let Some(ref fut) = *cell.borrow() {
            return Ok(fut.clone_ref(py));
        }
        let asyncio_mod = ASYNCIO_MOD.get_or_try_init(py, || {
            let m = py.import_bound(pyo3::intern!(py, "asyncio"))?;
            Ok::<PyObject, PyErr>(m.into())
        })?;
        let asyncio = asyncio_mod.bind(py);
        let loop_ = asyncio.call_method0(pyo3::intern!(py, "get_running_loop"))?;
        let fut = loop_.call_method0(pyo3::intern!(py, "create_future"))?;
        fut.call_method1(pyo3::intern!(py, "set_result"), (py.None(),))?;
        let fut_py = fut.unbind();
        *cell.borrow_mut() = Some(fut_py.clone_ref(py));
        Ok(fut_py)
    })
}

fn get_empty_request_future(py: Python) -> PyResult<PyObject> {
    EMPTY_REQUEST_FUTURE.with(|cell| {
        if let Some(ref fut) = *cell.borrow() {
            return Ok(fut.clone_ref(py));
        }
        let asyncio_mod = ASYNCIO_MOD.get_or_try_init(py, || {
            let m = py.import_bound(pyo3::intern!(py, "asyncio"))?;
            Ok::<PyObject, PyErr>(m.into())
        })?;
        let asyncio = asyncio_mod.bind(py);
        let loop_ = asyncio.call_method0(pyo3::intern!(py, "get_running_loop"))?;
        let fut = loop_.call_method0(pyo3::intern!(py, "create_future"))?;
        
        let dict = PyDict::new_bound(py);
        dict.set_item(pyo3::intern!(py, "type"), pyo3::intern!(py, "http.request"))?;
        dict.set_item(pyo3::intern!(py, "body"), PyBytes::new_bound(py, &[]))?;
        dict.set_item(pyo3::intern!(py, "more_body"), false)?;
        
        fut.call_method1(pyo3::intern!(py, "set_result"), (dict,))?;
        let fut_py = fut.unbind();
        *cell.borrow_mut() = Some(fut_py.clone_ref(py));
        Ok(fut_py)
    })
}

fn get_disconnect_future(py: Python) -> PyResult<PyObject> {
    DISCONNECT_FUTURE.with(|cell| {
        if let Some(ref fut) = *cell.borrow() {
            return Ok(fut.clone_ref(py));
        }
        let asyncio_mod = ASYNCIO_MOD.get_or_try_init(py, || {
            let m = py.import_bound(pyo3::intern!(py, "asyncio"))?;
            Ok::<PyObject, PyErr>(m.into())
        })?;
        let asyncio = asyncio_mod.bind(py);
        let loop_ = asyncio.call_method0(pyo3::intern!(py, "get_running_loop"))?;
        let fut = loop_.call_method0(pyo3::intern!(py, "create_future"))?;
        
        let dict = PyDict::new_bound(py);
        dict.set_item(pyo3::intern!(py, "type"), pyo3::intern!(py, "http.disconnect"))?;
        
        fut.call_method1(pyo3::intern!(py, "set_result"), (dict,))?;
        let fut_py = fut.unbind();
        *cell.borrow_mut() = Some(fut_py.clone_ref(py));
        Ok(fut_py)
    })
}


pub struct AsgiStart {
    pub status: u16,
    pub headers: Vec<(Vec<u8>, Vec<u8>)>,
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
        if let Some(ref rx) = self.ws_rx {
            let asyncio_mod = ASYNCIO_MOD.get_or_try_init(py, || {
                let m = py.import_bound(pyo3::intern!(py, "asyncio"))?;
                Ok::<PyObject, PyErr>(m.into())
            })?;
            let asyncio = asyncio_mod.bind(py);
            let loop_ = asyncio.call_method0(pyo3::intern!(py, "get_running_loop"))?;
            let fut = loop_.call_method0(pyo3::intern!(py, "create_future"))?;

            if let Ok(msg) = rx.try_recv() {
                fut.call_method1(pyo3::intern!(py, "set_result"), (msg,))?;
            } else {
                let rx_clone = rx.clone();
                let fut_clone: Py<PyAny> = fut.clone().unbind();
                let loop_clone: Py<PyAny> = loop_.clone().unbind();

                if let Some(handle) = TOKIO_HANDLE.read().clone() {
                    let handle_clone = handle.clone();
                    handle.spawn(async move {
                        let res = handle_clone.spawn_blocking(move || {
                            rx_clone.recv()
                        }).await;
                        if let Ok(Ok(msg)) = res {
                            Python::with_gil(|py| {
                                let f = fut_clone.bind(py);
                                let l = loop_clone.bind(py);
                                let set_result = f.getattr(pyo3::intern!(py, "set_result")).unwrap();
                                let _ = l.call_method1(pyo3::intern!(py, "call_soon_threadsafe"), (set_result, msg));
                            });
                        }
                    });
                }
            }
            Ok(fut.into_any().unbind())
        } else {
            if !self.consumed.swap(true, std::sync::atomic::Ordering::SeqCst) {
                if self.body.is_empty() {
                    return get_empty_request_future(py);
                }
                
                let asyncio_mod = ASYNCIO_MOD.get_or_try_init(py, || {
                    let m = py.import_bound(pyo3::intern!(py, "asyncio"))?;
                    Ok::<PyObject, PyErr>(m.into())
                })?;
                let asyncio = asyncio_mod.bind(py);
                let loop_ = asyncio.call_method0(pyo3::intern!(py, "get_running_loop"))?;
                let fut = loop_.call_method0(pyo3::intern!(py, "create_future"))?;
                
                let dict = PyDict::new_bound(py);
                dict.set_item(pyo3::intern!(py, "type"), pyo3::intern!(py, "http.request"))?;
                dict.set_item(pyo3::intern!(py, "body"), PyBytes::new_bound(py, &self.body))?;
                dict.set_item(pyo3::intern!(py, "more_body"), false)?;
                fut.call_method1(pyo3::intern!(py, "set_result"), (dict,))?;
                Ok(fut.into_any().unbind())
            } else {
                get_disconnect_future(py)
            }
        }
    }
}

#[pyclass]
pub struct AsgiSend {
    tx_start: std::sync::Mutex<Option<tokio::sync::oneshot::Sender<AsgiStart>>>,
    tx_body: std::sync::Mutex<Option<tokio::sync::mpsc::Sender<Result<http_body::Frame<bytes::Bytes>, hyper::Error>>>>,
    ws_tx: Option<crossbeam_channel::Sender<PyObject>>,
}

#[pymethods]
impl AsgiSend {
    fn __call__(&self, py: Python, message: &Bound<'_, PyDict>) -> PyResult<PyObject> {
        let msg_type_obj = message
            .get_item(pyo3::intern!(py, "type"))?
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("Missing 'type' in ASGI message"))?;
        let msg_type: &str = msg_type_obj.extract()?;

        match msg_type {
            "http.response.start" => {
                let status: u16 = message
                    .get_item(pyo3::intern!(py, "status"))?
                    .ok_or_else(|| {
                        pyo3::exceptions::PyValueError::new_err("Missing 'status' in response.start")
                    })?
                    .extract()?;

                let mut headers = Vec::new();
                if let Some(raw_headers) = message.get_item(pyo3::intern!(py, "headers"))? {
                    let header_list = raw_headers.downcast::<PyList>()?;
                    for item in header_list.iter() {
                        let tuple = item.downcast::<PyTuple>()?;
                        let name_bound = tuple.get_item(0)?;
                        let name: &[u8] = name_bound.extract()?;
                        let value_bound = tuple.get_item(1)?;
                        let value: &[u8] = value_bound.extract()?;
                        headers.push((
                            name.to_vec(),
                            value.to_vec(),
                        ));
                    }
                }

                if let Some(tx) = self.tx_start.lock().unwrap().take() {
                    let _ = tx.send(AsgiStart { status, headers });
                }
            }
            "http.response.body" => {
                let body_val = message.get_item(pyo3::intern!(py, "body"))?;
                let body_data: &[u8] = if let Some(ref b) = body_val {
                    b.extract()?
                } else {
                    &[]
                };

                let more_body: bool = message
                    .get_item(pyo3::intern!(py, "more_body"))?
                    .map(|val| val.extract::<bool>().unwrap_or(false))
                    .unwrap_or(false);

                if !body_data.is_empty() {
                    let frame = http_body::Frame::data(bytes::Bytes::copy_from_slice(body_data));
                    let opt_tx = self.tx_body.lock().unwrap();
                    if let Some(ref tx) = *opt_tx {
                        let _ = tx.blocking_send(Ok(frame));
                    }
                }

                if !more_body {
                    let mut opt_tx = self.tx_body.lock().unwrap();
                    let _ = opt_tx.take(); // Drops the sender, closing the channel
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

        get_completed_future(py)
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
            tx_body: std::sync::Mutex::new(Some(tx_body)),
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
    headers: &http::HeaderMap,
    peer_addr: Option<std::net::SocketAddr>,
    server_addr: Option<std::net::SocketAddr>,
    http_version: (u8, u8),
    params: &Params,
    is_websocket: bool,
) -> PyResult<Py<PyDict>> {
    let scope = PyDict::new_bound(py);

    if is_websocket {
        scope.set_item(pyo3::intern!(py, "type"), pyo3::intern!(py, "websocket"))?;
    } else {
        scope.set_item(pyo3::intern!(py, "type"), pyo3::intern!(py, "http"))?;
    }

    let asgi = PyDict::new_bound(py);
    asgi.set_item(pyo3::intern!(py, "version"), pyo3::intern!(py, "3.0"))?;
    asgi.set_item(pyo3::intern!(py, "spec_version"), pyo3::intern!(py, "2.3"))?;
    scope.set_item(pyo3::intern!(py, "asgi"), asgi)?;

    let ver_str = match http_version {
        (1, 1) => "1.1",
        (2, 0) => "2.0",
        (1, 0) => "1.0",
        _ => "1.1",
    };
    scope.set_item(
        pyo3::intern!(py, "http_version"),
        ver_str,
    )?;
    
    if !is_websocket {
        scope.set_item(pyo3::intern!(py, "method"), method.as_str())?;
    }
    
    scope.set_item(pyo3::intern!(py, "scheme"), pyo3::intern!(py, "ws"))?;
    scope.set_item(pyo3::intern!(py, "path"), path)?;
    scope.set_item(pyo3::intern!(py, "query_string"), PyBytes::new_bound(py, query.as_bytes()))?;
    scope.set_item(pyo3::intern!(py, "root_path"), "")?;

    let header_list = PyList::empty_bound(py);
    for (name, value) in headers.iter() {
        header_list.append((
            PyBytes::new_bound(py, name.as_str().as_bytes()),
            PyBytes::new_bound(py, value.as_bytes()),
        ))?;
    }
    scope.set_item(pyo3::intern!(py, "headers"), header_list)?;

    if let Some(addr) = server_addr {
        scope.set_item(pyo3::intern!(py, "server"), (addr.ip().to_string(), addr.port()))?;
    }

    if let Some(addr) = peer_addr {
        scope.set_item(pyo3::intern!(py, "client"), (addr.ip().to_string(), addr.port()))?;
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
    scope.set_item(pyo3::intern!(py, "path_params"), path_params)?;

    if is_websocket {
        let subprotocols = PyList::empty_bound(py);
        scope.set_item(pyo3::intern!(py, "subprotocols"), subprotocols)?;
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
    let asyncio = py.import_bound(pyo3::intern!(py, "asyncio"))?;

    let coro = app
        .bind(py)
        .call1((scope.bind(py), receive.bind(py), send.bind(py)))?;

    let future =
        asyncio.call_method1(pyo3::intern!(py, "run_coroutine_threadsafe"), (coro, event_loop.bind(py)))?;

    // Return immediately! We run asynchronously.
    let _ = future;

    Ok(())
}

impl Clone for EventLoopHandle {
    fn clone(&self) -> Self {
        Python::with_gil(|py| {
            let loops = self.loops.iter().map(|l| l.clone_ref(py)).collect();
            EventLoopHandle {
                loops,
                ready: self.ready.clone(),
                index: self.index.clone(),
            }
        })
    }
}

pub struct EventLoopHandle {
    loops: Vec<Py<PyAny>>,
    ready: Arc<AtomicBool>,
    index: Arc<std::sync::atomic::AtomicUsize>,
}

impl EventLoopHandle {
    pub fn start(py: Python, worker_count: usize) -> PyResult<Self> {
        let asyncio = py.import_bound(pyo3::intern!(py, "asyncio"))?;
        
        let mut loops = Vec::with_capacity(worker_count);
        let ready = Arc::new(AtomicBool::new(false));
        let index = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let start_count = Arc::new(std::sync::atomic::AtomicUsize::new(0));

        for i in 0..worker_count {
            let loop_ = asyncio.call_method0(pyo3::intern!(py, "new_event_loop"))?;
            let loop_ref: Py<PyAny> = loop_.unbind();
            loops.push(loop_ref.clone_ref(py));

            let start_count_clone = start_count.clone();
            let loop_for_thread = loop_ref.clone_ref(py);

            thread::Builder::new()
                .name(format!("vorte-asyncio-loop-{}", i))
                .spawn(move || {
                    Python::with_gil(|py| {
                        let l = loop_for_thread.bind(py);
                        if let Ok(asyncio_mod) = py.import_bound(pyo3::intern!(py, "asyncio")) {
                            let _ = asyncio_mod.call_method1(pyo3::intern!(py, "set_event_loop"), (l,));
                        }
                        start_count_clone.fetch_add(1, Ordering::SeqCst);
                        let _ = l.call_method0(pyo3::intern!(py, "run_forever"));
                    });
                })
                .map_err(|e| {
                    pyo3::exceptions::PyRuntimeError::new_err(format!(
                        "Failed to start event loop thread {}: {}",
                        i, e
                    ))
                })?;
        }

        py.allow_threads(|| {
            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
            while start_count.load(Ordering::SeqCst) < worker_count {
                if std::time::Instant::now() > deadline {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(
                        "Event loop threads failed to start within 5 seconds",
                    ));
                }
                std::thread::yield_now();
            }
            Ok(())
        })?;
        ready.store(true, Ordering::SeqCst);

        Ok(EventLoopHandle { loops, ready, index })
    }

    pub fn loop_ref(&self) -> &Py<PyAny> {
        let idx = self.index.fetch_add(1, Ordering::Relaxed);
        &self.loops[idx % self.loops.len()]
    }

    pub fn stop(&self, py: Python) {
        if self.ready.load(Ordering::SeqCst) {
            for loop_ref in &self.loops {
                let _ = loop_ref.bind(py).call_method0(pyo3::intern!(py, "stop"));
            }
        }
    }
}
