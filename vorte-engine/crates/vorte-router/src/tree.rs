use std::sync::Arc;

use parking_lot::RwLock;
use vorte_http::Method;

use crate::node::{Node, ParamNode, NodeType};
use crate::params::{MatchResult, Params};

pub struct Router {
    root: Arc<Node>,
    pending: RwLock<Node>,
    frozen: bool,
    route_count: Arc<std::sync::atomic::AtomicU32>,
}

impl Clone for Router {
    fn clone(&self) -> Self {
        let pending = self.pending.read().clone();
        Self {
            root: self.root.clone(),
            pending: RwLock::new(pending),
            frozen: self.frozen,
            route_count: self.route_count.clone(),
        }
    }
}

unsafe impl Send for Router {}
unsafe impl Sync for Router {}

impl Router {
    pub fn new() -> Self {
        Self {
            root: Arc::new(Node::new(NodeType::Static)),
            pending: RwLock::new(Node::new(NodeType::Static)),
            frozen: false,
            route_count: Arc::new(std::sync::atomic::AtomicU32::new(0)),
        }
    }

    pub fn add_route(&self, method: Method, path: &str, handler_id: u32) -> Result<(), String> {
        if self.frozen {
            return Err("Router is frozen and cannot accept new routes".into());
        }

        let normalized = Self::normalize_path(path);
        let segments = Self::split_segments(&normalized);

        let mut root = self.pending.write();

        if segments.is_empty() {
            root.add_handler(method, handler_id);
            self.route_count
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
            return Ok(());
        }

        Self::insert_segments(&mut root, &segments, method, handler_id);
        self.route_count
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);

