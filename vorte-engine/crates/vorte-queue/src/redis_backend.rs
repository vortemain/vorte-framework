/// Redis STREAM backend for `vorte-queue`.
///
/// Requires the `redis-backend` feature flag.
///
/// Each named Vorte queue maps to a Redis Stream at key `{stream_prefix}{queue}`.
/// Dead-letter items are stored in `{dlq_prefix}{queue}` (also a Stream).
///
/// Job serialisation: `serde_json` → stored as a single `payload` field in the
/// STREAM entry so the full `QueueJob` is recoverable by any consumer.
///
/// Consumer groups: every `RedisBackend` instance belongs to a shared consumer
/// group (`consumer_group`) so multiple workers can compete for jobs without
/// double-processing.
#[cfg(feature = "redis-backend")]
pub mod redis_backend {
    use std::collections::HashMap;
    use std::time::Duration;

    use redis::aio::MultiplexedConnection;
    use redis::{AsyncCommands, Client, RedisError, RedisResult, Value};
    use tracing::{debug, error, warn};

    use crate::job::{now_secs, JobStatus, QueueJob};

    // ------------------------------------------------------------------
    // Configuration
    // ------------------------------------------------------------------

    /// Configuration for the Redis STREAM backend.
    #[derive(Clone, Debug)]
    pub struct RedisConfig {
        /// Redis URL, e.g. `"redis://127.0.0.1:6379"`.
        pub url: String,
        /// Key prefix for job streams, e.g. `"vorte:queue:"`.
        pub stream_prefix: String,
        /// Key prefix for dead-letter streams, e.g. `"vorte:dlq:"`.
        pub dlq_prefix: String,
        /// Consumer group name shared across all workers.
        pub consumer_group: String,
        /// Unique consumer name (should be distinct per process/thread).
        pub consumer_name: String,
        /// Max entries retained in DLQ streams (`MAXLEN` on XADD).
        pub dlq_maxlen: usize,
        /// How long `XREADGROUP` blocks waiting for new entries (ms).
        pub block_ms: u64,
    }

    impl RedisConfig {
        pub fn new(url: impl Into<String>) -> Self {
            Self {
                url:            url.into(),
                stream_prefix:  "vorte:queue:".into(),
                dlq_prefix:     "vorte:dlq:".into(),
                consumer_group: "vorte-workers".into(),
                consumer_name:  format!("worker-{}", std::process::id()),
                dlq_maxlen:     5_000,
                block_ms:       2_000,
            }
        }
    }

    // ------------------------------------------------------------------
    // RedisBackend
    // ------------------------------------------------------------------

    /// Async Redis STREAM queue backend.
    pub struct RedisBackend {
        client: Client,
        cfg:    RedisConfig,
    }

    impl RedisBackend {
        pub async fn new(cfg: RedisConfig) -> RedisResult<Self> {
            let client = Client::open(cfg.url.clone())?;
            // Verify connectivity
            let mut conn = client.get_multiplexed_async_connection().await?;
            let _: String = redis::cmd("PING").query_async(&mut conn).await?;
            Ok(Self { client, cfg })
        }

        async fn conn(&self) -> RedisResult<MultiplexedConnection> {
            self.client.get_multiplexed_async_connection().await
        }

        fn stream_key(&self, queue: &str) -> String {
            format!("{}{}", self.cfg.stream_prefix, queue)
        }

        fn dlq_key(&self, queue: &str) -> String {
            format!("{}{}", self.cfg.dlq_prefix, queue)
        }

        /// Ensure the consumer group exists for a stream (MKSTREAM creates it if missing).
        async fn ensure_group(&self, conn: &mut MultiplexedConnection, stream: &str) -> RedisResult<()> {
            let result: RedisResult<()> = redis::cmd("XGROUP")
                .arg("CREATE")
                .arg(stream)
                .arg(&self.cfg.consumer_group)
                .arg("0")
                .arg("MKSTREAM")
                .query_async(conn)
                .await;
            match result {
                Ok(_) => Ok(()),
                Err(e) if e.to_string().contains("BUSYGROUP") => Ok(()), // group already exists
                Err(e) => Err(e),
            }
        }

        // ------------------------------------------------------------------
        // Enqueue
        // ------------------------------------------------------------------

