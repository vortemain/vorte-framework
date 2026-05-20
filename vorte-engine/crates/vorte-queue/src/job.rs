use std::cmp::Ordering;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

/// Job priority levels.  Higher discriminant = processed sooner.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum JobPriority {
    Low      = 0,
    Normal   = 1,
    High     = 2,
    Critical = 3,
}

impl Default for JobPriority {
    fn default() -> Self {
        JobPriority::Normal
    }
}

/// Lifecycle state of a job.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum JobStatus {
    Pending,
    Scheduled,
    Running,
    Completed,
    Failed,
    Retry,
}

/// The serialisable job envelope that flows through the queue.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct QueueJob {
    pub id:           String,
    pub queue:        String,
    pub job_class:    String,
    /// Opaque bytes — callers pack their own payload (msgpack / JSON / raw).
    pub payload:      Vec<u8>,
    pub priority:     JobPriority,
    pub status:       JobStatus,
    pub attempts:     u32,
    pub max_attempts: u32,
    pub retry_delay:  f64,
    pub run_at:       Option<f64>,
    pub scheduled_at: f64,
    pub started_at:   Option<f64>,
    pub completed_at: Option<f64>,
    pub failed_at:    Option<f64>,
    pub error:        Option<String>,
    pub trace_id:     Option<String>,
}

impl QueueJob {
    pub fn new(id: String, queue: String, job_class: String) -> Self {
        Self {
            id,
            queue,
            job_class,
            payload:      Vec::new(),
            priority:     JobPriority::Normal,
            status:       JobStatus::Pending,
            attempts:     0,
            max_attempts: 3,
            retry_delay:  30.0,
            run_at:       None,
            scheduled_at: now_secs(),
            started_at:   None,
            completed_at: None,
            failed_at:    None,
            error:        None,
            trace_id:     None,
        }
    }
}

// ------------------------------------------------------------------
// Ordering: max-heap by priority, FIFO tiebreak on scheduled_at
// ------------------------------------------------------------------

impl PartialEq for QueueJob {
    fn eq(&self, other: &Self) -> bool {
        self.id == other.id
    }
}
impl Eq for QueueJob {}

impl PartialOrd for QueueJob {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for QueueJob {
    fn cmp(&self, other: &Self) -> Ordering {
        self.priority
            .cmp(&other.priority)
            .then_with(|| {
                other
                    .scheduled_at
                    .partial_cmp(&self.scheduled_at)
                    .unwrap_or(Ordering::Equal)
            })
    }
}

/// Current time as fractional Unix seconds.
pub fn now_secs() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}
