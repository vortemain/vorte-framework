use std::cmp::Ordering;
use std::collections::{BTreeMap, BinaryHeap, HashMap, VecDeque};

use crossbeam_channel::{bounded, Receiver, Sender, TryRecvError, TrySendError};
use tracing::{debug, warn};

use crate::backpressure::{BackpressureState, DeadLetterQueue, QueueConfig};
use crate::job::{now_secs, JobPriority, JobStatus, QueueJob};

// ------------------------------------------------------------------
// Internal helpers
// ------------------------------------------------------------------

const MAX_COMPLETED_PER_QUEUE: usize = 1_000;

/// Newtype for f64 keys in BTreeMap (scheduled timestamps).
#[derive(Clone, Copy, Debug, PartialEq)]
struct OrderedFloat(f64);

impl Eq for OrderedFloat {}

impl PartialOrd for OrderedFloat {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        self.0.partial_cmp(&other.0)
    }
}

impl Ord for OrderedFloat {
    fn cmp(&self, other: &Self) -> Ordering {
        self.partial_cmp(other).unwrap_or(Ordering::Equal)
    }
}

// ------------------------------------------------------------------
// Per-queue channel pair + priority peek buffer
// ------------------------------------------------------------------

/// One logical named queue backed by a bounded crossbeam channel.
/// We keep a local BinaryHeap mirror so we can peek the highest-priority
/// job across multiple queues without destructively consuming the channel.
struct ChannelQueue {
    tx:     Sender<QueueJob>,
    rx:     Receiver<QueueJob>,
    /// Mirror heap — always kept in sync with channel contents during dequeue.
    buffer: BinaryHeap<QueueJob>,
    config: QueueConfig,
}

impl ChannelQueue {
    fn new(config: QueueConfig) -> Self {
        let (tx, rx) = bounded(config.capacity);
        Self {
            tx,
            rx,
            buffer: BinaryHeap::new(),
            config,
        }
    }

    fn len(&self) -> usize {
        // channel len is authoritative for enqueue backpressure;
        // buffer is empty outside of a dequeue cycle.
        self.tx.len() + self.buffer.len()
    }

    fn backpressure_state(&self) -> BackpressureState {
        let n = self.len();
        if n >= self.config.capacity {
            BackpressureState::Full
        } else if n >= self.config.high_watermark {
            BackpressureState::High
        } else {
            BackpressureState::Normal
        }
    }

    /// Drain all available items from the channel into the local buffer.
    fn drain_to_buffer(&mut self) {
        loop {
            match self.rx.try_recv() {
                Ok(job) => self.buffer.push(job),
                Err(TryRecvError::Empty) | Err(TryRecvError::Disconnected) => break,
            }
        }
    }

    /// Return the priority of the best buffered job (for cross-queue comparison).
    fn peek_priority(&mut self) -> Option<JobPriority> {
        self.drain_to_buffer();
        self.buffer.peek().map(|j| j.priority)
    }

    /// Pop the highest-priority job from the buffer.
    fn pop_best(&mut self) -> Option<QueueJob> {
        self.buffer.pop()
    }
}

// ------------------------------------------------------------------
// Enqueue result
// ------------------------------------------------------------------

/// Result of an enqueue attempt.
#[derive(Debug)]
pub enum EnqueueResult {
    /// Job accepted and scheduled normally.
    Ok(String),
    /// Job accepted but the queue is above its high-watermark.
    /// Callers should apply backpressure (slow producers).
    Backpressure(String),
    /// Queue channel is full; job was *not* accepted.
    Full(String),
}

// ------------------------------------------------------------------
// QueueEngine
// ------------------------------------------------------------------

pub struct QueueEngine {
    /// Named queues, each backed by a bounded channel.
    queues:      HashMap<String, ChannelQueue>,
    /// Jobs currently being processed: job_id → QueueJob.
    processing:  HashMap<String, QueueJob>,
    /// Future-scheduled jobs keyed by run_at timestamp.
    scheduled:   BTreeMap<OrderedFloat, VecDeque<QueueJob>>,
    /// Dead-letter queues per named queue.
    dead_letter: HashMap<String, DeadLetterQueue>,
    /// Completed ring-buffers per named queue.
    completed:   HashMap<String, VecDeque<QueueJob>>,
    /// Default config applied to queues that don't have an explicit entry.
    default_cfg: QueueConfig,
}

// ------------------------------------------------------------------
// QueueStats
// ------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct QueueStats {
    pub queues:      HashMap<String, usize>,
    pub processing:  usize,
    pub dead_letter: usize,
    pub completed:   usize,
    pub scheduled:   usize,
    /// Watermark state ("normal" | "high" | "full") per queue.
    pub watermarks:  HashMap<String, &'static str>,
}

// ------------------------------------------------------------------
// Implementation
// ------------------------------------------------------------------

