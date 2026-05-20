use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBool, PyBytes, PyDict, PyList, PyString, PyTuple};

use vorte_serde::{SerdeEngine, FORMAT_CBOR, FORMAT_JSON, FORMAT_MSGPACK, FORMAT_PROTOBUF};

use serde::ser::{SerializeMap, SerializeSeq};
use serde::Serialize;

// ---------------------------------------------------------------------------
// Direct Python Object → Wire Format (single-pass, zero intermediate allocation)
// ---------------------------------------------------------------------------

/// A zero-copy wrapper that implements `serde::Serialize` for any Python object.
///
/// Instead of the two-step `py_to_value()` → encode path (which builds an
/// entire `serde_json::Value` tree in Rust memory before encoding), this
/// struct drives the serde visitor API directly from the Python object graph.
/// The result is a single traversal with no intermediate allocation — the same
/// approach used by `orjson` in C.
///
/// Supports: None, bool, i64, u64, f64, str, dict, list, tuple.
/// Unknown Python types are serialized as `null`.
struct PyObjectSerializer<'py>(Bound<'py, PyAny>);

impl<'py> Serialize for PyObjectSerializer<'py> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        let obj = &self.0;

        if obj.is_none() {
            return serializer.serialize_none();
        }

        // Bool must be checked BEFORE int since PyBool is a subtype of PyInt in CPython.
        if let Ok(b) = obj.downcast::<PyBool>() {
            return serializer.serialize_bool(b.is_true());
        }

        if let Ok(i) = obj.extract::<i64>() {
            return serializer.serialize_i64(i);
        }

        if let Ok(u) = obj.extract::<u64>() {
            return serializer.serialize_u64(u);
        }

        if let Ok(f) = obj.extract::<f64>() {
            return serializer.serialize_f64(f);
        }

        if let Ok(s) = obj.downcast::<PyString>() {
            return serializer.serialize_str(
                s.to_str()
                    .map_err(|e| serde::ser::Error::custom(e.to_string()))?,
            );
        }

        if let Ok(d) = obj.downcast::<PyDict>() {
            let mut map = serializer.serialize_map(Some(d.len()))?;
            for (k, v) in d.iter() {
                let key = k
                    .extract::<String>()
                    .map_err(|e| serde::ser::Error::custom(e.to_string()))?;
                map.serialize_entry(&key, &PyObjectSerializer(v))?;
            }
            return map.end();
        }

        if let Ok(l) = obj.downcast::<PyList>() {
            let mut seq = serializer.serialize_seq(Some(l.len()))?;
            for item in l.iter() {
                seq.serialize_element(&PyObjectSerializer(item))?;
            }
            return seq.end();
        }

        if let Ok(t) = obj.downcast::<PyTuple>() {
            let mut seq = serializer.serialize_seq(Some(t.len()))?;
            for item in t.iter() {
                seq.serialize_element(&PyObjectSerializer(item))?;
            }
            return seq.end();
        }

        // Unknown Python types fall back to null
        serializer.serialize_none()
    }
}

// ---------------------------------------------------------------------------
// NativeSerde Python class
// ---------------------------------------------------------------------------

#[pyclass]
pub struct NativeSerde {
    engine: SerdeEngine,
}

#[pymethods]
impl NativeSerde {
    #[new]
    fn new() -> Self {
        NativeSerde {
            engine: SerdeEngine::new(),
        }
    }

