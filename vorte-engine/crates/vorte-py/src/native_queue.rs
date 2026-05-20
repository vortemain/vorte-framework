use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use vorte_queue::{
    BackpressureState, EnqueueResult, JobPriority, QueueConfig, QueueEngine, QueueJob, QueueStats,
};

// ------------------------------------------------------------------
// NativeQueue — PyO3 wrapper around QueueEngine
// ------------------------------------------------------------------

/// High-performance, channel-backed priority queue exposed to Python.
///
/// Constructor parameters
/// ----------------------
/// capacity     : usize  — hard channel capacity per queue (default 65 536)
/// hwm_ratio    : f64    — high-watermark fraction of capacity  (default 0.80)
/// lwm_ratio    : f64    — low-watermark  fraction of capacity  (default 0.20)
/// dlq_retention: usize  — max entries in each dead-letter queue (default 5 000)
/// dlq_ttl_secs : u64    — seconds before DLQ entries expire; 0 = no TTL
#[pyclass]
pub struct NativeQueue {
    engine: std::sync::Mutex<QueueEngine>,
}

#[pymethods]
impl NativeQueue {
    #[new]
    #[pyo3(signature = (
        capacity      = 65_536,
        hwm_ratio     = 0.80,
        lwm_ratio     = 0.20,
        dlq_retention = 5_000,
        dlq_ttl_secs  = 0,
    ))]
    fn new(
        capacity:      usize,
        hwm_ratio:     f64,
        lwm_ratio:     f64,
        dlq_retention: usize,
        dlq_ttl_secs:  u64,
    ) -> Self {
        let cfg = QueueConfig::new(capacity, hwm_ratio, lwm_ratio, dlq_retention)
            .with_dlq_ttl(dlq_ttl_secs);
        NativeQueue {
            engine: std::sync::Mutex::new(QueueEngine::with_config(cfg)),
        }
    }

    // ------------------------------------------------------------------
    // enqueue — returns {"status": "ok"|"backpressure"|"full", "id": str}
    // ------------------------------------------------------------------

    #[pyo3(signature = (
        id,
        queue,
        job_class,
        payload      = b"".to_vec(),
        priority     = "normal",
        max_attempts = 3u32,
        retry_delay  = 30.0,
        run_at       = None,
        trace_id     = None,
        attempts     = 0u32,
    ))]
    fn enqueue(
        &self,
        py:          Python,
        id:          String,
        queue:       String,
        job_class:   String,
        payload:     Vec<u8>,
        priority:    &str,
        max_attempts: u32,
        retry_delay: f64,
        run_at:      Option<f64>,
        trace_id:    Option<String>,
        attempts:    u32,
    ) -> PyResult<Py<PyDict>> {
        let job_priority = parse_job_priority(priority)?;

        let mut job        = QueueJob::new(id, queue, job_class);
        job.payload        = payload;
        job.priority       = job_priority;
        job.max_attempts   = max_attempts;
        job.retry_delay    = retry_delay;
        job.run_at         = run_at;
        job.trace_id       = trace_id;
        job.attempts       = attempts;

        let mut engine = lock(&self.engine)?;
        let result     = engine.enqueue(job);

        let d = PyDict::new_bound(py);
        match result {
            EnqueueResult::Ok(job_id) => {
                d.set_item("status", "ok")?;
                d.set_item("id", job_id)?;
            }
            EnqueueResult::Backpressure(job_id) => {
                d.set_item("status", "backpressure")?;
                d.set_item("id", job_id)?;
            }
            EnqueueResult::Full(job_id) => {
                d.set_item("status", "full")?;
                d.set_item("id", job_id)?;
            }
        }
        Ok(d.unbind())
    }

    // ------------------------------------------------------------------
    // dequeue — batch, returns list of dicts
    // ------------------------------------------------------------------

    #[pyo3(signature = (queues, count = 1))]
    fn dequeue(&self, queues: Vec<String>, count: usize) -> PyResult<Vec<Py<PyDict>>> {
        let mut engine = lock(&self.engine)?;
        let jobs       = engine.dequeue(&queues, count);

        Python::with_gil(|py| {
            let mut results = Vec::with_capacity(jobs.len());
            for job in &jobs {
                results.push(job_to_dict(py, job)?);
            }
            Ok(results)
        })
    }

    // ------------------------------------------------------------------
    // complete / fail
    // ------------------------------------------------------------------

    fn complete(&self, job_id: &str) -> PyResult<bool> {
        Ok(lock(&self.engine)?.complete(job_id).is_some())
    }

    #[pyo3(signature = (job_id, error))]
    fn fail(&self, job_id: &str, error: &str) -> PyResult<bool> {
        Ok(lock(&self.engine)?.fail(job_id, error).is_some())
    }

    // ------------------------------------------------------------------
    // Dead-letter management
    // ------------------------------------------------------------------

    fn retry_dead_letter(&self, job_id: &str) -> PyResult<bool> {
        Ok(lock(&self.engine)?.retry_dead_letter(job_id).is_some())
    }

    #[pyo3(signature = (queue_name = "default", limit = 50))]
    fn get_dead_letter(
        &self,
        py:         Python,
        queue_name: &str,
        limit:      usize,
    ) -> PyResult<Vec<Py<PyDict>>> {
        let engine = lock(&self.engine)?;
        let jobs   = engine.get_dead_letter(queue_name, limit);
        let mut results = Vec::with_capacity(jobs.len());
        for job in jobs {
            results.push(job_to_dict(py, job)?);
        }
        Ok(results)
    }

    /// Prune TTL-expired entries from all dead-letter queues.
    /// Returns the number of entries removed.
    fn purge_expired_dlq(&self) -> PyResult<usize> {
        Ok(lock(&self.engine)?.purge_expired_dlq())
    }

    // ------------------------------------------------------------------
    // Backpressure / watermark
    // ------------------------------------------------------------------

    /// Returns {"queue_name": "normal"|"high"|"full", …} for every known queue.
    fn watermark_state(&self, py: Python) -> PyResult<Py<PyDict>> {
        let engine = lock(&self.engine)?;
        let states = engine.watermark_states();
        let d      = PyDict::new_bound(py);
        for (name, state) in states {
            d.set_item(name, state)?;
        }
        Ok(d.unbind())
    }

    /// Returns the backpressure state for a single queue.
    #[pyo3(signature = (queue_name = "default"))]
    fn queue_backpressure(&self, queue_name: &str) -> PyResult<&'static str> {
        let engine = lock(&self.engine)?;
        Ok(engine.backpressure_state(queue_name).as_str())
    }

    // ------------------------------------------------------------------
    // Stats / size
    // ------------------------------------------------------------------

    fn stats(&self, py: Python) -> PyResult<Py<PyDict>> {
        let engine = lock(&self.engine)?;
        Ok(stats_to_dict(py, &engine.stats()))
    }

    #[pyo3(signature = (queue_name = "default"))]
    fn size(&self, queue_name: &str) -> PyResult<usize> {
        Ok(lock(&self.engine)?.queue_size(queue_name))
    }

    fn promote_scheduled(&self) -> PyResult<usize> {
        Ok(lock(&self.engine)?.promote_scheduled())
    }

    // ------------------------------------------------------------------
    // Per-queue config override (call before first enqueue to that queue)
    // ------------------------------------------------------------------

    #[pyo3(signature = (
        queue_name,
        capacity,
        hwm_ratio     = 0.80,
        lwm_ratio     = 0.20,
        dlq_retention = 5_000,
        dlq_ttl_secs  = 0,
    ))]
    fn configure_queue(
        &self,
        queue_name:    &str,
        capacity:      usize,
        hwm_ratio:     f64,
        lwm_ratio:     f64,
        dlq_retention: usize,
        dlq_ttl_secs:  u64,
    ) -> PyResult<()> {
        let cfg = QueueConfig::new(capacity, hwm_ratio, lwm_ratio, dlq_retention)
            .with_dlq_ttl(dlq_ttl_secs);
        lock(&self.engine)?.configure_queue(queue_name, cfg);
        Ok(())
    }
}

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

