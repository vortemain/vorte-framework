mod stats;
mod task;
mod worker;

pub use stats::SchedulerStats;
pub use task::{BatchTask, Task, TaskPriority};

use std::sync::Arc;

use stats::AtomicStats;
use worker::WorkerPool;

#[derive(Debug, Clone)]
pub struct SchedulerConfig {
    pub worker_threads: usize,
}

impl Default for SchedulerConfig {
    fn default() -> Self {
        let workers = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4);
        Self {
            worker_threads: workers * 2,
        }
    }
}

pub struct TaskScheduler {
    pool: WorkerPool,
    stats: Arc<AtomicStats>,
}

impl TaskScheduler {
    pub fn new(config: SchedulerConfig) -> Self {
        let stats = AtomicStats::new();
        let pool = WorkerPool::new(config.worker_threads, stats.clone());
        TaskScheduler { pool, stats }
    }

    pub fn submit(&self, task: Task) {
        self.pool.submit(task);
    }

    pub fn submit_batch(&self, tasks: Vec<Task>) {
        self.pool.submit_batch(tasks);
    }

    pub fn stats(&self) -> SchedulerStats {
        self.stats.snapshot()
    }

    pub fn shutdown(&self) {
        self.pool.shutdown();
    }
}

impl Default for TaskScheduler {
    fn default() -> Self {
        Self::new(SchedulerConfig::default())
    }
}
