use std::collections::BinaryHeap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Instant;

use crate::stats::AtomicStats;
use crate::task::{Task, TaskPriority};

struct PriorityTask {
    priority: TaskPriority,
    seq: u64,
    work: Box<dyn FnOnce() + Send + 'static>,
    deadline: Option<Instant>,
}

impl PartialEq for PriorityTask {
    fn eq(&self, other: &Self) -> bool {
        self.priority == other.priority && self.seq == other.seq
    }
}

impl Eq for PriorityTask {}

impl PartialOrd for PriorityTask {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for PriorityTask {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.priority
            .cmp(&other.priority)
            .then_with(|| other.seq.cmp(&self.seq))
    }
}

struct SharedQueue {
    heap: Mutex<BinaryHeap<PriorityTask>>,
    cond: Condvar,
    shutdown: AtomicBool,
    seq: AtomicU64,
}

pub(crate) struct WorkerPool {
    queue: Arc<SharedQueue>,
    stats: Arc<AtomicStats>,
}

impl WorkerPool {
    pub fn new(worker_count: usize, stats: Arc<AtomicStats>) -> Self {
        let queue = Arc::new(SharedQueue {
            heap: Mutex::new(BinaryHeap::with_capacity(1024)),
            cond: Condvar::new(),
            shutdown: AtomicBool::new(false),
            seq: AtomicU64::new(0),
        });

        for worker_id in 0..worker_count {
            let queue = queue.clone();
            let stats = stats.clone();

            std::thread::Builder::new()
                .name(format!("vorte-worker-{}", worker_id))
                .spawn(move || {
                    Self::worker_loop(worker_id, queue, stats);
                })
                .expect("Failed to spawn scheduler worker thread");
        }

        WorkerPool { queue, stats }
    }

    pub fn submit(&self, task: Task) {
        let seq = self.queue.seq.fetch_add(1, Ordering::Relaxed);
        match task.priority {
            TaskPriority::Low => {
                self.stats.queue_depth_low.fetch_add(1, Ordering::Relaxed);
            }
            TaskPriority::Normal => {
                self.stats.queue_depth_normal.fetch_add(1, Ordering::Relaxed);
            }
            TaskPriority::High | TaskPriority::Critical => {
                self.stats.queue_depth_high.fetch_add(1, Ordering::Relaxed);
            }
        }
        let ptask = PriorityTask {
            priority: task.priority,
            seq,
            work: task.work,
            deadline: task.deadline,
        };

        {
            let mut heap = self.queue.heap.lock().unwrap();
            heap.push(ptask);
        }
        self.queue.cond.notify_one();
        self.stats.submitted.fetch_add(1, Ordering::Relaxed);
    }

    pub fn submit_batch(&self, tasks: Vec<Task>) {
        let mut heap = self.queue.heap.lock().unwrap();
        for task in tasks {
            let seq = self.queue.seq.fetch_add(1, Ordering::Relaxed);
            match task.priority {
                TaskPriority::Low => {
                    self.stats.queue_depth_low.fetch_add(1, Ordering::Relaxed);
                }
                TaskPriority::Normal => {
                    self.stats.queue_depth_normal.fetch_add(1, Ordering::Relaxed);
                }
                TaskPriority::High | TaskPriority::Critical => {
                    self.stats.queue_depth_high.fetch_add(1, Ordering::Relaxed);
                }
            }
            let ptask = PriorityTask {
                priority: task.priority,
                seq,
                work: task.work,
                deadline: task.deadline,
            };
            heap.push(ptask);
            self.stats.submitted.fetch_add(1, Ordering::Relaxed);
        }
        drop(heap);
        self.queue.cond.notify_all();
    }

    pub fn shutdown(&self) {
        self.queue.shutdown.store(true, Ordering::SeqCst);
        self.queue.cond.notify_all();
    }

    fn worker_loop(
        worker_id: usize,
        queue: Arc<SharedQueue>,
        stats: Arc<AtomicStats>,
    ) {
        loop {
            let task = {
                let mut heap = match queue.heap.lock() {
                    Ok(h) => h,
                    Err(_) => break,
                };
                loop {
                    if let Some(task) = heap.pop() {
                        match task.priority {
                            TaskPriority::Low => {
                                stats.queue_depth_low.fetch_sub(1, Ordering::Relaxed);
                            }
                            TaskPriority::Normal => {
                                stats.queue_depth_normal.fetch_sub(1, Ordering::Relaxed);
                            }
                            TaskPriority::High | TaskPriority::Critical => {
                                stats.queue_depth_high.fetch_sub(1, Ordering::Relaxed);
                            }
                        }
                        break Some(task);
                    }
                    if queue.shutdown.load(Ordering::SeqCst) {
                        break None;
                    }
                    heap = match queue.cond.wait(heap) {
                        Ok(h) => h,
                        Err(poisoned) => poisoned.into_inner(),
                    };
                }
            };

            let task = match task {
                Some(t) => t,
                None => break,
            };

            stats.active_workers.fetch_add(1, Ordering::Relaxed);

            if let Some(deadline) = task.deadline {
                if Instant::now() > deadline {
                    stats.failed.fetch_add(1, Ordering::Relaxed);
                    stats.completed.fetch_add(1, Ordering::Relaxed);
                    stats.active_workers.fetch_sub(1, Ordering::Relaxed);
                    continue;
                }
            }

            let label = format!("worker-{}", worker_id);
            let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                (task.work)();
            }));

            if result.is_err() {
                tracing::error!("Task panic on {}", label);
                stats.failed.fetch_add(1, Ordering::Relaxed);
            }

            stats.completed.fetch_add(1, Ordering::Relaxed);
            stats.active_workers.fetch_sub(1, Ordering::Relaxed);
        }

        tracing::debug!("Worker {} shutting down", worker_id);
    }
}
