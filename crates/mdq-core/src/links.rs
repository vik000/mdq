//! Link rebasing (spec section 3.1, last paragraph; G36 in docs/TEST_PLAN.md).
//!
//! A relative destination written in a file of directory `from` is rewritten so that it still
//! reaches the same file from output placed in directory `to`. Directories are root-relative
//! (`""` is the root). Absolute URLs, `scheme:`, `//host`, `/abs`, `#fragment` and `?query`
//! destinations are never touched; query and fragment are kept; a result outside the root is
//! `MDQ_SECURITY_DENIED`.

use crate::blocks::{line_bounds, parse, Kind, Leaf};
use crate::diag::{Code, Diagnostic};
use crate::headings::{entity_at, link_parts, u_ws};
use crate::paths::dir_segments;
use crate::vars::html_inline_end;

type Res<T> = Result<T, Diagnostic>;

const MAX_INPUT: usize = 16 * 1024 * 1024;

fn has_scheme(d: &str) -> bool {
    let mut ch = d.chars();
    match ch.next() {
        Some(f) if f.is_ascii_alphabetic() => {}
        _ => return false,
    }
    for x in ch {
        if x == ':' {
            return true;
        }
        if !(x.is_ascii_alphanumeric() || x == '+' || x == '.' || x == '-') {
            return false;
        }
    }
    false
}

fn rebase_dest(dest: &str, f: &[String], t: &[String]) -> Res<String> {
    if dest.is_empty() || dest.starts_with(['#', '?', '/']) || has_scheme(dest) {
        return Ok(dest.to_string());
    }
    let cut = dest.find(['?', '#']).unwrap_or(dest.len());
    let (path, suffix) = dest.split_at(cut);
    if path.is_empty() {
        return Ok(dest.to_string());
    }
    let trailing = path.ends_with('/');
    let mut stack: Vec<String> = f.to_vec();
    for g in path.split('/') {
        if g.is_empty() || g == "." {
            continue;
        }
        if g == ".." {
            if stack.pop().is_none() {
                return Err(Diagnostic::new(Code::SecurityDenied, format!("link {:?} leaves the root", dest)));
            }
        } else {
            stack.push(g.to_string());
        }
    }
    let mut k = 0;
    while k < stack.len() && k < t.len() && stack[k] == t[k] {
        k += 1;
    }
    let mut parts: Vec<&str> = vec![".."; t.len() - k];
    parts.extend(stack[k..].iter().map(|s| s.as_str()));
    let mut out = parts.join("/");
    if out.is_empty() {
        out.push('.');
    }
    if trailing && !out.ends_with('/') {
        out.push('/');
    }
    if out.split('/').next().unwrap_or("").contains(':') {
        out = format!("./{}", out);
    }
    out.push_str(suffix);
    Ok(out)
}

/// Rebase one decoded destination.
pub fn rebase_link(dest: &str, from_dir: &str, to_dir: &str) -> Res<String> {
    let f = dir_segments(from_dir, "from_dir")?;
    let t = dir_segments(to_dir, "to_dir")?;
    rebase_dest(dest, &f, &t)
}

fn decode_dest(raw: &[char]) -> String {
    let mut out = String::new();
    let n = raw.len();
    let mut i = 0;
    while i < n {
        if raw[i] == '\\' && i + 1 < n && raw[i + 1].is_ascii_punctuation() {
            out.push(raw[i + 1]);
            i += 2;
        } else if raw[i] == '&' {
            match entity_at(raw, i, n) {
                Some((t, e)) => {
                    out.push_str(&t);
                    i = e;
                }
                None => {
                    out.push('&');
                    i += 1;
                }
            }
        } else {
            out.push(raw[i]);
            i += 1;
        }
    }
    out
}

