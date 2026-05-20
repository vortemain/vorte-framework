use std::collections::{BinaryHeap, VecDeque};
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
    created_at: Instant,
}

impl PriorityTask {
    fn age_score(&self) -> i64 {
        let priority_weight = match self.priority {
            TaskPriority::Low => 0,
            TaskPriority::Normal => 1000,
            TaskPriority::High => 5000,
            TaskPriority::Critical => 10000,
        };
        // Stable priority aging score rank:
        // Lower seq means older task. By subtracting seq, older tasks naturally gain score.
        // This is a stable, lock-free, pure-integer priority aging formula that preserves Ord.
        (priority_weight as i64 * 10000) - (self.seq as i64)
    }
}

impl PartialEq for PriorityTask {
    fn eq(&self, other: &Self) -> bool {
        self.age_score() == other.age_score() && self.seq == other.seq
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
        self.age_score()
            .cmp(&other.age_score())
            .then_with(|| other.seq.cmp(&self.seq))
    }
}

struct SharedQueue {
    global_heap: Mutex<BinaryHeap<PriorityTask>>,
    local_queues: Vec<Mutex<VecDeque<PriorityTask>>>,
    cond: Condvar,
    shutdown: AtomicBool,
    seq: AtomicU64,
}

pub(crate) struct WorkerPool {
    queue: Arc<SharedQueue>,
    stats: Arc<AtomicStats>,
    worker_count: usize,
}

impl WorkerPool {
    pub fn new(worker_count: usize, stats: Arc<AtomicStats>) -> Self {
        let mut local_queues = Vec::with_capacity(worker_count);
        for _ in 0..worker_count {
            local_queues.push(Mutex::new(VecDeque::with_capacity(128)));
        }

        let queue = Arc::new(SharedQueue {
            global_heap: Mutex::new(BinaryHeap::with_capacity(1024)),
            local_queues,
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
                    Self::worker_loop(worker_id, worker_count, queue, stats);
                })
                .expect("Failed to spawn scheduler worker thread");
        }

        WorkerPool { queue, stats, worker_count }
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
            created_at: task.created_at,
        };

        if task.priority == TaskPriority::High || task.priority == TaskPriority::Critical {
            let mut heap = self.queue.global_heap.lock().unwrap();
            heap.push(ptask);
        } else {
            // Distribute to per-worker queues to bypass locks under high concurrency
            let idx = (seq as usize) % self.worker_count;
            let mut local = self.queue.local_queues[idx].lock().unwrap();
            if task.priority == TaskPriority::Normal {
                local.push_front(ptask);
            } else {
                local.push_back(ptask);
            }
        }
        self.queue.cond.notify_all();
        self.stats.submitted.fetch_add(1, Ordering::Relaxed);
    }

    pub fn submit_batch(&self, tasks: Vec<Task>) {
        let mut heap = self.queue.global_heap.lock().unwrap();
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
                created_at: task.created_at,
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
        worker_count: usize,
        queue: Arc<SharedQueue>,
        stats: Arc<AtomicStats>,
    ) {
        loop {
            // Work retrieval loop: global priority heap -> local queue -> steal from siblings
            let mut task = None;

            // 1. Try global heap first (priority path)
            if let Ok(mut heap) = queue.global_heap.lock() {
                task = heap.pop();
            }

            // 2. Try local queue (no contention, fast path)
            if task.is_none() {
                if let Ok(mut local) = queue.local_queues[worker_id].lock() {
                    task = local.pop_front();
                }
            }

            // 3. Try to steal from sibling worker local queues
            if task.is_none() {
                for offset in 1..worker_count {
                    let target_worker = (worker_id + offset) % worker_count;
                    if let Ok(mut other_local) = queue.local_queues[target_worker].lock() {
                        if let Some(stolen) = other_local.pop_back() {
                            task = Some(stolen);
                            break;
                        }
                    }
                }
            }

            // Decrement correct stats if a task was fetched
            if let Some(ref t) = task {
                match t.priority {
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
            }

            // If no task, await on condition variable (unless shutdown is triggered)
            let task = match task {
                Some(t) => t,
                None => {
                    if queue.shutdown.load(Ordering::SeqCst) {
                        break;
                    }
                    // Wait for new tasks using the global heap lock as block point
                    let heap_lock = queue.global_heap.lock().unwrap();
                    let _guard = match queue.cond.wait(heap_lock) {
                        Ok(g) => g,
                        Err(poisoned) => poisoned.into_inner(),
                    };
                    continue;
                }
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