        Ok(())
    }

    pub fn freeze(&mut self) {
        let root = std::mem::replace(&mut *self.pending.write(), Node::new(NodeType::Static));
        self.root = Arc::new(root);
        self.frozen = true;
    }

    pub fn match_route(&self, method: Method, path: &str) -> MatchResult {
        let root = if self.frozen {
            &self.root
        } else {
            return MatchResult::not_found();
        };

        let normalized = Self::normalize_path(path);
        let bytes = normalized.as_bytes();

        let mut params = Params::new();
        match Self::match_node(root, bytes, &normalized, 0, &mut params, method) {
            Some(handler_id) => MatchResult::found(handler_id, params),
            None => {
                let mut params2 = Params::new();
                match Self::match_node_any(root, bytes, &normalized, 0, &mut params2) {
                    Some(_) => {
                        let mut result = MatchResult::not_found();
                        result.params = params2;
                        result
                    }
                    None => MatchResult::not_found(),
                }
            }
        }
    }

    fn match_node<'a>(
        node: &Node,
        path: &[u8],
        path_str: &str,
        offset: u32,
        params: &mut Params,
        method: Method,
    ) -> Option<u32> {
        if path.len() < node.prefix.len() {
            return None;
        }

        if !path[..node.prefix.len()].eq_ignore_ascii_case(&node.prefix) {
            return None;
        }

        let remaining = &path[node.prefix.len()..];
        let current_offset = offset + node.prefix.len() as u32;

        if remaining.is_empty() {
            return node.handlers.get(method);
        }

        for child in &node.static_children {
            if let Some(id) = Self::match_node(child, remaining, path_str, current_offset, params, method) {
                return Some(id);
            }
        }

        if let Some(ref param_node) = node.param_child {
            let seg_end = remaining.iter().position(|&b| b == b'/').unwrap_or(remaining.len());
            if !remaining[..seg_end].is_empty() {
                params.push(&param_node.name, current_offset, seg_end as u32);
                let next_remaining = &remaining[seg_end..];
                if next_remaining.is_empty() {
                    return param_node.node.handlers.get(method);
                }
                if let Some(id) = Self::match_node(&param_node.node, next_remaining, path_str, current_offset + seg_end as u32, params, method) {
                    return Some(id);
                }
                params.pop();
            }
        }

        if let Some(ref wildcard) = node.wildcard_child {
            let name = wildcard.wildcard_name.as_deref().unwrap_or("path");
            params.push(name, current_offset, remaining.len() as u32);
            return wildcard.handlers.get(method);
        }

        None
    }

    fn match_node_any(
        node: &Node,
        path: &[u8],
        path_str: &str,
        offset: u32,
        params: &mut Params,
    ) -> Option<u32> {
        if path.len() < node.prefix.len() {
            return None;
        }

        if !path[..node.prefix.len()].eq_ignore_ascii_case(&node.prefix) {
            return None;
        }

        let remaining = &path[node.prefix.len()..];
        let current_offset = offset + node.prefix.len() as u32;

        if remaining.is_empty() {
            return node.handlers.entries().iter().find_map(|&h| h);
        }

        for child in &node.static_children {
            if Self::match_node_any(child, remaining, path_str, current_offset, params).is_some() {
                return Some(1);
            }
        }

        if let Some(ref param_node) = node.param_child {
            let seg_end = remaining.iter().position(|&b| b == b'/').unwrap_or(remaining.len());
            params.push(&param_node.name, current_offset, seg_end as u32);
            let next_remaining = &remaining[seg_end..];
            if next_remaining.is_empty() {
                return param_node.node.handlers.entries().iter().find_map(|&h| h);
            }
            if Self::match_node_any(&param_node.node, next_remaining, path_str, current_offset + seg_end as u32, params).is_some() {
                return Some(1);
            }
            params.pop();
        }

        if node.wildcard_child.is_some() {
            return Some(1);
        }

        None
    }

    fn insert_segments(
        root: &mut Node,
        segments: &[Segment],
        method: Method,
        handler_id: u32,
    ) {
        let mut current = root;

        for (i, segment) in segments.iter().enumerate() {
            let is_last = i == segments.len() - 1;

            match segment.seg_type {
                SegType::Static => {
                    let prefix = &segment.value;
                    let first_byte = prefix[0];

                    if let Some(idx) = current.find_static_child(first_byte) {
                        let child = &mut current.static_children[idx];
                        let common = child
                            .prefix
                            .iter()
                            .zip(prefix.iter())
                            .take_while(|(a, b)| a == b)
                            .count();

                        if common == child.prefix.len() && common == prefix.len() {
                            if is_last {
                                child.add_handler(method, handler_id);
                                return;
                            }
                            current = child;
                            continue;
                        }

                        if common < child.prefix.len() {
                            let old_prefix = std::mem::take(&mut child.prefix);
                            child.prefix = old_prefix[..common].to_vec();

                            let mut split = Node::with_prefix(&old_prefix[common..]);
                            split.handlers = std::mem::take(&mut child.handlers);
                            split.static_children = std::mem::take(&mut child.static_children);
                            split.param_child = std::mem::take(&mut child.param_child);
                            split.wildcard_child = std::mem::take(&mut child.wildcard_child);
                            split.param_name = std::mem::take(&mut child.param_name);
                            split.wildcard_name = std::mem::take(&mut child.wildcard_name);

                            child.static_children.push(Box::new(split));

                            if common == prefix.len() {
                                if is_last {
                                    child.add_handler(method, handler_id);
                                    return;
                                }
                                current = child;
                                continue;
                            }

                            let mut new_child = Node::with_prefix(&prefix[common..]);
                            if is_last {
                                new_child.add_handler(method, handler_id);
                            }
                            child.static_children.push(Box::new(new_child));
                            current = if is_last { return; } else { child };
                            continue;
                        }

                        let remaining_prefix = &prefix[common..];
                        if is_last {
                            let mut new_child = Node::with_prefix(remaining_prefix);
                            new_child.add_handler(method, handler_id);
                            child.static_children.push(Box::new(new_child));
                            return;
                        }

                        let next_seg = &segments[i + 1..];
                        let mut new_child = Node::with_prefix(remaining_prefix);
                        Self::insert_segments_in_child(&mut new_child, next_seg, method, handler_id);
                        child.static_children.push(Box::new(new_child));
                        return;
                    }

                    let mut new_child = Node::with_prefix(prefix);
                    if is_last {
                        new_child.add_handler(method, handler_id);
                    } else {
                        let next_seg = &segments[i + 1..];
                        Self::insert_segments_in_child(&mut new_child, next_seg, method, handler_id);
                    }
                    current.static_children.push(Box::new(new_child));
                    return;
                }
                SegType::Param => {
                    if current.param_child.is_none() {
                        let mut param_node = Node::new(NodeType::Param);
                        param_node.param_name = Some(segment.param_name.clone());
                        current.param_child = Some(Box::new(ParamNode {
                            name: segment.param_name.clone(),
                            node: param_node,
                        }));
                    }

                    let param_child = current.param_child.as_mut().unwrap();
                    if is_last {
                        param_child.node.add_handler(method, handler_id);
                        return;
                    }
                    current = &mut param_child.node;
                }
                SegType::Wildcard => {
                    if current.wildcard_child.is_none() {
                        let mut wildcard = Node::new(NodeType::Wildcard);
                        wildcard.add_handler(method, handler_id);
                        wildcard.wildcard_name = Some(segment.param_name.clone());
                        current.wildcard_child = Some(Box::new(wildcard));
                        return;
                    }

                    let wildcard = current.wildcard_child.as_mut().unwrap();
                    wildcard.add_handler(method, handler_id);
                    return;
                }
            }
        }
    }

    fn insert_segments_in_child(
        node: &mut Node,
        segments: &[Segment],
        method: Method,
        handler_id: u32,
    ) {
        Self::insert_segments(node, segments, method, handler_id);
    }

    fn normalize_path(path: &str) -> String {
        if path.is_empty() || path == "/" {
            return String::new();
        }
        let p = if path.starts_with('/') { &path[1..] } else { path };
        if p.ends_with('/') && p.len() > 1 {
            p[..p.len() - 1].to_owned()
        } else {
            p.to_owned()
        }
    }

    fn split_segments(path: &str) -> Vec<Segment> {
        if path.is_empty() {
            return Vec::new();
        }

        let mut segments = Vec::new();
        let mut start = 0;

        while let Some(brace_start) = path[start..].find('{') {
            let brace_start_idx = start + brace_start;
            if brace_start_idx > start {
                segments.push(Segment {
                    seg_type: SegType::Static,
                    value: path[start..brace_start_idx].as_bytes().to_vec(),
                    param_name: String::new(),
                });
            }

            if let Some(brace_end) = path[brace_start_idx..].find('}') {
                let brace_end_idx = brace_start_idx + brace_end;
                let inner = &path[brace_start_idx + 1..brace_end_idx];
                if let Some(colon_pos) = inner.find(':') {
                    let name = &inner[..colon_pos];
                    if inner[colon_pos + 1..].starts_with("path") {
                        segments.push(Segment {
                            seg_type: SegType::Wildcard,
                            value: Vec::new(),
                            param_name: name.to_owned(),
                        });
                    } else {
                        segments.push(Segment {
                            seg_type: SegType::Param,
                            value: Vec::new(),
                            param_name: name.to_owned(),
                        });
                    }
                } else {
                    segments.push(Segment {
                        seg_type: SegType::Param,
                        value: Vec::new(),
                        param_name: inner.to_owned(),
                    });
                }
                start = brace_end_idx + 1;
            } else {
                break;
            }
        }

        if start < path.len() {
            segments.push(Segment {
                seg_type: SegType::Static,
                value: path[start..].as_bytes().to_vec(),
                param_name: String::new(),
            });
        }

        segments
    }

    pub fn route_count(&self) -> u32 {
        self.route_count.load(std::sync::atomic::Ordering::Relaxed)
    }
}

