use pyo3::prelude::*;

mod bridge;
mod engine;
mod executor;
mod graph;
mod handler;
mod metrics;
mod native_queue;
mod native_serde;
mod scheduler;

use crate::engine::VorteEngine;
use crate::executor::RustExecutor;
use crate::metrics::MetricsCollector;
use crate::native_queue::NativeQueue;
use crate::native_serde::{NativeSerde, VorteBuffer};
use crate::scheduler::{TaskScheduler, PyCancellationToken};
use crate::graph::PyExecutionGraph;

#[pyfunction]
#[pyo3(signature = (py_debug = false))]
fn init_logging(py_debug: bool) -> PyResult<()> {
    let filter = if py_debug {
        "vorte_core=debug,vorte_router=debug,vorte_py=debug,info"
    } else {
        "vorte_core=warn,vorte_router=warn,vorte_py=warn,error"
    };

    let _ = tracing_subscriber::fmt()
        .with_env_filter(filter)
        .try_init();
    Ok(())
}

#[pymodule]
fn _vorte_engine(m: &Bound<'_, pyo3::types::PyModule>) -> PyResult<()> {
    m.add_class::<VorteEngine>()?;
    m.add_class::<MetricsCollector>()?;
    m.add_class::<RustExecutor>()?;
    m.add_class::<TaskScheduler>()?;
    m.add_class::<PyCancellationToken>()?;
    m.add_class::<NativeQueue>()?;
    m.add_class::<NativeSerde>()?;
    m.add_class::<VorteBuffer>()?;
    m.add_class::<PyExecutionGraph>()?;
    m.add_function(wrap_pyfunction!(init_logging, m)?)?;
    Ok(())
}