    /// Serialize a Python object to bytes in the given format.
    ///
    /// For JSON, Msgpack, and CBOR the serialization uses a single-pass
    /// `PyObjectSerializer` walk — no intermediate `serde_json::Value` tree
    /// is allocated, mirroring the approach used by orjson in C.
    ///
    /// Protobuf still uses an intermediate `prost_types::Value` because
    /// `prost::Message::encode` does not use the serde trait.
    #[pyo3(signature = (data, format="json"))]
    fn serialize(&self, py: Python, data: Bound<'_, PyAny>, format: &str) -> PyResult<Py<PyBytes>> {
        let fmt = parse_format(format);

        match fmt {
            FORMAT_JSON => {
                let mut pooled = self.engine.pool_acquire();
                {
                    // BufWriter over the pooled Vec<u8> — no heap allocation for the buffer.
                    let writer = std::io::BufWriter::new(&mut *pooled);
                    let mut ser = serde_json::Serializer::new(writer);
                    PyObjectSerializer(data)
                        .serialize(&mut ser)
                        .map_err(|e| {
                            PyRuntimeError::new_err(format!("JSON serialization error: {e}"))
                        })?;
                }
                let len = pooled.len();
                let py_bytes = PyBytes::new_bound_with(py, len, |slice| {
                    slice.copy_from_slice(&pooled);
                    Ok(())
                })?;
                Ok(py_bytes.unbind())
            }

            FORMAT_MSGPACK => {
                let mut pooled = self.engine.pool_acquire();
                {
                    let mut ser = rmp_serde::encode::Serializer::new(&mut *pooled);
                    PyObjectSerializer(data)
                        .serialize(&mut ser)
                        .map_err(|e| {
                            PyRuntimeError::new_err(format!("MessagePack serialization error: {e}"))
                        })?;
                }
                let len = pooled.len();
                let py_bytes = PyBytes::new_bound_with(py, len, |slice| {
                    slice.copy_from_slice(&pooled);
                    Ok(())
                })?;
                Ok(py_bytes.unbind())
            }

            FORMAT_CBOR => {
                let mut pooled = self.engine.pool_acquire();
                ciborium::into_writer(&PyObjectSerializer(data), &mut *pooled).map_err(|e| {
                    PyRuntimeError::new_err(format!("CBOR serialization error: {e}"))
                })?;
                let len = pooled.len();
                let py_bytes = PyBytes::new_bound_with(py, len, |slice| {
                    slice.copy_from_slice(&pooled);
                    Ok(())
                })?;
                Ok(py_bytes.unbind())
            }

            FORMAT_PROTOBUF | _ => {
                // Protobuf requires prost_types::Value mapping — intermediate step unavoidable.
                let value = py_to_value(&data)?;
                let pooled = self.engine.serialize_protobuf(&value);
                let len = pooled.len();
                let py_bytes = PyBytes::new_bound_with(py, len, |slice| {
                    slice.copy_from_slice(&pooled);
                    Ok(())
                })?;
                Ok(py_bytes.unbind())
            }
        }
    }

    /// Deserialize bytes back to a native Python object.
    ///
    /// The `value_to_py()` helper recursively builds `PyDict` / `PyList`
    /// structures directly from the parsed `serde_json::Value` tree without
    /// re-encoding to a JSON string first.
    #[pyo3(signature = (data, format="json"))]
    fn deserialize(&self, py: Python, data: &[u8], format: &str) -> PyResult<PyObject> {
        let fmt = parse_format(format);
        let value = self
            .engine
            .deserialize(data, fmt)
            .ok_or_else(|| PyRuntimeError::new_err("Deserialization failed"))?;

        value_to_py(py, &value)
    }

    /// Convenience: serialize a JSON byte string to Msgpack bytes.
    fn serialize_msgpack(&self, py: Python, data: &[u8]) -> PyResult<Py<PyBytes>> {
        let value: serde_json::Value = serde_json::from_slice(data)
            .map_err(|e| PyRuntimeError::new_err(format!("JSON parse error: {e}")))?;

        let pooled = self.engine.serialize_msgpack(&value);
        let len = pooled.len();
        let py_bytes = PyBytes::new_bound_with(py, len, |slice| {
            slice.copy_from_slice(&pooled);
            Ok(())
        })?;
        Ok(py_bytes.unbind())
    }