        /// Append a job to the Redis Stream for its queue.
        pub async fn enqueue(&self, job: &QueueJob) -> RedisResult<String> {
            let key = self.stream_key(&job.queue);
            let payload = serde_json::to_string(job)
                .map_err(|e| RedisError::from((redis::ErrorKind::IoError, "serialize", e.to_string())))?;

            let mut conn = self.conn().await?;
            self.ensure_group(&mut conn, &key).await?;

            let entry_id: String = conn
                .xadd(&key, "*", &[("payload", &payload)])
                .await?;

            debug!(queue = %job.queue, job_id = %job.id, entry_id = %entry_id, "Job enqueued to Redis STREAM");
            Ok(entry_id)
        }

        // ------------------------------------------------------------------
        // Batch dequeue (XREADGROUP)
        // ------------------------------------------------------------------

        /// Read up to `count` jobs from the given queues using XREADGROUP.
        /// Returns a Vec of `(stream_entry_id, QueueJob)` pairs.
        /// The caller must `ack` each entry after processing.
        pub async fn dequeue(
            &self,
            queue_names: &[&str],
            count: usize,
        ) -> RedisResult<Vec<(String, QueueJob)>> {
            if queue_names.is_empty() {
                return Ok(vec![]);
            }

            let mut conn = self.conn().await?;

            // Ensure groups exist for all queues
            for q in queue_names {
                let key = self.stream_key(q);
                self.ensure_group(&mut conn, &key).await?;
            }

            let stream_keys: Vec<String> = queue_names.iter().map(|q| self.stream_key(q)).collect();
            let ids: Vec<&str> = vec![">"; queue_names.len()];

            // XREADGROUP GROUP <group> <consumer> COUNT <n> BLOCK <ms> STREAMS <keys…> <ids…>
            let reply: Value = redis::cmd("XREADGROUP")
                .arg("GROUP")
                .arg(&self.cfg.consumer_group)
                .arg(&self.cfg.consumer_name)
                .arg("COUNT")
                .arg(count)
                .arg("BLOCK")
                .arg(0u64) // non-blocking; Python scheduler drives the poll loop
                .arg("STREAMS")
                .arg(&stream_keys)
                .arg(&ids)
                .query_async(&mut conn)
                .await
                .unwrap_or(Value::Nil);

            let mut results = Vec::new();
            if let Value::Array(stream_blocks) = reply {
                for block in stream_blocks {
                    if let Value::Array(ref parts) = block {
                        if parts.len() < 2 {
                            continue;
                        }
                        if let Value::Array(ref entries) = parts[1] {
                            for entry in entries {
                                if let Value::Array(ref entry_parts) = entry {
                                    if entry_parts.len() < 2 {
                                        continue;
                                    }
                                    let entry_id = match &entry_parts[0] {
                                        Value::BulkString(b) => {
                                            String::from_utf8_lossy(b).to_string()
                                        }
                                        Value::SimpleString(s) => s.clone(),
                                        _ => continue,
                                    };
                                    // Fields are key-value pairs in a flat array
                                    if let Value::Array(ref fields) = entry_parts[1] {
                                        let payload_val = fields.chunks(2).find_map(|pair| {
                                            if let [Value::BulkString(k), Value::BulkString(v)] = pair {
                                                if k == b"payload" {
                                                    return Some(v.clone());
                                                }
                                            }
                                            None
                                        });
                                        if let Some(bytes) = payload_val {
                                            match serde_json::from_slice::<QueueJob>(&bytes) {
                                                Ok(mut job) => {
                                                    job.status = JobStatus::Running;
                                                    job.started_at = Some(now_secs());
                                                    results.push((entry_id, job));
                                                }
                                                Err(e) => {
                                                    error!("Failed to deserialize job: {}", e);
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Ok(results)
        }

        // ------------------------------------------------------------------
        // Ack / Move to DLQ
        // ------------------------------------------------------------------

        /// Acknowledge a successfully processed entry (XACK + XDEL).
        pub async fn ack(&self, queue: &str, entry_id: &str) -> RedisResult<()> {
            let key = self.stream_key(queue);
            let mut conn = self.conn().await?;
            let _: i64 = conn.xack(&key, &self.cfg.consumer_group, &[entry_id]).await?;
            let _: i64 = conn.xdel(&key, &[entry_id]).await?;
            Ok(())
        }

        /// Move a failed job to the dead-letter stream (XADD with MAXLEN).
        pub async fn move_to_dlq(&self, job: &QueueJob) -> RedisResult<()> {
            let key = self.dlq_key(&job.queue);
            let payload = serde_json::to_string(job)
                .map_err(|e| RedisError::from((redis::ErrorKind::IoError, "serialize", e.to_string())))?;

            let mut conn = self.conn().await?;
            let _: String = redis::cmd("XADD")
                .arg(&key)
                .arg("MAXLEN")
                .arg("~")
                .arg(self.cfg.dlq_maxlen)
                .arg("*")
                .arg("payload")
                .arg(&payload)
                .query_async(&mut conn)
                .await?;

            warn!(queue = %job.queue, job_id = %job.id, "Job moved to dead-letter stream");
            Ok(())
        }

        // ------------------------------------------------------------------
        // DLQ inspection / retry
        // ------------------------------------------------------------------

        /// Fetch the last `limit` entries from the dead-letter stream.
        pub async fn get_dead_letter(&self, queue: &str, limit: usize) -> RedisResult<Vec<QueueJob>> {
            let key = self.dlq_key(queue);
            let mut conn = self.conn().await?;
            let reply: Value = redis::cmd("XREVRANGE")
                .arg(&key)
                .arg("+")
                .arg("-")
                .arg("COUNT")
                .arg(limit)
                .query_async(&mut conn)
                .await
                .unwrap_or(Value::Nil);

            let mut jobs = Vec::new();
            if let Value::Array(entries) = reply {
                for entry in entries {
                    if let Value::Array(ref parts) = entry {
                        if let Some(Value::Array(ref fields)) = parts.get(1) {
                            let payload = fields.chunks(2).find_map(|pair| {
                                if let [Value::BulkString(k), Value::BulkString(v)] = pair {
                                    if k == b"payload" {
                                        return Some(v.clone());
                                    }
                                }
                                None
                            });
                            if let Some(bytes) = payload {
                                if let Ok(job) = serde_json::from_slice::<QueueJob>(&bytes) {
                                    jobs.push(job);
                                }
                            }
                        }
                    }
                }
            }
            Ok(jobs)
        }

        /// Re-enqueue a DLQ job back to its original queue.
        pub async fn retry_dead_letter(&self, queue: &str, job_id: &str) -> RedisResult<bool> {
            let jobs = self.get_dead_letter(queue, 1_000).await?;
            if let Some(mut job) = jobs.into_iter().find(|j| j.id == job_id) {
                job.status = JobStatus::Pending;
                job.attempts = 0;
                job.error = None;
                job.run_at = None;
                self.enqueue(&job).await?;

                // Remove from DLQ — XREVRANGE doesn't give us the entry_id directly,
                // so we scan with XRANGE to find the matching entry.
                let dlq_key = self.dlq_key(queue);
                let mut conn = self.conn().await?;
                let scan: Value = redis::cmd("XRANGE")
                    .arg(&dlq_key)
                    .arg("-")
                    .arg("+")
                    .query_async(&mut conn)
                    .await
                    .unwrap_or(Value::Nil);

                if let Value::Array(entries) = scan {
                    for entry in entries {
                        if let Value::Array(ref parts) = entry {
                            if let (Some(eid), Some(Value::Array(fields))) =
                                (parts.get(0), parts.get(1))
                            {
                                let is_match = fields.chunks(2).any(|pair| {
                                    if let [Value::BulkString(k), Value::BulkString(v)] = pair {
                                        if k == b"payload" {
                                            if let Ok(j) = serde_json::from_slice::<QueueJob>(v) {
                                                return j.id == job_id;
                                            }
                                        }
                                    }
                                    false
                                });
                                if is_match {
                                    if let Value::BulkString(id_bytes) = eid {
                                        let id_str = String::from_utf8_lossy(id_bytes);
                                        let _: i64 = conn.xdel(&dlq_key, &[id_str.as_ref()]).await?;
                                    }
                                }
                            }
                        }
                    }
                }

                Ok(true)
            } else {
                Ok(false)
            }
        }

        // ------------------------------------------------------------------
        // Stats
        // ------------------------------------------------------------------

        pub async fn stream_len(&self, queue: &str) -> RedisResult<usize> {
            let key = self.stream_key(queue);
            let mut conn = self.conn().await?;
            let len: usize = conn.xlen(&key).await?;
            Ok(len)
        }
    }
}
