pub const MAX_PARAMS: usize = 16;

#[derive(Clone, Debug)]
pub struct Param {
    pub key: String,
    pub value_start: u32,
    pub value_len: u32,
}

impl Param {
    pub fn value<'a>(&self, path: &'a str) -> &'a str {
        let start = self.value_start as usize;
        let end = start + self.value_len as usize;
        &path[start..end]
    }
}

#[derive(Clone, Debug)]
pub struct Params {
    data: Vec<Param>,
}

impl Params {
    pub fn new() -> Self {
        Self {
            data: Vec::with_capacity(MAX_PARAMS),
        }
    }

    #[inline]
    pub fn push(&mut self, key: &str, value_start: u32, value_len: u32) -> bool {
        if self.data.len() >= MAX_PARAMS {
            return false;
        }
        self.data.push(Param {
            key: key.to_string(),
            value_start,
            value_len,
        });
        true
    }
    
    #[inline]
    pub fn pop(&mut self) {
        self.data.pop();
    }

    #[inline]
    pub fn get(&self, key: &str) -> Option<&Param> {
        self.data.iter().find(|p| p.key == key)
    }

    #[inline]
    pub fn len(&self) -> usize {
        self.data.len()
    }

    #[inline]
    pub fn is_empty(&self) -> bool {
        self.data.is_empty()
    }

    pub fn iter(&self) -> impl Iterator<Item = &Param> {
        self.data.iter()
    }
}

impl Default for Params {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Debug, Clone)]
pub struct MatchResult {
    pub handler_id: u32,
    pub params: Params,
    pub matched: bool,
}

impl MatchResult {
    pub fn not_found() -> Self {
        Self {
            handler_id: 0,
            params: Params::new(),
            matched: false,
        }
    }

    pub fn found(handler_id: u32, params: Params) -> Self {
        Self {
            handler_id,
            params,
            matched: true,
        }
    }

    pub fn param_value<'a>(&self, key: &str, path: &'a str) -> Option<&'a str> {
        self.params.get(key).map(|p| {
            let mut start = p.value_start as usize;
            if path.starts_with('/') {
                start += 1;
            }
            let end = start + p.value_len as usize;
            &path[start..end]
        })
    }
}
