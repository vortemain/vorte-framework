/// Per-queue configuration: capacity, watermarks, dead-letter retention.
///
/// * `capacity`      — hard upper bound; crossbeam channel size.
/// * `high_watermark`— once `len >= hwm` every `enqueue` returns `Backpressure`.
/// * `low_watermark` — once `len <= lwm` the queue is considered "healthy" again.
/// * `dlq_retention` — max number of jobs kept in the dead-letter queue.
/// * `dlq_ttl_secs`  — seconds before a DLQ entry is pruned by `purge_expired`.
///                     `0` means no TTL (keep forever up to `dlq_retention`).
#[derive(Clone, Debug)]
pub struct QueueConfig {
    pub capacity:       usize,
    pub high_watermark: usize,
    pub low_watermark:  usize,
    pub dlq_retention:  usize,
    pub dlq_ttl_secs:   u64,
}

impl QueueConfig {
    pub fn new(capacity: usize, hwm_ratio: f64, lwm_ratio: f64, dlq_retention: usize) -> Self {
        let high_watermark = ((capacity as f64) * hwm_ratio.clamp(0.0, 1.0)) as usize;
        let low_watermark  = ((capacity as f64) * lwm_ratio.clamp(0.0, 1.0)) as usize;
        Self {
            capacity,
            high_watermark,
            low_watermark,
            dlq_retention,
            dlq_ttl_secs: 0,
        }
    }

    pub fn with_dlq_ttl(mut self, secs: u64) -> Self {
        self.dlq_ttl_secs = secs;
        self
    }
}

impl Default for QueueConfig {
    fn default() -> Self {
        Self::new(65_536, 0.80, 0.20, 5_000)
    }
}

// ------------------------------------------------------------------
// BackpressureState
// ------------------------------------------------------------------

/// Describes the pressure level of a single named queue.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BackpressureState {
    /// Queue is healthy — below high watermark.
    Normal,
    /// Queue is above the high watermark; callers should slow down.
    High,
    /// Queue channel is full; new jobs are rejected outright.
    Full,
}

impl BackpressureState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Normal => "normal",
            Self::High   => "high",
            Self::Full   => "full",
        }
    }
}

// ------------------------------------------------------------------
// DeadLetterQueue
// ------------------------------------------------------------------

use std::collections::VecDeque;
use crate::job::QueueJob;

/// A retention-bounded, optionally TTL-pruned dead-letter store.
pub struct DeadLetterQueue {
    pub jobs:        VecDeque<QueueJob>,
    pub arrived_at:  VecDeque<f64>,   // Unix timestamp each job arrived in DLQ
    pub retention:   usize,
    pub ttl_secs:    u64,
}

impl DeadLetterQueue {
    pub fn new(retention: usize, ttl_secs: u64) -> Self {
        Self {
            jobs:       VecDeque::with_capacity(retention.min(4096)),
            arrived_at: VecDeque::with_capacity(retention.min(4096)),
            retention,
            ttl_secs,
        }
    }

    /// Push a job, evicting the oldest if over retention.
    pub fn push(&mut self, job: QueueJob, now: f64) {
        if self.jobs.len() >= self.retention {
            self.jobs.pop_front();
            self.arrived_at.pop_front();
        }
        self.jobs.push_back(job);
        self.arrived_at.push_back(now);
    }

    /// Remove and return a job by id.
    pub fn remove_by_id(&mut self, job_id: &str) -> Option<QueueJob> {
        if let Some(pos) = self.jobs.iter().position(|j| j.id == job_id) {
            self.arrived_at.remove(pos);
            self.jobs.remove(pos)
        } else {
            None
        }
    }

    /// Prune entries older than `ttl_secs` (only active when `ttl_secs > 0`).
    pub fn purge_expired(&mut self, now: f64) -> usize {
        if self.ttl_secs == 0 {
            return 0;
        }
        let threshold = now - (self.ttl_secs as f64);
        let mut pruned = 0;
        while let Some(&ts) = self.arrived_at.front() {
            if ts < threshold {
                self.jobs.pop_front();
                self.arrived_at.pop_front();
                pruned += 1;
            } else {
                break;
            }
        }
        pruned
    }

    pub fn len(&self) -> usize {
        self.jobs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.jobs.is_empty()
    }

    pub fn iter_recent(&self, limit: usize) -> impl Iterator<Item = &QueueJob> {
        self.jobs.iter().rev().take(limit)
    }
}
