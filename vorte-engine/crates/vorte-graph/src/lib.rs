use std::collections::HashMap;
use thiserror::Error;

#[derive(Error, Debug)]
pub enum GraphError {
    #[error("Execution failed at node: {0}")]
    ExecutionFailed(String),
    #[error("Node not found: {0}")]
    NodeNotFound(String),
    #[error("Cycle detected in the execution graph")]
    CycleDetected,
}

#[derive(Clone, Debug)]
pub enum ExecutionNode {
    Middleware {
        name: String,
        headers: HashMap<String, String>,
    },
    Dependency {
        name: String,
        resolver_type: String,
    },
    Query {
        name: String,
        sql: String,
    },
    Format {
        name: String,
        format_type: String,
    },
    PythonFallback {
        name: String,
    },
}

pub struct ExecutionGraph {
    pub nodes: HashMap<String, ExecutionNode>,
    pub adjacency_list: HashMap<String, Vec<String>>,
    pub roots: Vec<String>,
}

impl ExecutionGraph {
    pub fn new() -> Self {
        Self {
            nodes: HashMap::new(),
            adjacency_list: HashMap::new(),
            roots: Vec::new(),
        }
    }

    pub fn add_node(&mut self, id: String, node: ExecutionNode) {
        self.nodes.insert(id, node);
    }

    pub fn add_edge(&mut self, from: String, to: String) {
        self.adjacency_list.entry(from).or_insert_with(Vec::new).push(to);
    }

    pub fn execute(&self) -> Result<String, GraphError> {
        let mut executed_nodes = Vec::new();
        for root in &self.roots {
            self.execute_node_recursive(root, &mut executed_nodes)?;
        }
        Ok(format!(
            "Executed graph with nodes: {}",
            executed_nodes.join(" -> ")
        ))
    }

    fn execute_node_recursive(&self, node_id: &str, executed: &mut Vec<String>) -> Result<(), GraphError> {
        let node = self.nodes.get(node_id).ok_or_else(|| GraphError::NodeNotFound(node_id.to_string()))?;
        
        match node {
            ExecutionNode::Middleware { name, headers } => {
                if headers.contains_key("X-Short-Circuit") {
                    executed.push(format!("Middleware({}) [SHORT-CIRCUIT]", name));
                    return Ok(());
                }
                executed.push(format!("Middleware({})", name));
            }
            ExecutionNode::Dependency { name, .. } => {
                executed.push(format!("Dependency({})", name));
            }
            ExecutionNode::Query { name, .. } => {
                executed.push(format!("Query({})", name));
            }
            ExecutionNode::Format { name, .. } => {
                executed.push(format!("Format({})", name));
            }
            ExecutionNode::PythonFallback { name } => {
                executed.push(format!("PythonFallback({})", name));
            }
        }

        if let Some(neighbors) = self.adjacency_list.get(node_id) {
            for neighbor in neighbors {
                self.execute_node_recursive(neighbor, executed)?;
            }
        }

        Ok(())
    }
}

impl Default for ExecutionGraph {
    fn default() -> Self {
        Self::new()
    }
}