    /// Convenience: deserialize Msgpack bytes and return JSON bytes.
    fn deserialize_msgpack(&self, py: Python, data: &[u8]) -> PyResult<Py<PyBytes>> {
        let value = self
            .engine
            .deserialize_msgpack(data)
            .ok_or_else(|| PyRuntimeError::new_err("MessagePack deserialization failed"))?;

        let json_bytes =
            serde_json::to_vec(&value).map_err(|e| PyRuntimeError::new_err(format!("JSON encode error: {e}")))?;

        Ok(PyBytes::new_bound(py, &json_bytes).unbind())
    }

    #[getter]
    fn pool_size(&self) -> usize {
        self.engine.pool_size()
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn parse_format(format: &str) -> u8 {
    match format.to_lowercase().as_str() {
        "json" => FORMAT_JSON,
        "msgpack" | "messagepack" => FORMAT_MSGPACK,
        "cbor" => FORMAT_CBOR,
        "protobuf" | "proto" => FORMAT_PROTOBUF,
        _ => FORMAT_JSON,
    }
}

/// Slow path: recursively build a `serde_json::Value` from a Python object.
/// Used only for Protobuf (which requires `prost_types::Value` mapping) and
/// as a fallback for unknown formats.
fn py_to_value(obj: &Bound<'_, PyAny>) -> PyResult<serde_json::Value> {
    if obj.is_none() {
        Ok(serde_json::Value::Null)
    } else if let Ok(b) = obj.downcast::<PyBool>() {
        Ok(serde_json::Value::Bool(b.is_true()))
    } else if let Ok(i) = obj.extract::<i64>() {
        Ok(serde_json::Value::Number(i.into()))
    } else if let Ok(f) = obj.extract::<f64>() {
        if let Some(n) = serde_json::Number::from_f64(f) {
            Ok(serde_json::Value::Number(n))
        } else {
            Ok(serde_json::Value::Null)
        }
    } else if let Ok(s) = obj.downcast::<PyString>() {
        Ok(serde_json::Value::String(s.to_str()?.to_owned()))
    } else if let Ok(d) = obj.downcast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (k, v) in d.iter() {
            let k_str = k.extract::<String>()?;
            map.insert(k_str, py_to_value(&v)?);
        }
        Ok(serde_json::Value::Object(map))
    } else if let Ok(l) = obj.downcast::<PyList>() {
        let mut arr = Vec::new();
        for item in l.iter() {
            arr.push(py_to_value(&item)?);
        }
        Ok(serde_json::Value::Array(arr))
    } else if let Ok(t) = obj.downcast::<PyTuple>() {
        let mut arr = Vec::new();
        for item in t.iter() {
            arr.push(py_to_value(&item)?);
        }
        Ok(serde_json::Value::Array(arr))
    } else {
        Ok(serde_json::Value::Null)
    }
}

/// Recursively build native Python objects from a `serde_json::Value`,
/// constructing `PyDict` / `PyList` structures directly without re-encoding
/// to a JSON string. This avoids the overhead of `json.loads()` entirely.
fn value_to_py(py: Python<'_>, value: &serde_json::Value) -> PyResult<PyObject> {
    match value {
        serde_json::Value::Null => Ok(py.None()),
        serde_json::Value::Bool(b) => Ok(b.into_py(py)),
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_py(py))
            } else if let Some(u) = n.as_u64() {
                Ok(u.into_py(py))
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_py(py))
            } else {
                Ok(py.None())
            }
        }
        serde_json::Value::String(s) => Ok(s.into_py(py)),
        serde_json::Value::Array(arr) => {
            let py_list = pyo3::types::PyList::empty_bound(py);
            for val in arr {
                py_list.append(value_to_py(py, val)?)?;
            }
            Ok(py_list.into())
        }
        serde_json::Value::Object(obj) => {
            let py_dict = pyo3::types::PyDict::new_bound(py);
            for (k, v) in obj {
                py_dict.set_item(k, value_to_py(py, v)?)?;
            }
            Ok(py_dict.into())
        }
    }
}
