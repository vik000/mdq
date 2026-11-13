//! Import expansion (spec section 3; G35 in docs/TEST_PLAN.md).
//!
//! `![[path]]`, `![[path#Heading]]` and `![[path#^id]]` stand alone in a paragraph at column 1 and
//! are replaced by the target's body (frontmatter removed), the section of its first matching
//! heading, or one block, verbatim. The host reads files through a callback; every path is read at
//! most once. Cycles are detected on (path, line range) frames, so a section only cycles when it
//! really contains the import.

use crate::blocks::{line_bounds, parse, Kind, Leaf};
use crate::diag::{Code, Diagnostic};
use crate::frontmatter;
use crate::paths::path_normalise;
use crate::refs::{resolve_in, Index};
use crate::vars::html_inline_end;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

type Res<T> = Result<T, Diagnostic>;

const MAX_INPUT: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone, Copy)]
pub struct Limits {
    pub max_depth: usize,
    pub max_expansions: usize,
    pub max_bytes: usize,
}

impl Default for Limits {
    fn default() -> Self {
        Limits { max_depth: 64, max_expansions: 10_000, max_bytes: MAX_INPUT }
    }
}

/// `out_start..out_end` (0-based, exclusive) of the output came from `path` starting at
/// 1-based file line `src_start`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Segment {
    pub out_start: usize,
    pub out_end: usize,
    pub path: String,
    pub src_start: usize,
}

#[derive(Debug, Clone)]
pub struct Expanded {
    pub text: String,
    pub map: Vec<Segment>,
}

struct Src {
    path: String,
    body: String,
    body_line: usize,
    c: Vec<char>,
    starts: Vec<usize>,
    ends: Vec<usize>,
    leaves: Vec<Leaf>,
    ix: RefCell<Option<Rc<Index>>>,
}

fn with_path(mut d: Diagnostic, path: &str) -> Diagnostic {
    if d.path.is_none() {
        d.path = Some(path.to_string());
    }
    d
}

fn locate(mut d: Diagnostic, path: &str, line: u32, col: u32) -> Diagnostic {
    if d.path.is_none() {
        d.path = Some(path.to_string());
    }
    if d.line.is_none() {
        d.line = Some(line);
        d.column = Some(col);
    }
    d
}

impl Src {
    fn new(path: &str, text: &str) -> Res<Src> {
        let sp = frontmatter::split(text).map_err(|d| with_path(d, path))?;
        let body = sp.body.to_string();
        let c: Vec<char> = body.chars().collect();
        let (starts, ends) = line_bounds(&c);
        let leaves = parse(&c, &starts, &ends, 1).map_err(|d| with_path(d, path))?;
        Ok(Src {
            path: path.to_string(),
            body,
            body_line: sp.body_line as usize,
            c,
            starts,
            ends,
            leaves,
            ix: RefCell::new(None),
        })
    }

    fn n(&self) -> usize {
        self.starts.len()
    }

    fn index(&self) -> Res<Rc<Index>> {
        if let Some(i) = self.ix.borrow().as_ref() {
            return Ok(i.clone());
        }
        let i = Rc::new(crate::refs::build(&self.body, 1).map_err(|d| with_path(d, &self.path))?);
        *self.ix.borrow_mut() = Some(i.clone());
        Ok(i)
    }
}

struct Embed {
    line: usize,
    path: String,
    frag: Option<String>,
    dline: u32,
    dcol: u32,
}

const PUNCT: &str = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~";

/// Embeds `![[inner]]` in a leaf's text, outside code spans, escapes and inline HTML.
fn find_embeds(s: &[char]) -> Vec<(usize, usize, String)> {
    let n = s.len();
    let mut out = Vec::new();
    let mut i = 0;
    while i < n {
        let ch = s[i];
        if ch == '\\' {
            i += if i + 1 < n && PUNCT.contains(s[i + 1]) { 2 } else { 1 };
        } else if ch == '`' {
            let mut j = i;
            while j < n && s[j] == '`' {
                j += 1;
            }
            let run = j - i;
            let mut k = j;
            let mut end: Option<usize> = None;
            while k < n {
                if s[k] == '`' {
                    let mut m = k;
                    while m < n && s[m] == '`' {
                        m += 1;
                    }
                    if m - k == run {
                        end = Some(m);
                        break;
                    }
                    k = m;
                } else {
                    k += 1;
                }
            }
            i = end.unwrap_or(j);
        } else if ch == '<' {
            i = html_inline_end(s, i, n).unwrap_or(i + 1);
        } else if ch == '!' && i + 2 < n && s[i + 1] == '[' && s[i + 2] == '[' {
            let mut j = i + 3;
            while j < n && s[j] != '[' && s[j] != ']' && s[j] != '\n' {
                j += 1;
            }
            if j + 1 < n && s[j] == ']' && s[j + 1] == ']' {
                out.push((i, j + 2, s[i + 3..j].iter().collect()));
                i = j + 2;
            } else {
                i += 1;
            }
        } else {
            i += 1;
        }
    }
    out
}