fn encode_dest(d: &str, angle: bool) -> String {
    let chars: Vec<char> = d.chars().collect();
    let special = d.is_empty()
        || chars.iter().any(|&ch| u_ws(ch) || (ch as u32) < 0x20 || ch as u32 == 0x7f || "<>()\\".contains(ch));
    let use_angle = angle || special;
    let mut out = String::new();
    for (i, &ch) in chars.iter().enumerate() {
        if ch == '&' && entity_at(&chars, i, chars.len()).is_some() {
            out.push_str("\\&");
        } else if use_angle && "<>\\".contains(ch) {
            out.push('\\');
            out.push(ch);
        } else {
            out.push(ch);
        }
    }
    if use_angle {
        format!("<{}>", out)
    } else {
        out
    }
}

fn code_skip(s: &[char], i: usize, n: usize) -> usize {
    let mut j = i;
    while j < n && s[j] == '`' {
        j += 1;
    }
    let run = j - i;
    let mut k = j;
    while k < n {
        if s[k] == '`' {
            let mut m = k;
            while m < n && s[m] == '`' {
                m += 1;
            }
            if m - k == run {
                return m;
            }
            k = m;
        } else {
            k += 1;
        }
    }
    j
}

/// Inline link and image destinations in a leaf's text: (start, end, angle), the range inside any `<>`.
fn leaf_dests(s: &[char]) -> Vec<(usize, usize, bool)> {
    let n = s.len();
    let mut out = Vec::new();
    let mut stack: Vec<(bool, bool)> = Vec::new();
    let mut i = 0;
    while i < n {
        let ch = s[i];
        if ch == '\\' {
            i += if i + 1 < n && s[i + 1].is_ascii_punctuation() { 2 } else { 1 };
        } else if ch == '`' {
            i = code_skip(s, i, n);
        } else if ch == '<' {
            i = html_inline_end(s, i, n).unwrap_or(i + 1);
        } else if ch == '[' {
            stack.push((false, true));
            i += 1;
        } else if ch == '!' && i + 1 < n && s[i + 1] == '[' {
            stack.push((true, true));
            i += 2;
        } else if ch == ']' {
            let Some(&(img, active)) = stack.last() else {
                i += 1;
                continue;
            };
            if !active {
                stack.pop();
                i += 1;
                continue;
            }
            match link_parts(s, i + 1, n) {
                None => {
                    stack.pop();
                    i += 1;
                }
                Some(p) => {
                    out.push((p.dest.0, p.dest.1, p.angle));
                    if !img {
                        for ob in stack.iter_mut() {
                            if !ob.0 {
                                ob.1 = false;
                            }
                        }
                    }
                    stack.pop();
                    i = p.end;
                }
            }
        } else {
            i += 1;
        }
    }
    out
}

/// Destination range of a one-line link reference definition `[label]: dest "title"`.
fn def_dest(line: &[char]) -> Option<(usize, usize, bool)> {
    let n = line.len();
    if n == 0 || line[0] != '[' {
        return None;
    }
    let ws = |x: char| x == ' ' || x == '\t';
    let mut i = 1;
    while i < n && line[i] != ']' {
        if line[i] == '[' {
            return None;
        }
        i += if line[i] == '\\' && i + 1 < n { 2 } else { 1 };
    }
    if i >= n || i == 1 || line[1] == '^' || i + 1 >= n || line[i + 1] != ':' {
        return None;
    }
    i += 2;
    while i < n && ws(line[i]) {
        i += 1;
    }
    if i >= n {
        return None;
    }
    let rng;
    if line[i] == '<' {
        let mut j = i + 1;
        while j < n && line[j] != '<' && line[j] != '>' {
            j += if line[j] == '\\' && j + 1 < n { 2 } else { 1 };
        }
        if j >= n || line[j] != '>' {
            return None;
        }
        rng = (i + 1, j, true);
        i = j + 1;
    } else {
        let mut j = i;
        while j < n && !u_ws(line[j]) && (line[j] as u32) >= 0x20 {
            j += 1;
        }
        if j == i {
            return None;
        }
        rng = (i, j, false);
        i = j;
    }
    let mut k = i;
    while k < n && ws(line[k]) {
        k += 1;
    }
    if k == n {
        return Some(rng);
    }
    if k == i || !matches!(line[k], '"' | '\'' | '(') {
        return None;
    }
    let closer = if line[k] == '(' { ')' } else { line[k] };
    let mut j = k + 1;
    while j < n && line[j] != closer {
        if line[j] == '(' && closer == ')' {
            return None;
        }
        j += if line[j] == '\\' && j + 1 < n { 2 } else { 1 };
    }
    if j >= n {
        return None;
    }
    j += 1;
    while j < n && ws(line[j]) {
        j += 1;
    }
    if j == n {
        Some(rng)
    } else {
        None
    }
}

