use pyo3::prelude::*;

mod bridge;
mod engine;
mod executor;
mod handler;
mod metrics;
mod native_queue;
mod native_serde;
mod scheduler;

use crate::engine::VorteEngine;
use crate::executor::RustExecutor;
use crate::metrics::MetricsCollector;
use crate::native_queue::NativeQueue;
use crate::native_serde::NativeSerde;
use crate::scheduler::TaskScheduler;

#[pymodule]
fn _vorte_engine(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    m.add_class::<VorteEngine>()?;
    m.add_class::<MetricsCollector>()?;
    m.add_class::<RustExecutor>()?;
    m.add_class::<TaskScheduler>()?;
    m.add_class::<NativeQueue>()?;
    m.add_class::<NativeSerde>()?;
    Ok(())
}
