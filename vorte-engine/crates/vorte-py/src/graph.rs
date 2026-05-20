use pyo3::prelude::*;
use std::collections::HashMap;
use vorte_graph::{ExecutionGraph, ExecutionNode};

#[pyclass]
pub struct PyExecutionGraph {
    inner: ExecutionGraph,
}

#[pymethods]
impl PyExecutionGraph {
    #[new]
    pub fn new() -> Self {
        PyExecutionGraph {
            inner: ExecutionGraph::new(),
        }
    }

    pub fn add_middleware_node(&mut self, id: String, name: String, headers: HashMap<String, String>) {
        self.inner.add_node(id, ExecutionNode::Middleware { name, headers });
    }

    pub fn add_dependency_node(&mut self, id: String, name: String, resolver_type: String) {
        self.inner.add_node(id, ExecutionNode::Dependency { name, resolver_type });
    }

    pub fn add_query_node(&mut self, id: String, name: String, sql: String) {
        self.inner.add_node(id, ExecutionNode::Query { name, sql });
    }

    pub fn add_format_node(&mut self, id: String, name: String, format_type: String) {
        self.inner.add_node(id, ExecutionNode::Format { name, format_type });
    }

    pub fn add_python_fallback_node(&mut self, id: String, name: String) {
        self.inner.add_node(id, ExecutionNode::PythonFallback { name });
    }

    pub fn add_edge(&mut self, from: String, to: String) {
        self.inner.add_edge(from, to);
    }

    pub fn add_root(&mut self, id: String) {
        self.inner.roots.push(id);
    }

    pub fn execute(&self) -> PyResult<String> {
        self.inner.execute().map_err(|e| {
            pyo3::exceptions::PyRuntimeError::new_err(e.to_string())
        })
    }
}

impl Default for PyExecutionGraph {
    fn default() -> Self {
        Self::new()
    }
}
