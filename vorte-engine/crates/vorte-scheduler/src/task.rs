use std::time::Instant;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum TaskPriority {
    Low = 0,
    Normal = 1,
    High = 2,
    Critical = 3,
}

impl Default for TaskPriority {
    fn default() -> Self {
        TaskPriority::Normal
    }
}

pub struct Task {
    pub work: Box<dyn FnOnce() + Send + 'static>,
    pub priority: TaskPriority,
    pub deadline: Option<Instant>,
    pub label: Option<String>,
}

impl Task {
    pub fn new(work: Box<dyn FnOnce() + Send + 'static>) -> Self {
        Self {
            work,
            priority: TaskPriority::Normal,
            deadline: None,
            label: None,
        }
    }

    pub fn with_priority(mut self, priority: TaskPriority) -> Self {
        self.priority = priority;
        self
    }

    pub fn with_deadline(mut self, deadline: Instant) -> Self {
        self.deadline = Some(deadline);
        self
    }

    pub fn with_label(mut self, label: impl Into<String>) -> Self {
        self.label = Some(label.into());
        self
    }
}

pub struct BatchTask {
    pub tasks: Vec<Task>,
    pub priority: TaskPriority,
}

impl BatchTask {
    pub fn new(tasks: Vec<Task>) -> Self {
        let priority = tasks
            .iter()
            .map(|t| t.priority)
            .max()
            .unwrap_or(TaskPriority::Normal);
        Self { tasks, priority }
    }
}
