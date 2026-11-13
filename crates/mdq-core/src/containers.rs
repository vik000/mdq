//! Attribute lists and fenced containers (spec section 6; G30 in docs/TEST_PLAN.md).
//!
//! `{key="value" ...}` attribute lists and `:::name {attrs}` ... `:::` containers. Fence lines
//! start at column zero, outside code and HTML blocks; a closer has exactly as many colons as
//! its opener and a nested container uses more colons than its parent.

use crate::blocks::{self, Kind};
use crate::diag::{Code, Diagnostic};
use crate::vars::{self, Scope};
use crate::yaml::Yaml;

const MAX_INPUT: usize = 16 * 1024 * 1024;
pub const MAX_ATTRS: usize = 64;
pub const MAX_DEPTH: usize = 64;

type Res<T> = Result<T, Diagnostic>;

fn syntax(msg: impl Into<String>, line: u32, col: u32) -> Diagnostic {
    Diagnostic::new(Code::InvalidSyntax, msg).at_position(line, col)
}

fn is_name_char(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || ch == '_' || ch == '-'
}

/// Parse `{key="value" ...}` starting at `c[i] == '{'`, ending before `e`. `pos` maps a char
/// index to (line, column). Returns the pairs in source order and the index after `}`.
fn attrs_at(
    c: &[char],
    i: usize,
    e: usize,
    pos: &dyn Fn(usize) -> (u32, u32),
) -> Res<(Vec<(String, String)>, usize)> {
    let bad = |idx: usize, msg: String| {
        let (l, cc) = pos(idx);
        syntax(msg, l, cc)
    };
    let mut out: Vec<(String, String)> = Vec::new();
    let mut i = i + 1;
    loop {
        let j = i;
        while i < e && (c[i] == ' ' || c[i] == '\t') {
            i += 1;
        }
        if i >= e {
            return Err(bad(i, "unterminated attribute list: expected }".to_string()));
        }
        if c[i] == '}' {
            return Ok((out, i + 1));
        }
        if !out.is_empty() && i == j {
            return Err(bad(i, "attributes must be separated by white space".to_string()));
        }
        let ks = i;
        if !(c[i].is_ascii_alphabetic() || c[i] == '_') {
            return Err(bad(i, "expected an attribute name".to_string()));
        }
        i += 1;
        while i < e && is_name_char(c[i]) {
            i += 1;
        }
        let key: String = c[ks..i].iter().collect();
        if i >= e || c[i] != '=' {
            return Err(bad(i, format!("expected = after attribute name \"{}\"", key)));
        }
        i += 1;
        if i >= e || c[i] != '"' {
            return Err(bad(i, format!("the value of \"{}\" must be a double-quoted string", key)));
        }
        i += 1;
        let mut val = String::new();
        loop {
            if i >= e {
                return Err(bad(i, format!("unterminated string value for \"{}\"", key)));
            }
            let ch = c[i];
            if ch == '"' {
                i += 1;
                break;
            }
            if ch == '\\' {
                if i + 1 < e && (c[i + 1] == '"' || c[i + 1] == '\\') {
                    val.push(c[i + 1]);
                    i += 2;
                    continue;
                }
                return Err(bad(i, "invalid escape: only \\\" and \\\\ are allowed in attribute values".to_string()));
            }
            if (ch < ' ' && ch != '\t') || ch == '\u{7f}' {
                return Err(bad(i, "control characters are not allowed in attribute values".to_string()));
            }
            val.push(ch);
            i += 1;
        }
        if out.iter().any(|(k, _)| *k == key) {
            return Err(bad(ks, format!("duplicate attribute \"{}\"", key)));
        }
        if out.len() >= MAX_ATTRS {
            let (l, cc) = pos(ks);
            return Err(Diagnostic::new(Code::ResourceLimit, format!("more than {} attributes", MAX_ATTRS))
                .at_position(l, cc));
        }
        out.push((key, val));
    }
}

