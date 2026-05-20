use std::future::Future;
use std::net::SocketAddr;
use std::pin::Pin;
use std::sync::Arc;

use bytes::Bytes;
use hyper::body::Incoming;
use http_body_util::{BodyExt, Full};
use http_body_util::combinators::BoxBody;
use tracing::trace;

use vorte_http::{HttpResponse, Method};
use vorte_router::MatchResult;

pub type ResponseBody = BoxBody<Bytes, hyper::Error>;

pub fn box_full_response(resp: hyper::Response<Full<Bytes>>) -> hyper::Response<ResponseBody> {
    resp.map(|body| body.map_err(|never| match never {}).boxed())
}

pub type HandlerFn = Arc<
    dyn Fn(
        hyper::Request<Incoming>,
        Method,
        &str,
        &MatchResult,
        SocketAddr,
        Option<SocketAddr>,
    ) -> Pin<Box<dyn Future<Output = hyper::Response<ResponseBody>> + Send>>
        + Send
        + Sync,
>;

pub struct Pipeline {
    handler: parking_lot::RwLock<Option<HandlerFn>>,
    pre_routing_hooks: parking_lot::RwLock<Vec<PreRoutingHook>>,
    post_routing_hooks: parking_lot::RwLock<Vec<PostRoutingHook>>,
}

type PreRoutingHook = Arc<dyn Fn(&Method, &str) -> HookAction + Send + Sync>;
type PostRoutingHook =
    Arc<dyn Fn(&Method, &str, u16) -> Pin<Box<dyn Future<Output = ()> + Send>> + Send + Sync>;

#[derive(Clone, Copy, PartialEq)]
pub enum HookAction {
    Continue,
    Reject(u16),
}

impl Pipeline {
    pub fn new() -> Self {
        Self {
            handler: parking_lot::RwLock::new(None),
            pre_routing_hooks: parking_lot::RwLock::new(Vec::new()),
            post_routing_hooks: parking_lot::RwLock::new(Vec::new()),
        }
    }

    pub fn set_handler(&self, handler: HandlerFn) {
        *self.handler.write() = Some(handler);
    }

    pub fn add_pre_routing_hook(&self, hook: PreRoutingHook) {
        self.pre_routing_hooks.write().push(hook);
    }

    pub fn add_post_routing_hook(&self, hook: PostRoutingHook) {
        self.post_routing_hooks.write().push(hook);
    }

    pub async fn execute(
        &self,
        req: hyper::Request<Incoming>,
        method: Method,
        path: &str,
        match_result: &MatchResult,
        peer_addr: SocketAddr,
        server_addr: Option<SocketAddr>,
    ) -> hyper::Response<ResponseBody> {
        for hook in self.pre_routing_hooks.read().iter() {
            match hook(&method, path) {
                HookAction::Continue => {}
                HookAction::Reject(status) => {
                    trace!("Request rejected by pre-routing hook: {} {} -> {}", method, path, status);
                    return box_full_response(HttpResponse::new(status)
                        .json(format!(r#"{{"detail":"Request rejected"}}"#).as_bytes())
                        .into_hyper());
                }
            }
        }

        let handler_opt = self.handler.read().clone();
        let response = if let Some(handler) = handler_opt {
            handler(req, method, path, match_result, peer_addr, server_addr).await
        } else {
            box_full_response(HttpResponse::internal_error().into_hyper())
        };

        let status = response.status().as_u16();
        
        let hooks = self.post_routing_hooks.read().clone();
        for hook in hooks.iter() {
            hook(&method, path, status).await;
        }

        response
    }
}

impl Default for Pipeline {
    fn default() -> Self {
        Self::new()
    }
}