fn lock(m: &std::sync::Mutex<QueueEngine>) -> PyResult<std::sync::MutexGuard<'_, QueueEngine>> {
    m.lock()
        .map_err(|e| PyRuntimeError::new_err(format!("Queue lock poisoned: {}", e)))
}

fn parse_job_priority(priority: &str) -> PyResult<JobPriority> {
    match priority.to_lowercase().as_str() {
        "low"      => Ok(JobPriority::Low),
        "normal"   => Ok(JobPriority::Normal),
        "high"     => Ok(JobPriority::High),
        "critical" => Ok(JobPriority::Critical),
        _ => Err(PyRuntimeError::new_err(format!(
            "Invalid priority '{}'. Use: low, normal, high, critical",
            priority
        ))),
    }
}

fn job_to_dict(py: Python, job: &QueueJob) -> PyResult<Py<PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("id",          &job.id)?;
    d.set_item("queue",       &job.queue)?;
    d.set_item("job_class",   &job.job_class)?;
    d.set_item("payload",     pyo3::types::PyBytes::new_bound(py, &job.payload))?;
    d.set_item("priority",    job.priority as u8)?;
    d.set_item("status",      format!("{:?}", job.status).to_lowercase())?;
    d.set_item("attempts",    job.attempts)?;
    d.set_item("max_attempts", job.max_attempts)?;
    d.set_item("retry_delay", job.retry_delay)?;
    d.set_item("scheduled_at", job.scheduled_at)?;
    if let Some(v) = job.run_at       { d.set_item("run_at",       v)?; }
    if let Some(v) = job.started_at   { d.set_item("started_at",   v)?; }
    if let Some(v) = job.completed_at { d.set_item("completed_at", v)?; }
    if let Some(v) = job.failed_at    { d.set_item("failed_at",    v)?; }
    if let Some(ref s) = job.error    { d.set_item("error",        s)?; }
    if let Some(ref s) = job.trace_id { d.set_item("trace_id",     s)?; }
    Ok(d.unbind())
}

fn stats_to_dict(py: Python, s: &QueueStats) -> Py<PyDict> {
    let d          = PyDict::new_bound(py);
    let queues_d   = PyDict::new_bound(py);
    let wm_d       = PyDict::new_bound(py);

    for (k, v)   in &s.queues     { queues_d.set_item(k, v).ok(); }
    for (k, v)   in &s.watermarks { wm_d.set_item(k, v).ok(); }

    d.set_item("queues",      queues_d).ok();
    d.set_item("processing",  s.processing).ok();
    d.set_item("dead_letter", s.dead_letter).ok();
    d.set_item("completed",   s.completed).ok();
    d.set_item("scheduled",   s.scheduled).ok();
    d.set_item("watermarks",  wm_d).ok();
    d.unbind()
}