fn leaf_embed(src: &Src, leaf: &Leaf) -> Res<Option<Embed>> {
    if !matches!(leaf.kind, Kind::Paragraph | Kind::Heading) {
        return Ok(None);
    }
    let mut s: Vec<char> = Vec::new();
    let mut origin: Vec<usize> = Vec::new();
    for (k, &(x, y)) in leaf.segments.iter().enumerate() {
        if k > 0 {
            s.push('\n');
            origin.push(usize::MAX);
        }
        s.extend_from_slice(&src.c[x..y]);
        origin.extend(x..y);
    }
    let found = find_embeds(&s);
    let Some((st, en, inner)) = found.first().cloned() else {
        return Ok(None);
    };
    let off = origin[st];
    let li = src.starts.partition_point(|&x| x <= off) - 1;
    let (line, col) = ((src.body_line + li) as u32, (off - src.starts[li] + 1) as u32);
    let bad = |msg: &str| Diagnostic::new(Code::InvalidSyntax, msg).at(&src.path, line, col);
    let ws = |x: &char| *x == ' ' || *x == '\t';
    let alone = found.len() == 1
        && s[..st].iter().all(ws)
        && s[en..].iter().all(ws)
        && leaf.end_line - leaf.first_line == 1
        && leaf.segments.first().map(|x| x.0) == Some(src.starts[leaf.first_line]);
    if !alone {
        return Err(bad("![[...]] must stand alone in its own paragraph at column 1 (not inside a quote, list or heading)"));
    }
    if inner.is_empty() || inner.contains('|') {
        return Err(bad("an embed takes a path with an optional #Heading or #^id (no label)"));
    }
    let (path, frag) = match inner.split_once('#') {
        Some((p, f)) => (p.to_string(), Some(f.to_string())),
        None => (inner.clone(), None),
    };
    if path.is_empty() || frag.as_deref() == Some("") {
        return Err(bad("an embed needs a path and, after #, a non-empty fragment"));
    }
    Ok(Some(Embed { line: leaf.first_line, path, frag, dline: line, dcol: col }))
}

struct Ctx<'a> {
    read: &'a mut dyn FnMut(&str) -> Res<Option<String>>,
    cache: HashMap<String, Rc<Src>>,
    limits: Limits,
    expansions: usize,
    stack: Vec<(String, usize, usize)>,
    out: String,
    out_lines: usize,
    map: Vec<Segment>,
}

impl<'a> Ctx<'a> {
    fn check_size(&self) -> Res<()> {
        if self.out.len() > self.limits.max_bytes {
            return Err(Diagnostic::new(
                Code::ResourceLimit,
                format!("expanded output is larger than {} bytes", self.limits.max_bytes),
            ));
        }
        Ok(())
    }

    fn load(&mut self, path: &str, who: &Src, e: &Embed) -> Res<Rc<Src>> {
        if let Some(s) = self.cache.get(path) {
            return Ok(s.clone());
        }
        let Some(text) = (self.read)(path)? else {
            return Err(Diagnostic::new(Code::MissingImport, format!("cannot find {:?}", path))
                .at(&who.path, e.dline, e.dcol));
        };
        if text.len() > MAX_INPUT {
            return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB").at(path, 1, 1));
        }
        let s = Rc::new(Src::new(path, &text)?);
        self.cache.insert(path.to_string(), s.clone());
        Ok(s)
    }

