// Vorte Native Metrics Collector
// ================================
// Lock-free ring buffer for nanosecond-resolution request spans.
// Exposed to Python via PyO3 as `MetricsCollector`.
//
// Blueprint reference: §5.3 Zero-Overhead Metrics
//   "Continuous trace spans are tracked down to nanosecond resolutions via
//    compiled sidecar hooks, completely separating the performance matrix
//    tracking overhead from the primary application execution path."

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicU64, Ordering};

use pyo3::prelude::*;
use pyo3::types::PyDict;

pub static VORTE_SERIALIZATION_TIME_NS: AtomicU64 = AtomicU64::new(0);
pub static VORTE_DATABASE_WAIT_TIME_NS: AtomicU64 = AtomicU64::new(0);
pub static VORTE_SCHEDULING_LATENCY_NS: AtomicU64 = AtomicU64::new(0);
pub static VORTE_EVENT_LOOP_LAG_NS: AtomicU64 = AtomicU64::new(0);

/// Maximum spans held in the ring buffer before old entries are evicted.
const RING_BUFFER_CAPACITY: usize = 10_000;

/// A single request trace span with nanosecond resolution.
#[derive(Clone, Debug)]
pub struct Span {
    pub method: String,
    pub path: String,
    pub status: u16,
    pub latency_ns: u64,
}

/// Shared, thread-safe ring buffer backing the `MetricsCollector`.
#[derive(Clone)]
pub struct MetricsBuffer {
    inner: Arc<Mutex<VecDeque<Span>>>,
}

impl MetricsBuffer {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(VecDeque::with_capacity(RING_BUFFER_CAPACITY))),
        }
    }

    /// Push a new span; evicts the oldest entry when the buffer is full.
    pub fn push(&self, span: Span) {
        if let Ok(mut buf) = self.inner.lock() {
            if buf.len() >= RING_BUFFER_CAPACITY {
                buf.pop_front();
            }
            buf.push_back(span);
        }
    }

    /// Drain all spans from the buffer and return them.
    pub fn drain(&self) -> Vec<Span> {
        if let Ok(mut buf) = self.inner.lock() {
            buf.drain(..).collect()
        } else {
            Vec::new()
        }
    }

    /// Peek at the last *n* spans without removing them.
    pub fn tail(&self, n: usize) -> Vec<Span> {
        if let Ok(buf) = self.inner.lock() {
            let start = buf.len().saturating_sub(n);
            buf.range(start..).cloned().collect()
        } else {
            Vec::new()
        }
    }

    /// Current number of spans in the buffer.
    pub fn len(&self) -> usize {
        self.inner.lock().map(|b| b.len()).unwrap_or(0)
    }
}

impl Default for MetricsBuffer {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// PyO3 binding
// ---------------------------------------------------------------------------

/// Native nanosecond-resolution metrics collector exposed to Python.
///
/// Python usage::
///
///     from vorte._vorte_engine import MetricsCollector
///
///     mc = MetricsCollector()
///     mc.push("GET", "/api/users", 200, 45_123)   # latency in nanoseconds
///
///     spans = mc.drain()  # -> list[dict]
///     # [{"method": "GET", "path": "/api/users", "status": 200, "latency_ns": 45123}, ...]
#[pyclass]
pub struct MetricsCollector {
    buffer: MetricsBuffer,
}

impl MetricsCollector {
    /// Internal constructor used by VorteEngine to share its buffer.
    pub fn from_buffer(buffer: MetricsBuffer) -> Self {
        MetricsCollector { buffer }
    }
}

#[pymethods]
impl MetricsCollector {
    #[new]
    pub fn new() -> Self {
        MetricsCollector {
            buffer: MetricsBuffer::new(),
        }
    }

    /// Push a span into the ring buffer.
    ///
    /// Args:
    ///     method:     HTTP method string (e.g. "GET").
    ///     path:       Request path (e.g. "/api/users").
    ///     status:     HTTP status code.
    ///     latency_ns: Round-trip latency in nanoseconds.
    #[pyo3(signature = (method, path, status, latency_ns))]
    pub fn push(&self, method: String, path: String, status: u16, latency_ns: u64) {
        self.buffer.push(Span { method, path, status, latency_ns });
    }

    /// Drain all spans from the ring buffer, returning them as a list of dicts.
    ///
    /// After this call the buffer is empty.
    pub fn drain(&self, py: Python) -> PyResult<Vec<Py<PyDict>>> {
        let spans = self.buffer.drain();
        let mut result = Vec::with_capacity(spans.len());
        for span in spans {
            let d = PyDict::new_bound(py);
            d.set_item("method", &span.method)?;
            d.set_item("path", &span.path)?;
            d.set_item("status", span.status)?;
            d.set_item("latency_ns", span.latency_ns)?;
            d.set_item("latency_ms", span.latency_ns as f64 / 1_000_000.0)?;
            result.push(d.unbind());
        }
        Ok(result)
    }

    /// Peek at the last *n* spans without draining the buffer.
    #[pyo3(signature = (n = 100))]
    pub fn tail(&self, py: Python, n: usize) -> PyResult<Vec<Py<PyDict>>> {
        let spans = self.buffer.tail(n);
        let mut result = Vec::with_capacity(spans.len());
        for span in spans {
            let d = PyDict::new_bound(py);
            d.set_item("method", &span.method)?;
            d.set_item("path", &span.path)?;
            d.set_item("status", span.status)?;
            d.set_item("latency_ns", span.latency_ns)?;
            d.set_item("latency_ms", span.latency_ns as f64 / 1_000_000.0)?;
            result.push(d.unbind());
        }
        Ok(result)
    }

    /// Number of spans currently buffered.
    #[getter]
    pub fn buffered(&self) -> usize {
        self.buffer.len()
    }

    /// Maximum ring buffer capacity.
    #[getter]
    pub fn capacity(&self) -> usize {
        RING_BUFFER_CAPACITY
    }

    pub fn increment_serialization_time(&self, amt: u64) {
        VORTE_SERIALIZATION_TIME_NS.fetch_add(amt, Ordering::Relaxed);
    }
    pub fn increment_database_wait_time(&self, amt: u64) {
        VORTE_DATABASE_WAIT_TIME_NS.fetch_add(amt, Ordering::Relaxed);
    }
    pub fn increment_scheduling_latency(&self, amt: u64) {
        VORTE_SCHEDULING_LATENCY_NS.fetch_add(amt, Ordering::Relaxed);
    }
    pub fn increment_event_loop_lag(&self, amt: u64) {
        VORTE_EVENT_LOOP_LAG_NS.fetch_add(amt, Ordering::Relaxed);
    }

    #[getter]
    pub fn serialization_time_ns(&self) -> u64 {
        VORTE_SERIALIZATION_TIME_NS.load(Ordering::Relaxed)
    }
    #[getter]
    pub fn database_wait_time_ns(&self) -> u64 {
        VORTE_DATABASE_WAIT_TIME_NS.load(Ordering::Relaxed)
    }
    #[getter]
    pub fn scheduling_latency_ns(&self) -> u64 {
        VORTE_SCHEDULING_LATENCY_NS.load(Ordering::Relaxed)
    }
    #[getter]
    pub fn event_loop_lag_ns(&self) -> u64 {
        VORTE_EVENT_LOOP_LAG_NS.load(Ordering::Relaxed)
    }
}
