use pyo3::prelude::*;
use pyo3::types::PyDict;

#[pyclass]
pub struct RustExecutor {
    runtime: tokio::runtime::Runtime,
}

#[pymethods]
impl RustExecutor {
    #[new]
    #[pyo3(signature = (max_workers=None))]
    fn new(max_workers: Option<usize>) -> PyResult<Self> {
        let workers = max_workers.unwrap_or_else(|| {
            std::thread::available_parallelism()
                .map(|n| n.get() * 4)
                .unwrap_or(16)
        });

        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(workers)
            .enable_all()
            .build()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(format!("Failed to create Tokio runtime: {}", e)))?;

        Ok(RustExecutor { runtime })
    }

    #[pyo3(signature = (func, args, kwargs=None))]
    fn run(
        &self,
        py: Python,
        func: PyObject,
        args: PyObject,
        kwargs: Option<PyObject>,
    ) -> PyResult<PyObject> {
        py.allow_threads(|| {
            self.runtime.block_on(async {
                Python::with_gil(|py| {
                    let args_tuple = args.downcast_bound::<pyo3::types::PyTuple>(py)?;
                    let kwargs_dict = match kwargs {
                        Some(ref kw) => Some(kw.downcast_bound::<PyDict>(py)?),
                        None => None,
                    };

                    let result = if let Some(kw) = kwargs_dict {
                        func.call_bound(py, args_tuple, Some(kw))?
                    } else {
                        func.call_bound(py, args_tuple, None)?
                    };

                    Ok(result)
                })
            })
        })
    }
}