impl Default for Router {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug)]
struct Segment {
    seg_type: SegType,
    value: Vec<u8>,
    param_name: String,
}

#[derive(Debug, PartialEq)]
enum SegType {
    Static,
    Param,
    Wildcard,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_static_route() {
        let mut router = Router::new();
        router.add_route(Method::Get, "/users", 1).unwrap();
        router.freeze();
        let result = router.match_route(Method::Get, "/users");
        assert!(result.matched);
        assert_eq!(result.handler_id, 1);
    }

    #[test]
    fn test_param_route() {
        let mut router = Router::new();
        router.add_route(Method::Get, "/users/{id}", 2).unwrap();
        router.freeze();
        let result = router.match_route(Method::Get, "/users/42");
        assert!(result.matched);
        assert_eq!(result.handler_id, 2);
        assert_eq!(result.param_value("id", "/users/42"), Some("42"));
    }

    #[test]
    fn test_wildcard_route() {
        let mut router = Router::new();
        router.add_route(Method::Get, "/files/{path:path}", 3).unwrap();
        router.freeze();
        let result = router.match_route(Method::Get, "/files/a/b/c.txt");
        assert!(result.matched);
        assert_eq!(result.handler_id, 3);
    }

    #[test]
    fn test_not_found() {
        let mut router = Router::new();
        router.add_route(Method::Get, "/users", 1).unwrap();
        router.freeze();
        let result = router.match_route(Method::Get, "/posts");
        assert!(!result.matched);
    }

    #[test]
    fn test_method_dispatch() {
        let mut router = Router::new();
        router.add_route(Method::Get, "/items", 1).unwrap();
        router.add_route(Method::Post, "/items", 2).unwrap();
        router.freeze();

        let get_result = router.match_route(Method::Get, "/items");
        let post_result = router.match_route(Method::Post, "/items");
        assert!(get_result.matched);
        assert!(post_result.matched);
        assert_eq!(get_result.handler_id, 1);
        assert_eq!(post_result.handler_id, 2);
    }

    #[test]
    fn test_multi_param() {
        let mut router = Router::new();
        router.add_route(Method::Get, "/users/{user_id}/posts/{post_id}", 10).unwrap();
        router.freeze();
        let result = router.match_route(Method::Get, "/users/5/posts/42");
        assert!(result.matched);
        assert_eq!(result.handler_id, 10);
        assert_eq!(result.param_value("user_id", "/users/5/posts/42"), Some("5"));
        assert_eq!(result.param_value("post_id", "/users/5/posts/42"), Some("42"));
    }

    #[test]
    fn test_static_precedence() {
        let mut router = Router::new();
        router.add_route(Method::Get, "/users/me", 1).unwrap();
        router.add_route(Method::Get, "/users/{id}", 2).unwrap();
        router.freeze();

        let static_result = router.match_route(Method::Get, "/users/me");
        assert!(static_result.matched);
        assert_eq!(static_result.handler_id, 1);

        let param_result = router.match_route(Method::Get, "/users/123");
        assert!(param_result.matched);
        assert_eq!(param_result.handler_id, 2);
    }
}