impl QueueEngine {
    /// Create a new engine with a uniform default config for all queues.
    pub fn new(capacity: usize) -> Self {
        Self::with_config(QueueConfig::new(capacity, 0.80, 0.20, 5_000))
    }

    /// Create a new engine with a fully-customised default config.
    pub fn with_config(default_cfg: QueueConfig) -> Self {
        Self {
            queues:      HashMap::new(),
            processing:  HashMap::new(),
            scheduled:   BTreeMap::new(),
            dead_letter: HashMap::new(),
            completed:   HashMap::new(),
            default_cfg,
        }
    }

    /// Override the config for a specific named queue.
    /// Must be called before any jobs are enqueued to that queue.
    pub fn configure_queue(&mut self, queue_name: &str, cfg: QueueConfig) {
        self.queues
            .entry(queue_name.to_string())
            .or_insert_with(|| ChannelQueue::new(cfg.clone()));
        // If it already exists we can't resize the channel, but we update
        // the stored config so watermark calculations remain accurate.
        if let Some(q) = self.queues.get_mut(queue_name) {
            q.config = cfg;
        }
    }

    // ------------------------------------------------------------------
    // Enqueue
    // ------------------------------------------------------------------

    pub fn enqueue(&mut self, mut job: QueueJob) -> EnqueueResult {
        // If scheduled for the future, park in the BTreeMap.
        if let Some(run_at) = job.run_at {
            if run_at > now_secs() {
                job.status = JobStatus::Scheduled;
                self.scheduled
                    .entry(OrderedFloat(run_at))
                    .or_insert_with(VecDeque::new)
                    .push_back(job.clone());
                return EnqueueResult::Ok(job.id);
            }
        }

        job.status = JobStatus::Pending;
        let queue_name = job.queue.clone();
        let job_id = job.id.clone();

        let cq = self
            .queues
            .entry(queue_name.clone())
            .or_insert_with(|| ChannelQueue::new(self.default_cfg.clone()));

        match cq.tx.try_send(job) {
            Ok(()) => {
                let state = cq.backpressure_state();
                if state == BackpressureState::High {
                    warn!(queue = %queue_name, "Queue above high-watermark — applying backpressure");
                    EnqueueResult::Backpressure(job_id)
                } else {
                    debug!(queue = %queue_name, job_id = %job_id, "Job enqueued");
                    EnqueueResult::Ok(job_id)
                }
            }
            Err(TrySendError::Full(_)) => {
                warn!(queue = %queue_name, "Queue channel full — rejecting job");
                EnqueueResult::Full(job_id)
            }
            Err(TrySendError::Disconnected(_)) => {
                warn!(queue = %queue_name, "Queue channel disconnected");
                EnqueueResult::Full(job_id)
            }
        }
    }

    // ------------------------------------------------------------------
    // Promote scheduled jobs whose run_at has arrived
    // ------------------------------------------------------------------

    pub fn promote_scheduled(&mut self) -> usize {
        let now = now_secs();
        let mut promoted = 0;
        let mut keys_to_remove = Vec::new();

        for (&key, jobs) in self.scheduled.iter_mut() {
            if key.0 <= now {
                while let Some(mut job) = jobs.pop_front() {
                    job.status = JobStatus::Pending;
                    let cq = self
                        .queues
                        .entry(job.queue.clone())
                        .or_insert_with(|| ChannelQueue::new(self.default_cfg.clone()));
                    let _ = cq.tx.try_send(job);
                    promoted += 1;
                }
                keys_to_remove.push(key);
            } else {
                break; // BTreeMap is sorted — no point scanning further
            }
        }

        for key in keys_to_remove {
            self.scheduled.remove(&key);
        }
        promoted
    }

    // ------------------------------------------------------------------
    // Batch dequeue
    // ------------------------------------------------------------------

    /// Dequeue up to `count` jobs from the named queues, picking the
    /// highest-priority job across all queues on each iteration.
    pub fn dequeue(&mut self, queue_names: &[String], count: usize) -> Vec<QueueJob> {
        self.promote_scheduled();

        let mut results = Vec::with_capacity(count);

        while results.len() < count {
            // Find which queue has the globally best next job.
            let best_queue = queue_names
                .iter()
                .filter_map(|name| {
                    self.queues
                        .get_mut(name)
                        .and_then(|cq| cq.peek_priority().map(|p| (p, name.clone())))
                })
                .max_by_key(|(p, _)| *p)
                .map(|(_, name)| name);

            match best_queue {
                Some(name) => {
                    if let Some(cq) = self.queues.get_mut(&name) {
                        if let Some(mut job) = cq.pop_best() {
                            job.status = JobStatus::Running;
                            job.started_at = Some(now_secs());
                            self.processing.insert(job.id.clone(), job.clone());
                            results.push(job);
                        } else {
                            break;
                        }
                    } else {
                        break;
                    }
                }
                None => break,
            }
        }

        results
    }

