use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use serde::Serialize;
use prost::Message;
use prost_types::value::Kind;
use prost_types::{ListValue, Struct, Value};

pub const FORMAT_JSON: u8 = 0;
pub const FORMAT_MSGPACK: u8 = 1;
pub const FORMAT_CBOR: u8 = 2;
pub const FORMAT_PROTOBUF: u8 = 3;

const POOL_CAPACITY: usize = 256;
const INITIAL_BUFFER_SIZE: usize = 4096;

const BUCKET_SIZES: [usize; 4] = [4096, 16384, 65536, 262144];

fn bucket_idx(capacity: usize) -> usize {
    if capacity <= 4096 {
        0
    } else if capacity <= 16384 {
        1
    } else if capacity <= 65536 {
        2
    } else {
        3
    }
}

pub struct BufferPool {
    buckets: [Mutex<VecDeque<Vec<u8>>>; 4],
}

impl BufferPool {
    pub fn new() -> Self {
        Self {
            buckets: [
                Mutex::new(VecDeque::with_capacity(64)),
                Mutex::new(VecDeque::with_capacity(64)),
                Mutex::new(VecDeque::with_capacity(64)),
                Mutex::new(VecDeque::with_capacity(64)),
            ],
        }
    }

    pub fn acquire(self: &Arc<Self>) -> PooledBuffer {
        self.acquire_with_capacity(4096)
    }

    pub fn acquire_with_capacity(self: &Arc<Self>, capacity: usize) -> PooledBuffer {
        let idx = bucket_idx(capacity);
        let buf = self.buckets[idx]
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .pop_front()
            .unwrap_or_else(|| Vec::with_capacity(BUCKET_SIZES[idx]));
        PooledBuffer {
            buf,
            bucket_idx: idx,
            pool: Some(self.clone()),
        }
    }

    pub fn release(&self, mut buf: Vec<u8>, bucket_idx: usize) {
        buf.clear();
        if let Ok(mut bucket) = self.buckets[bucket_idx].lock() {
            if bucket.len() < 64 {
                bucket.push_back(buf);
            }
        }
    }

    pub fn pool_size(&self) -> usize {
        let mut total = 0;
        for bucket in &self.buckets {
            if let Ok(b) = bucket.lock() {
                total += b.len();
            }
        }
        total
    }
}

impl Default for BufferPool {
    fn default() -> Self {
        Self::new()
    }
}

pub struct PooledBuffer {
    pub buf: Vec<u8>,
    pub bucket_idx: usize,
    pool: Option<Arc<BufferPool>>,
}

impl std::ops::Deref for PooledBuffer {
    type Target = Vec<u8>;
    fn deref(&self) -> &Self::Target {
        &self.buf
    }
}

impl std::ops::DerefMut for PooledBuffer {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.buf
    }
}

impl Drop for PooledBuffer {
    fn drop(&mut self) {
        if let Some(pool) = self.pool.take() {
            let buf = std::mem::take(&mut self.buf);
            pool.release(buf, self.bucket_idx);
        }
    }
}

#[derive(Clone)]
pub struct SerdeEngine {
    pool: Arc<BufferPool>,
}

impl SerdeEngine {
    pub fn new() -> Self {
        Self {
            pool: Arc::new(BufferPool::new()),
        }
    }

    /// Acquire a pooled buffer directly. Callers that implement their own
    /// serialization (e.g., via serde::Serialize on Python objects) can use
    /// this to bypass the intermediate serde_json::Value allocation.
    pub fn pool_acquire(&self) -> PooledBuffer {
        self.pool.acquire()
    }

    pub fn pool_acquire_with_capacity(&self, capacity: usize) -> PooledBuffer {
        self.pool.acquire_with_capacity(capacity)
    }


    pub fn serialize_json(&self, value: &serde_json::Value) -> PooledBuffer {
        let mut pooled = self.pool.acquire();
        {
            let writer = std::io::BufWriter::new(&mut *pooled);
            let mut serializer = serde_json::Serializer::new(writer);
            if let Err(e) = value.serialize(&mut serializer) {
                tracing::error!("JSON serialization error: {}", e);
            }
        }
        pooled
    }

    pub fn deserialize_json(&self, data: &[u8]) -> Option<serde_json::Value> {
        serde_json::from_slice(data).ok()
    }