    fn emit(&mut self, src: &Src, x: usize, y: usize) -> Res<()> {
        if x >= y {
            return Ok(());
        }
        let end = if y < src.n() { src.starts[y] } else { src.c.len() };
        if src.starts[x] < end {
            self.out.extend(src.c[src.starts[x]..end].iter());
            self.check_size()?;
        }
        let phantom = (y == src.n() && src.ends[y - 1] == src.starts[y - 1]) as usize;
        let lines = (y - x).saturating_sub(phantom);
        if lines == 0 {
            return Ok(());
        }
        let sl = src.body_line + x;
        if let Some(m) = self.map.last_mut() {
            if m.path == src.path && m.out_end == self.out_lines && m.src_start + (m.out_end - m.out_start) == sl {
                m.out_end += lines;
                self.out_lines += lines;
                return Ok(());
            }
        }
        self.map.push(Segment { out_start: self.out_lines, out_end: self.out_lines + lines, path: src.path.clone(), src_start: sl });
        self.out_lines += lines;
        Ok(())
    }

    fn expand(&mut self, src: &Src, a: usize, b: usize) -> Res<()> {
        let mut p = a;
        for leaf in &src.leaves {
            if !(a <= leaf.first_line && leaf.first_line < b) {
                continue;
            }
            let Some(e) = leaf_embed(src, leaf)? else {
                continue;
            };
            self.emit(src, p, e.line)?;
            self.embed(src, &e)?;
            p = e.line + 1;
        }
        self.emit(src, p, b)
    }

    fn embed(&mut self, src: &Src, e: &Embed) -> Res<()> {
        self.expansions += 1;
        if self.expansions > self.limits.max_expansions {
            return Err(Diagnostic::new(
                Code::ResourceLimit,
                format!("more than {} imports expanded", self.limits.max_expansions),
            )
            .at(&src.path, e.dline, e.dcol));
        }
        if self.stack.len() >= self.limits.max_depth {
            return Err(Diagnostic::new(
                Code::ResourceLimit,
                format!("imports nested deeper than {}", self.limits.max_depth),
            )
            .at(&src.path, e.dline, e.dcol));
        }
        let dir = src.path.rsplit_once('/').map(|x| x.0).unwrap_or("");
        let target = path_normalise(dir, &e.path).map_err(|d| locate(d, &src.path, e.dline, e.dcol))?;
        let t = self.load(&target, src, e)?;
        let (ra, rb) = match &e.frag {
            None => (0, t.n()),
            Some(f) => {
                let ix = t.index()?;
                let r = resolve_in(&ix, f).map_err(|d| locate(d, &src.path, e.dline, e.dcol))?;
                match (&r.heading, f.starts_with('^')) {
                    (Some(h), false) => (h.start_line, h.section_end),
                    _ => (r.start_line, r.end_line),
                }
            }
        };
        let key = (target.clone(), ra, rb);
        if self.stack.contains(&key) {
            let mut chain: Vec<&str> = self.stack.iter().map(|f| f.0.as_str()).collect();
            chain.push(&target);
            return Err(Diagnostic::new(Code::ImportCycle, format!("import cycle: {}", chain.join(" -> ")))
                .at(&src.path, e.dline, e.dcol));
        }
        self.stack.push(key);
        let before = self.out.len();
        self.expand(&t, ra, rb)?;
        self.stack.pop();
        if self.out.len() > before && !self.out.ends_with(&['\n', '\r'][..]) {
            let e1 = if e.line + 1 < src.n() { src.starts[e.line + 1] } else { src.c.len() };
            self.out.extend(src.c[src.ends[e.line]..e1].iter());
            self.check_size()?;
        }
        Ok(())
    }
}

/// Expand every embed in `text` (the file at root-relative `path`). `read` returns the text of a
/// root-relative path, or `None` when the file does not exist.
pub fn import_expand(
    text: &str,
    path: &str,
    read: &mut dyn FnMut(&str) -> Res<Option<String>>,
    limits: Limits,
) -> Res<Expanded> {
    if text.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let root = path_normalise("", path)?;
    let rs = Rc::new(Src::new(&root, text)?);
    let mut ctx = Ctx {
        read,
        cache: HashMap::new(),
        limits,
        expansions: 0,
        stack: vec![(root.clone(), 0, rs.n())],
        out: String::new(),
        out_lines: 0,
        map: Vec::new(),
    };
    ctx.cache.insert(root, rs.clone());
    ctx.expand(&rs, 0, rs.n())?;
    Ok(Expanded { text: ctx.out, map: ctx.map })
}