/// Parse a whole attribute list (`{...}` plus optional trailing blanks). `line`/`column` give
/// the position of the first char, for diagnostics.
pub fn attrs_parse(text: &str, line: u32, column: u32) -> Res<Vec<(String, String)>> {
    let c: Vec<char> = text.chars().collect();
    let e = c.len();
    let pos = move |idx: usize| (line, column + idx as u32);
    if e == 0 || c[0] != '{' {
        return Err(syntax("an attribute list must start with {", line, column));
    }
    let (out, mut i) = attrs_at(&c, 0, e, &pos)?;
    while i < e && (c[i] == ' ' || c[i] == '\t') {
        i += 1;
    }
    if i < e {
        let (l, cc) = pos(i);
        return Err(syntax("unexpected text after the attribute list", l, cc));
    }
    Ok(out)
}

/// One container with source offsets (char indices into the parsed text).
#[derive(Debug, Clone)]
pub struct Container {
    pub name: String,
    pub attrs: Vec<(String, String)>,
    pub colons: usize,
    pub line: u32,
    pub close_line: u32,
    pub start: usize,
    pub body_start: usize,
    pub body_end: usize,
    pub end: usize,
    pub children: Vec<Container>,
}

struct Open {
    node: Container,
}

pub fn container_parse(text: &str, first_line: u32) -> Res<Vec<Container>> {
    if text.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = blocks::line_bounds(&c);
    let mut nl = starts.len();
    if nl > 1 && starts[nl - 1] == c.len() {
        nl -= 1;
    }
    let mut skip = vec![false; nl];
    for leaf in blocks::parse(&c, &starts, &ends, first_line)? {
        if matches!(leaf.kind, Kind::CodeFence | Kind::CodeIndented | Kind::Html) {
            for l in leaf.first_line..leaf.end_line.min(nl) {
                skip[l] = true;
            }
        }
    }
    let mut roots: Vec<Container> = Vec::new();
    let mut stack: Vec<Open> = Vec::new();
    for ln in 0..nl {
        if skip[ln] {
            continue;
        }
        let s = starts[ln];
        let e = ends[ln];
        if !(e - s >= 3 && c[s] == ':' && c[s + 1] == ':' && c[s + 2] == ':') {
            continue;
        }
        let line_no = first_line + ln as u32;
        let err = |idx: usize, msg: String| syntax(msg, line_no, (idx - s) as u32 + 1);
        let mut n = 0;
        while s + n < e && c[s + n] == ':' {
            n += 1;
        }
        let mut k = s + n;
        while k < e && (c[k] == ' ' || c[k] == '\t') {
            k += 1;
        }
        if k >= e {
            // closing fence
            let mut top = match stack.pop() {
                Some(t) => t,
                None => return Err(err(s, "closing fence without an open container".to_string())),
            };
            if n != top.node.colons {
                return Err(err(
                    s,
                    format!(
                        "closing fence has {} colons but the open container \"{}\" (line {}) needs {}",
                        n, top.node.name, top.node.line, top.node.colons
                    ),
                ));
            }
            top.node.close_line = line_no;
            top.node.body_end = s;
            top.node.end = if ln + 1 < starts.len() { starts[ln + 1] } else { c.len() };
            match stack.last_mut() {
                Some(parent) => parent.node.children.push(top.node),
                None => roots.push(top.node),
            }
            continue;
        }
        let i = k;
        if !c[i].is_ascii_alphabetic() {
            return Err(err(i, "expected a container name after the colons".to_string()));
        }
        let mut j = i;
        loop {
            j += 1;
            while j < e && is_name_char(c[j]) {
                j += 1;
            }
            if j + 1 < e && c[j] == '.' && c[j + 1].is_ascii_alphabetic() {
                j += 1;
            } else {
                break;
            }
        }
        let name: String = c[i..j].iter().collect();
        if let Some(top) = stack.last() {
            if n <= top.node.colons {
                return Err(err(
                    s,
                    format!("a nested container needs more than {} colons (found {})", top.node.colons, n),
                ));
            }
        }
        if stack.len() >= MAX_DEPTH {
            return Err(Diagnostic::new(
                Code::ResourceLimit,
                format!("containers are nested deeper than {} levels", MAX_DEPTH),
            )
            .at_position(line_no, 1));
        }
        let mut k = j;
        while k < e && (c[k] == ' ' || c[k] == '\t') {
            k += 1;
        }
        let mut attrs: Vec<(String, String)> = Vec::new();
        if k < e {
            if c[k] != '{' {
                return Err(err(k, "expected { or the end of the line after the container name".to_string()));
            }
            let pos = |idx: usize| (line_no, (idx - s) as u32 + 1);
            let (a, after) = attrs_at(&c, k, e, &pos)?;
            attrs = a;
            k = after;
            while k < e && (c[k] == ' ' || c[k] == '\t') {
                k += 1;
            }
            if k < e {
                return Err(err(k, "unexpected text after the attribute list".to_string()));
            }
        }
        stack.push(Open {
            node: Container {
                name,
                attrs,
                colons: n,
                line: line_no,
                close_line: 0,
                start: s,
                body_start: if ln + 1 < starts.len() { starts[ln + 1] } else { c.len() },
                body_end: 0,
                end: 0,
                children: Vec::new(),
            },
        });
    }
    if let Some(top) = stack.last() {
        return Err(syntax(format!("container \"{}\" is not closed", top.node.name), top.node.line, 1));
    }
    Ok(roots)
}

