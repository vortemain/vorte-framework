use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;

#[derive(Clone, Copy, Debug, Default)]
pub struct SchedulerStats {
    pub tasks_submitted: u64,
    pub tasks_completed: u64,
    pub tasks_failed: u64,
    pub active_workers: usize,
    pub queue_depth: usize,
    pub queue_depth_high: usize,
    pub queue_depth_normal: usize,
    pub queue_depth_low: usize,
}

pub(crate) struct AtomicStats {
    pub submitted: AtomicU64,
    pub completed: AtomicU64,
    pub failed: AtomicU64,
    pub active_workers: AtomicUsize,
    pub queue_depth_high: AtomicUsize,
    pub queue_depth_normal: AtomicUsize,
    pub queue_depth_low: AtomicUsize,
}

impl AtomicStats {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            submitted: AtomicU64::new(0),
            completed: AtomicU64::new(0),
            failed: AtomicU64::new(0),
            active_workers: AtomicUsize::new(0),
            queue_depth_high: AtomicUsize::new(0),
            queue_depth_normal: AtomicUsize::new(0),
            queue_depth_low: AtomicUsize::new(0),
        })
    }

    pub fn snapshot(&self) -> SchedulerStats {
        SchedulerStats {
            tasks_submitted: self.submitted.load(Ordering::Relaxed),
            tasks_completed: self.completed.load(Ordering::Relaxed),
            tasks_failed: self.failed.load(Ordering::Relaxed),
            active_workers: self.active_workers.load(Ordering::Relaxed),
            queue_depth: self.queue_depth_high.load(Ordering::Relaxed)
                + self.queue_depth_normal.load(Ordering::Relaxed)
                + self.queue_depth_low.load(Ordering::Relaxed),
            queue_depth_high: self.queue_depth_high.load(Ordering::Relaxed),
            queue_depth_normal: self.queue_depth_normal.load(Ordering::Relaxed),
            queue_depth_low: self.queue_depth_low.load(Ordering::Relaxed),
        }
    }
}
