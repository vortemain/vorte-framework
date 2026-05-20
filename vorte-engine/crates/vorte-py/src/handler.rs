use std::net::SocketAddr;
use std::sync::Arc;
use std::time::Instant;

use bytes::Bytes;
use http_body_util::BodyExt;
use hyper::body::Incoming;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use tracing::error;

use vorte_core::pipeline::{HandlerFn, box_full_response};
use vorte_http::{HttpResponse, Method};
use vorte_router::MatchResult;

use crate::bridge::{build_asgi_scope, create_asgi_callables, run_asgi_on_loop, EventLoopHandle, AsgiStart};
use crate::metrics::{MetricsBuffer, Span};
use futures_util::{SinkExt, StreamExt};
use tokio_tungstenite::tungstenite::protocol::Role;
use tokio_tungstenite::tungstenite::Message;
use sha1::{Sha1, Digest};
use data_encoding::BASE64;

fn compute_ws_accept(key: &str) -> String {
    let mut hasher = Sha1::new();
    hasher.update(key.as_bytes());
    hasher.update(b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11");
    BASE64.encode(&hasher.finalize())
}

struct ChannelBody {
    rx: tokio::sync::mpsc::Receiver<Result<http_body::Frame<Bytes>, hyper::Error>>,
}

impl http_body::Body for ChannelBody {
    type Data = Bytes;
    type Error = hyper::Error;

    fn poll_frame(
        mut self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<Result<http_body::Frame<Self::Data>, Self::Error>>> {
        self.rx.poll_recv(cx)
    }
}

pub fn create_python_handler(
    app: Py<PyAny>,
    metrics: MetricsBuffer,
    event_loop: EventLoopHandle,
) -> HandlerFn {
    let app_arc = Arc::new(app);
    let event_loop = Arc::new(event_loop);
    Arc::new(
        move |mut req: hyper::Request<Incoming>,
              method: Method,
              path: &str,
              match_result: &MatchResult,
              peer_addr: SocketAddr,
              server_addr: Option<SocketAddr>| {
            let app = app_arc.clone();
            let path_owned = path.to_owned();
            let params = match_result.params.clone();
            let metrics = metrics.clone();
            let event_loop = event_loop.clone();

            Box::pin(async move {
                let is_websocket = req.headers()
                    .get("upgrade")
                    .and_then(|h| h.to_str().ok())
                    .map(|s| s.to_lowercase() == "websocket")
                    .unwrap_or(false);

                let query = req.uri().query().unwrap_or("").to_owned();

                let http_version = if req.version() == http::Version::HTTP_11 {
                    (1, 1)
                } else if req.version() == http::Version::HTTP_2 {
                    (2, 0)
                } else if req.version() == http::Version::HTTP_10 {
                    (1, 0)
                } else {
                    (1, 1)
                };

                let headers: Vec<(String, Vec<u8>)> = req
                    .headers()
                    .iter()
                    .map(|(name, value)| (name.as_str().to_owned(), value.as_bytes().to_vec()))
                    .collect();

                let (tx_start, rx_start) = tokio::sync::oneshot::channel::<AsgiStart>();
                let (tx_body, rx_body) = tokio::sync::mpsc::channel::<Result<http_body::Frame<Bytes>, hyper::Error>>(1024);

                let (receive, send, ws_channels) = if is_websocket {
                    let (client_to_py_tx, client_to_py_rx) = crossbeam_channel::unbounded::<PyObject>();
                    let (py_to_client_tx, py_to_client_rx) = crossbeam_channel::unbounded::<PyObject>();
                    
                    let res = Python::with_gil(|py| {
                        create_asgi_callables(
                            py,
                            &[],
                            tx_start,
                            tx_body,
                            Some(client_to_py_rx),
                            Some(py_to_client_tx),
                        )
                    });
                    
                    match res {
                        Ok((rec, sen)) => (rec, sen, Some((client_to_py_tx, py_to_client_rx))),
                        Err(e) => {
                            error!("Failed to create ASGI callables for WS: {}", e);
                            return box_full_response(HttpResponse::internal_error().into_hyper());
                        }
                    }
                } else {
                    // Read request body asynchronously
                    let req_body = req.body_mut();
                    let mut body_bytes = Vec::new();
                    while let Some(chunk_res) = req_body.frame().await {
                        match chunk_res {
                            Ok(frame) => {
                                if let Some(data) = frame.data_ref() {
                                    body_bytes.extend_from_slice(data);
                                }
                            }
                            Err(e) => {
                                error!("Failed to read request body: {}", e);
                                return box_full_response(HttpResponse::internal_error().into_hyper());
                            }
                        }
                    }

                    let res = Python::with_gil(|py| {
                        create_asgi_callables(
                            py,
                            &body_bytes,
                            tx_start,
                            tx_body,
                            None,
                            None,
                        )
                    });

                    match res {
                        Ok((rec, sen)) => (rec, sen, None),
                        Err(e) => {
                            error!("Failed to create ASGI callables for HTTP: {}", e);
                            return box_full_response(HttpResponse::internal_error().into_hyper());
                        }
                    }
                };

                let scope_res = Python::with_gil(|py| {
                    build_asgi_scope(
                        py,
                        method,
                        &path_owned,
                        &query,
                        &headers,
                        Some(peer_addr),
                        server_addr,
                        http_version,
                        &params,
                        is_websocket,
                    )
                });

                let scope = match scope_res {
                    Ok(s) => s,
                    Err(e) => {
                        error!("Failed to build ASGI scope: {}", e);
                        return box_full_response(HttpResponse::internal_error().into_hyper());
                    }
                };

                let t0 = Instant::now();
                if let Err(e) = Python::with_gil(|py| {
                    run_asgi_on_loop(py, &*app, scope, receive, send, event_loop.loop_ref())
                }) {
                    error!("Failed to run ASGI app on loop: {}", e);
                    return box_full_response(HttpResponse::internal_error().into_hyper());
                }

                if is_websocket {
                    let start_res = tokio::time::timeout(std::time::Duration::from_secs(30), rx_start).await;
                    let accepted = match start_res {
                        Ok(Ok(start_data)) => start_data.status == 101,
                        _ => false,
                    };

                    if !accepted {
                        error!("WebSocket connection rejected or failed to accept");
                        return box_full_response(HttpResponse::new(400).body("WebSocket rejected").into_hyper());
                    }

                    let ws_key = req.headers()
                        .get("sec-websocket-key")
                        .and_then(|h| h.to_str().ok())
                        .unwrap_or("")
                        .to_owned();
                    let accept = compute_ws_accept(&ws_key);

                    let builder = http::Response::builder()
                        .status(101)
                        .header("upgrade", "websocket")
                        .header("connection", "upgrade")
                        .header("sec-websocket-accept", accept);

                    let response = builder.body(http_body_util::Empty::new().map_err(|never| match never {}).boxed()).unwrap();

                    // Spawn the tokio task to bridge the upgraded connection
                    if let Some((client_to_py_tx, py_to_client_rx)) = ws_channels {
                        let upgrade_fut = hyper::upgrade::on(&mut req);
                        
                        tokio::spawn(async move {
                            let upgraded = match upgrade_fut.await {
                                Ok(u) => u,
                                Err(e) => {
                                    error!("Upgrade failed: {}", e);
                                    return;
                                }
                            };
                            let io = hyper_util::rt::TokioIo::new(upgraded);
                            let ws_stream = tokio_tungstenite::WebSocketStream::from_raw_socket(io, Role::Server, None).await;

                            let (mut ws_sender, mut ws_receiver) = ws_stream.split();

                            // Send websocket.connect to Python
                            Python::with_gil(|py| {
                                let dict = PyDict::new_bound(py);
                                dict.set_item("type", "websocket.connect").unwrap();
                                let _ = client_to_py_tx.send(dict.into_any().unbind());
                            });

                            // Spawn reader task
                            let client_to_py_tx_clone = client_to_py_tx.clone();
                            let reader_handle = tokio::spawn(async move {
                                while let Some(msg_res) = ws_receiver.next().await {
                                    match msg_res {
                                        Ok(msg) => {
                                            if msg.is_text() || msg.is_binary() {
                                                Python::with_gil(|py| {
                                                    let dict = PyDict::new_bound(py);
                                                    dict.set_item("type", "websocket.receive").unwrap();
                                                    if msg.is_text() {
                                                        dict.set_item("text", msg.to_text().unwrap_or("")).unwrap();
                                                        dict.set_item("bytes", py.None()).unwrap();
                                                    } else {
                                                        dict.set_item("text", py.None()).unwrap();
                                                        dict.set_item("bytes", PyBytes::new_bound(py, msg.into_data().as_slice())).unwrap();
                                                    }
                                                    let _ = client_to_py_tx_clone.send(dict.into_any().unbind());
                                                });
                                            } else if let Message::Close(cf) = msg {
                                                let code = if let Some(ref frame) = cf {
                                                    frame.code.into()
                                                } else {
                                                    1000
                                                };
                                                Python::with_gil(|py| {
                                                    let dict = PyDict::new_bound(py);
                                                    dict.set_item("type", "websocket.disconnect").unwrap();
                                                    dict.set_item("code", code).unwrap();
                                                    let _ = client_to_py_tx_clone.send(dict.into_any().unbind());
                                                });
                                                break;
                                            }
                                        }
                                        Err(e) => {
                                            error!("WebSocket read error: {}", e);
                                            break;
                                        }
                                    }
                                }
                            });

                            // Spawn writer task
                            let rt_handle = tokio::runtime::Handle::current();
                            let py_to_client_rx_clone = py_to_client_rx.clone();
                            let _writer_handle = std::thread::spawn(move || {
                                let mut ws_sender = ws_sender;
                                while let Ok(msg_obj) = py_to_client_rx_clone.recv() {
                                    let action = Python::with_gil(|py| -> Option<(Message, bool)> {
                                        let dict = msg_obj.bind(py).downcast::<PyDict>().ok()?.clone();
                                        let msg_type: String = dict.get_item("type").ok()??.extract().ok()?;
                                        match msg_type.as_str() {
                                            "websocket.send" => {
                                                if let Some(text_val) = dict.get_item("text").ok()? {
                                                    if !text_val.is_none() {
                                                        let text: String = text_val.extract().ok()?;
                                                        return Some((Message::Text(text.into()), false));
                                                    }
                                                }
                                                if let Some(bytes_val) = dict.get_item("bytes").ok()? {
                                                    if !bytes_val.is_none() {
                                                        let bytes: Vec<u8> = bytes_val.extract().ok()?;
                                                        return Some((Message::Binary(bytes.into()), false));
                                                    }
                                                }
                                                None
                                            }
                                            "websocket.close" => {
                                                let code = dict.get_item("code").ok()??.extract::<u16>().unwrap_or(1000);
                                                Some((Message::Close(Some(
                                                    tokio_tungstenite::tungstenite::protocol::CloseFrame {
                                                        code: code.into(),
                                                        reason: "".into(),
                                                    }
                                                )), true))
                                            }
                                            _ => None
                                        }
                                    });

                                    if let Some((ws_msg, is_close)) = action {
                                        let res = rt_handle.block_on(async {
                                            ws_sender.send(ws_msg).await
                                        });
                                        if res.is_err() || is_close {
                                            break;
                                        }
                                    }
                                }
                            });

                            // Await reader finish
                            let _ = reader_handle.await;
                        });
                    }

                    let latency_ns = t0.elapsed().as_nanos() as u64;
                    metrics.push(Span {
                        method: "GET".to_owned(),
                        path: path_owned.clone(),
                        status: 101,
                        latency_ns,
                    });

                    return response;
                }

                // Await start of response
                let start_res = tokio::time::timeout(std::time::Duration::from_secs(30), rx_start).await;
                let start_data = match start_res {
                    Ok(Ok(data)) => data,
                    _ => {
                        error!("Timeout or error waiting for ASGI response start");
                        return box_full_response(HttpResponse::internal_error().into_hyper());
                    }
                };

                let mut builder = http::Response::builder().status(start_data.status);
                for (name, value) in start_data.headers {
                    builder = builder.header(&name, value);
                }

                let body = ChannelBody { rx: rx_body }.boxed();
                let latency_ns = t0.elapsed().as_nanos() as u64;
                metrics.push(Span {
                    method: method.as_str().to_owned(),
                    path: path_owned.clone(),
                    status: start_data.status,
                    latency_ns,
                });

                builder.body(body).unwrap_or_else(|e| {
                    error!("Failed to build hyper response: {}", e);
                    box_full_response(HttpResponse::internal_error().into_hyper())
                })
            })
        },
    )
}