// ---------------------------------------------------------------- evaluation (if / for)

fn valid_name(name: &str, dotted: bool) -> bool {
    let ok_seg = |g: &str| {
        let mut ch = g.chars();
        match ch.next() {
            Some(f) if f.is_ascii_alphabetic() || f == '_' => ch.all(|x| x.is_ascii_alphanumeric() || x == '_'),
            _ => false,
        }
    };
    if dotted {
        name.split('.').all(ok_seg)
    } else {
        ok_seg(name)
    }
}

struct Ev<'t> {
    c: &'t [char],
    starts: &'t [usize],
    first_line: u32,
    iterations: usize,
    max_iterations: usize,
    size: usize,
}

impl<'t> Ev<'t> {
    fn line_of(&self, pos: usize) -> u32 {
        let idx = match self.starts.binary_search(&pos) {
            Ok(i) => i,
            Err(i) => i - 1,
        };
        self.first_line + idx as u32
    }

    fn put(&mut self, out: &mut String, s: &str) -> Res<()> {
        self.size += s.len();
        if self.size > vars::MAX_OUTPUT {
            return Err(Diagnostic::new(Code::ResourceLimit, "evaluated output is too large"));
        }
        out.push_str(s);
        Ok(())
    }

    fn seg(&mut self, a: usize, b: usize, scope: &Scope, out: &mut String) -> Res<()> {
        if a < b {
            let s: String = self.c[a..b].iter().collect();
            let r = vars::interpolate_with(&s, scope, self.line_of(a))?;
            self.put(out, &r)?;
        }
        Ok(())
    }