#[derive(Debug, Clone)]
pub(crate) struct Edit {
    pub start: usize,
    pub end: usize,
    pub text: String,
}

/// Edits (over source char offsets) for the links of paragraph and heading leaves whose first
/// line is in `lo..hi`. `base` is the line number of line 0 (for diagnostics).
#[allow(clippy::too_many_arguments)]
pub(crate) fn leaf_edits(
    c: &[char],
    starts: &[usize],
    leaves: &[Leaf],
    lo: usize,
    hi: usize,
    f: &[String],
    t: &[String],
    base: u32,
) -> Res<Vec<Edit>> {
    let mut edits: Vec<Edit> = Vec::new();
    for leaf in leaves {
        let paragraph = match leaf.kind {
            Kind::Paragraph => true,
            Kind::Heading => false,
            _ => continue,
        };
        if !(lo <= leaf.first_line && leaf.first_line < hi) {
            continue;
        }
        let mut s: Vec<char> = Vec::new();
        let mut origin: Vec<usize> = Vec::new();
        for (k, &(x, y)) in leaf.segments.iter().enumerate() {
            if k > 0 {
                s.push('\n');
                origin.push(usize::MAX);
            }
            s.extend_from_slice(&c[x..y]);
            origin.extend(x..y);
        }
        let mut found = leaf_dests(&s);
        if paragraph {
            let mut pos = 0;
            for &(x, y) in &leaf.segments {
                match def_dest(&c[x..y]) {
                    None => break,
                    Some(d) => found.push((pos + d.0, pos + d.1, d.2)),
                }
                pos += (y - x) + 1;
            }
        }
        found.sort();
        found.dedup();
        for (ds, de, ang) in found {
            let raw = &s[ds..de];
            if raw.is_empty() || origin[ds..de].contains(&usize::MAX) {
                continue;
            }
            let old = decode_dest(raw);
            let new = match rebase_dest(&old, f, t) {
                Ok(v) => v,
                Err(mut e) => {
                    let off = origin[ds];
                    let li = starts.partition_point(|&x| x <= off) - 1;
                    e.line = Some(base + li as u32);
                    e.column = Some((off - starts[li] + 1) as u32);
                    return Err(e);
                }
            };
            if new != old {
                let extra = ang as usize;
                edits.push(Edit { start: origin[ds] - extra, end: origin[de - 1] + 1 + extra, text: encode_dest(&new, ang) });
            }
        }
    }
    edits.sort_by(|a, b| a.start.cmp(&b.start).then(a.end.cmp(&b.end)));
    Ok(edits)
}

/// The text of `c[a..b]` with the edits that lie inside it applied.
pub(crate) fn apply_edits(c: &[char], a: usize, b: usize, edits: &[Edit]) -> String {
    let mut out = String::new();
    let mut p = a;
    for e in edits {
        if e.start < a || e.end > b {
            continue;
        }
        out.extend(c[p..e.start].iter());
        out.push_str(&e.text);
        p = e.end;
    }
    out.extend(c[p..b].iter());
    out
}

/// Rebase every link, image and one-line reference definition in `text`.
pub fn rebase_links(text: &str, from_dir: &str, to_dir: &str, first_line: u32) -> Res<String> {
    let f = dir_segments(from_dir, "from_dir")?;
    let t = dir_segments(to_dir, "to_dir")?;
    if text.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    if f == t {
        return Ok(text.to_string());
    }
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let edits = leaf_edits(&c, &starts, &leaves, 0, starts.len(), &f, &t, first_line)?;
    Ok(apply_edits(&c, 0, c.len(), &edits))
}