    // ------------------------------------------------------------------
    // Complete / Fail
    // ------------------------------------------------------------------

    pub fn complete(&mut self, job_id: &str) -> Option<QueueJob> {
        let mut job = self.processing.remove(job_id)?;
        job.status = JobStatus::Completed;
        job.completed_at = Some(now_secs());

        let ring = self
            .completed
            .entry(job.queue.clone())
            .or_insert_with(|| VecDeque::with_capacity(MAX_COMPLETED_PER_QUEUE));

        if ring.len() >= MAX_COMPLETED_PER_QUEUE {
            ring.pop_front();
        }
        ring.push_back(job.clone());

        Some(job)
    }

    pub fn fail(&mut self, job_id: &str, error: &str) -> Option<QueueJob> {
        let mut job = self.processing.remove(job_id)?;
        job.attempts += 1;
        job.error = Some(error.to_string());

        if job.attempts >= job.max_attempts {
            job.status = JobStatus::Failed;
            job.failed_at = Some(now_secs());

            let now = now_secs();
            let cfg = self
                .queues
                .get(&job.queue)
                .map(|cq| cq.config.clone())
                .unwrap_or_else(|| self.default_cfg.clone());

            let dlq = self
                .dead_letter
                .entry(job.queue.clone())
                .or_insert_with(|| DeadLetterQueue::new(cfg.dlq_retention, cfg.dlq_ttl_secs));

            dlq.push(job.clone(), now);
        } else {
            // Exponential-backoff retry
            job.status = JobStatus::Retry;
            let backoff = job.retry_delay * (2u32.pow(job.attempts - 1) as f64);
            let run_at = now_secs() + backoff;
            job.run_at = Some(run_at);

            self.scheduled
                .entry(OrderedFloat(run_at))
                .or_insert_with(VecDeque::new)
                .push_back(job.clone());
        }

        Some(job)
    }

    // ------------------------------------------------------------------
    // Dead-letter management
    // ------------------------------------------------------------------

    pub fn retry_dead_letter(&mut self, job_id: &str) -> Option<QueueJob> {
        let queue_names: Vec<String> = self.dead_letter.keys().cloned().collect();

        for queue_name in queue_names {
            if let Some(dlq) = self.dead_letter.get_mut(&queue_name) {
                if let Some(mut job) = dlq.remove_by_id(job_id) {
                    job.status = JobStatus::Pending;
                    job.attempts = 0;
                    job.error = None;
                    job.run_at = None;

                    let cq = self
                        .queues
                        .entry(job.queue.clone())
                        .or_insert_with(|| ChannelQueue::new(self.default_cfg.clone()));
                    let _ = cq.tx.try_send(job.clone());
                    return Some(job);
                }
            }
        }
        None
    }

    pub fn get_dead_letter(&self, queue_name: &str, limit: usize) -> Vec<&QueueJob> {
        self.dead_letter
            .get(queue_name)
            .map(|dlq| dlq.iter_recent(limit).collect())
            .unwrap_or_default()
    }

    /// Prune TTL-expired entries from every DLQ.
    /// Returns total number of entries removed.
    pub fn purge_expired_dlq(&mut self) -> usize {
        let now = now_secs();
        self.dead_letter
            .values_mut()
            .map(|dlq| dlq.purge_expired(now))
            .sum()
    }

    // ------------------------------------------------------------------
    // Backpressure / watermark inspection
    // ------------------------------------------------------------------

    pub fn watermark_states(&self) -> HashMap<String, &'static str> {
        self.queues
            .iter()
            .map(|(name, cq)| (name.clone(), cq.backpressure_state().as_str()))
            .collect()
    }

    pub fn backpressure_state(&self, queue_name: &str) -> BackpressureState {
        self.queues
            .get(queue_name)
            .map(|cq| cq.backpressure_state())
            .unwrap_or(BackpressureState::Normal)
    }

    // ------------------------------------------------------------------
    // Stats / size
    // ------------------------------------------------------------------

    pub fn stats(&self) -> QueueStats {
        QueueStats {
            queues: self
                .queues
                .iter()
                .map(|(k, cq)| (k.clone(), cq.tx.len()))
                .collect(),
            processing: self.processing.len(),
            dead_letter: self.dead_letter.values().map(|d| d.len()).sum(),
            completed: self.completed.values().map(|v| v.len()).sum(),
            scheduled: self.scheduled.values().map(|v| v.len()).sum(),
            watermarks: self.watermark_states(),
        }
    }

    pub fn queue_size(&self, queue_name: &str) -> usize {
        self.queues
            .get(queue_name)
            .map(|cq| cq.tx.len())
            .unwrap_or(0)
    }
}