    fn range<'v>(&mut self, a: usize, b: usize, nodes: &[Container], scope: &mut Scope<'v>, out: &mut String) -> Res<()> {
        let mut pos = a;
        for n in nodes {
            self.seg(pos, n.start, scope, out)?;
            self.node(n, scope, out)?;
            pos = n.end;
        }
        self.seg(pos, b, scope, out)
    }

    fn check<'n>(&self, n: &'n Container, allowed: &[&str]) -> Res<Vec<&'n str>> {
        for (k, _) in &n.attrs {
            if !allowed.contains(&k.as_str()) {
                return Err(syntax(format!("unknown attribute \"{}\" on container \"{}\"", k, n.name), n.line, 1));
            }
        }
        let mut vals = Vec::new();
        for k in allowed {
            match n.attrs.iter().find(|(key, _)| key == k) {
                Some((_, v)) => vals.push(v.as_str()),
                None => {
                    return Err(syntax(format!("container \"{}\" requires the attribute \"{}\"", n.name, k), n.line, 1));
                }
            }
        }
        Ok(vals)
    }

    fn resolve<'v>(&self, n: &Container, key: &str, name: &str, scope: &Scope<'v>) -> Res<&'v Yaml> {
        if !valid_name(name, true) {
            return Err(syntax(
                format!("the \"{}\" attribute must be a dotted variable name, found \"{}\"", key, name),
                n.line,
                1,
            ));
        }
        if scope.is_deferred(name) {
            return Err(Diagnostic::new(
                Code::UnsupportedFeature,
                format!("\"{}\" is a calc result; :::if and :::for cannot use calc results", name),
            )
            .at_position(n.line, 1));
        }
        match scope.get(name) {
            Some(v) => Ok(v),
            None => Err(Diagnostic::new(Code::UndefinedVariable, format!("undefined variable \"{}\"", name))
                .at_position(n.line, 1)),
        }
    }

    fn node<'v>(&mut self, n: &Container, scope: &mut Scope<'v>, out: &mut String) -> Res<()> {
        if n.name == "if" {
            let a = self.check(n, &["var", "equals"])?;
            let v = self.resolve(n, "var", a[0], scope)?;
            let got = match vars::format_scalar(v) {
                Ok(t) => t,
                Err(d) => {
                    return Err(Diagnostic::new(d.code, format!("{} (variable \"{}\")", d.message, a[0]))
                        .at_position(n.line, 1));
                }
            };
            if got == a[1] {
                self.range(n.body_start, n.body_end, &n.children, scope, out)?;
            }
        } else if n.name == "for" {
            let a = self.check(n, &["each", "as"])?;
            let v = self.resolve(n, "each", a[0], scope)?;
            let items = match v {
                Yaml::Seq(items) => items,
                other => {
                    return Err(Diagnostic::new(
                        Code::MetadataError,
                        format!("\"{}\" is not a list (found {})", a[0], other.kind()),
                    )
                    .at_position(n.line, 1));
                }
            };
            if !valid_name(a[1], false) {
                return Err(syntax(
                    format!("the \"as\" attribute must be a plain identifier, found \"{}\"", a[1]),
                    n.line,
                    1,
                ));
            }
            for item in items {
                self.iterations += 1;
                if self.iterations > self.max_iterations {
                    return Err(Diagnostic::new(
                        Code::ResourceLimit,
                        format!("more than {} loop iterations", self.max_iterations),
                    )
                    .at_position(n.line, 1));
                }
                scope.push(a[1], item);
                let r = self.range(n.body_start, n.body_end, &n.children, scope, out);
                scope.pop();
                r?;
            }
        } else {
            let head: String = self.c[n.start..n.body_start].iter().collect();
            self.put(out, &head)?;
            self.range(n.body_start, n.body_end, &n.children, scope, out)?;
            let tail: String = self.c[n.body_end..n.end].iter().collect();
            self.put(out, &tail)?;
        }
        Ok(())
    }
}

/// Resolve `if`/`for` containers and interpolate everything else (see G31).
pub fn container_eval(text: &str, vars_yaml: &Yaml, first_line: u32, max_iterations: usize) -> Res<String> {
    eval_with(text, Scope::new(vars_yaml), first_line, max_iterations)
}

/// `container_eval`, leaving `{{defer.*}}` references in place for a later pass (see `Scope`).
pub fn container_eval_deferred(text: &str, vars_yaml: &Yaml, defer: &str, first_line: u32, max_iterations: usize) -> Res<String> {
    eval_with(text, Scope::deferring(vars_yaml, defer), first_line, max_iterations)
}

fn eval_with(text: &str, mut scope: Scope<'_>, first_line: u32, max_iterations: usize) -> Res<String> {
    let tree = container_parse(text, first_line)?;
    let c: Vec<char> = text.chars().collect();
    let (starts, _ends) = blocks::line_bounds(&c);
    let mut ev = Ev { c: &c, starts: &starts, first_line, iterations: 0, max_iterations, size: 0 };
    let mut out = String::with_capacity(text.len());
    ev.range(0, c.len(), &tree, &mut scope, &mut out)?;
    Ok(out)
}
