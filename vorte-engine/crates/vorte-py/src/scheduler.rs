use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use vorte_scheduler::{SchedulerConfig, Task, TaskPriority, TaskScheduler as InnerScheduler};

#[pyclass]
pub struct TaskScheduler {
    inner: InnerScheduler,
}

#[pymethods]
impl TaskScheduler {
    #[new]
    #[pyo3(signature = (workers=None))]
    fn new(workers: Option<usize>) -> PyResult<Self> {
        let mut config = SchedulerConfig::default();
        if let Some(w) = workers {
            config.worker_threads = w;
        }
        Ok(TaskScheduler {
            inner: InnerScheduler::new(config),
        })
    }

    #[pyo3(signature = (func, args=None, kwargs=None, priority="normal"))]
    fn submit(
        &self,
        py: Python,
        func: PyObject,
        args: Option<PyObject>,
        kwargs: Option<PyObject>,
        priority: &str,
    ) -> PyResult<()> {
        let task_priority = parse_priority(priority)?;
        let args = args.unwrap_or_else(|| {
            pyo3::types::PyTuple::empty_bound(py).into_any().unbind()
        });
        let task = Task::new(Box::new(move || {
            let _ = Python::with_gil(|py| -> PyResult<()> {
                let args_tuple = args.downcast_bound::<pyo3::types::PyTuple>(py)?;
                let result = if let Some(ref kw) = kwargs {
                    let kw_dict = kw.downcast_bound::<PyDict>(py)?;
                    func.call_bound(py, args_tuple, Some(kw_dict))
                } else {
                    func.call_bound(py, args_tuple, None)
                };
                if let Err(e) = result {
                    tracing::error!("Scheduled task error: {}", e);
                }
                Ok(())
            });
        }))
        .with_priority(task_priority);

        self.inner.submit(task);
        Ok(())
    }

    fn stats(&self, py: Python) -> PyResult<Py<PyDict>> {
        let s = self.inner.stats();
        let dict = PyDict::new_bound(py);
        dict.set_item("tasks_submitted", s.tasks_submitted)?;
        dict.set_item("tasks_completed", s.tasks_completed)?;
        dict.set_item("tasks_failed", s.tasks_failed)?;
        dict.set_item("active_workers", s.active_workers)?;
        dict.set_item("queue_depth", s.queue_depth)?;
        dict.set_item("queue_depth_high", s.queue_depth_high)?;
        dict.set_item("queue_depth_normal", s.queue_depth_normal)?;
        dict.set_item("queue_depth_low", s.queue_depth_low)?;
        Ok(dict.unbind())
    }

    fn shutdown(&self) {
        self.inner.shutdown();
    }
}

fn parse_priority(priority: &str) -> PyResult<TaskPriority> {
    match priority.to_lowercase().as_str() {
        "low" => Ok(TaskPriority::Low),
        "normal" => Ok(TaskPriority::Normal),
        "high" => Ok(TaskPriority::High),
        "critical" => Ok(TaskPriority::Critical),
        _ => Err(PyRuntimeError::new_err(format!(
            "Invalid priority '{}'. Use: low, normal, high, critical",
            priority
        ))),
    }
}

#[pyclass]
#[derive(Clone)]
pub struct PyCancellationToken {
    pub cancelled: Arc<AtomicBool>,
}

#[pymethods]
impl PyCancellationToken {
    #[new]
    pub fn new() -> Self {
        PyCancellationToken {
            cancelled: Arc::new(AtomicBool::new(false)),
        }
    }
    pub fn cancel(&self) {
        self.cancelled.store(true, Ordering::SeqCst);
    }
    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::SeqCst)
    }
}