    pub fn serialize_msgpack(&self, value: &serde_json::Value) -> PooledBuffer {
        let mut pooled = self.pool.acquire();
        {
            let mut serializer = rmp_serde::encode::Serializer::new(&mut *pooled);
            if let Err(e) = value.serialize(&mut serializer) {
                tracing::error!("MessagePack serialization error: {}", e);
            }
        }
        pooled
    }

    pub fn deserialize_msgpack(&self, data: &[u8]) -> Option<serde_json::Value> {
        rmp_serde::from_slice(data).ok()
    }

    pub fn serialize_cbor(&self, value: &serde_json::Value) -> PooledBuffer {
        let mut pooled = self.pool.acquire();
        {
            if let Err(e) = ciborium::into_writer(value, &mut *pooled) {
                tracing::error!("CBOR serialization error: {}", e);
            }
        }
        pooled
    }

    pub fn deserialize_cbor(&self, data: &[u8]) -> Option<serde_json::Value> {
        ciborium::from_reader(data).ok()
    }

    pub fn serialize_protobuf(&self, value: &serde_json::Value) -> PooledBuffer {
        let mut pooled = self.pool.acquire();
        let prost_val = json_to_prost(value);
        if let Err(e) = prost_val.encode(&mut *pooled) {
            tracing::error!("Protobuf serialization error: {}", e);
        }
        pooled
    }

    pub fn deserialize_protobuf(&self, data: &[u8]) -> Option<serde_json::Value> {
        let prost_val = Value::decode(data).ok()?;
        Some(prost_to_json(&prost_val))
    }

    pub fn serialize(&self, value: &serde_json::Value, format: u8) -> PooledBuffer {
        match format {
            FORMAT_JSON => self.serialize_json(value),
            FORMAT_MSGPACK => self.serialize_msgpack(value),
            FORMAT_CBOR => self.serialize_cbor(value),
            FORMAT_PROTOBUF => self.serialize_protobuf(value),
            _ => self.serialize_json(value),
        }
    }

    pub fn deserialize(&self, data: &[u8], format: u8) -> Option<serde_json::Value> {
        match format {
            FORMAT_JSON => self.deserialize_json(data),
            FORMAT_MSGPACK => self.deserialize_msgpack(data),
            FORMAT_CBOR => self.deserialize_cbor(data),
            FORMAT_PROTOBUF => self.deserialize_protobuf(data),
            _ => self.deserialize_json(data),
        }
    }

    pub fn pool_size(&self) -> usize {
        self.pool.pool_size()
    }
}

impl Default for SerdeEngine {
    fn default() -> Self {
        Self::new()
    }
}

fn json_to_prost(val: &serde_json::Value) -> Value {
    let kind = match val {
        serde_json::Value::Null => Kind::NullValue(0),
        serde_json::Value::Bool(b) => Kind::BoolValue(*b),
        serde_json::Value::Number(n) => Kind::NumberValue(n.as_f64().unwrap_or(0.0)),
        serde_json::Value::String(s) => Kind::StringValue(s.clone()),
        serde_json::Value::Array(arr) => Kind::ListValue(ListValue {
            values: arr.iter().map(json_to_prost).collect(),
        }),
        serde_json::Value::Object(obj) => Kind::StructValue(Struct {
            fields: obj.iter().map(|(k, v)| (k.clone(), json_to_prost(v))).collect(),
        }),
    };
    Value { kind: Some(kind) }
}

fn prost_to_json(val: &Value) -> serde_json::Value {
    match &val.kind {
        Some(Kind::NullValue(_)) | None => serde_json::Value::Null,
        Some(Kind::BoolValue(b)) => serde_json::Value::Bool(*b),
        Some(Kind::NumberValue(n)) => serde_json::Number::from_f64(*n)
            .map(serde_json::Value::Number)
            .unwrap_or(serde_json::Value::Null),
        Some(Kind::StringValue(s)) => serde_json::Value::String(s.clone()),
        Some(Kind::ListValue(l)) => serde_json::Value::Array(
            l.values.iter().map(prost_to_json).collect()
        ),
        Some(Kind::StructValue(s)) => serde_json::Value::Object(
            s.fields.iter().map(|(k, v)| (k.clone(), prost_to_json(v))).collect()
        ),
    }
}
