//! Maths fallback and footnotes (spec section 7; G38 in docs/TEST_PLAN.md).

use crate::blocks::{line_bounds, parse, Kind, Leaf};
use crate::diag::{Code, Diagnostic};
use crate::headings::{link_tail, u_ws};
use crate::links::{apply_edits, Edit};
use crate::rich::leaf_text;
use crate::vars::html_inline_end;
use std::collections::HashSet;

type Res<T> = Result<T, Diagnostic>;

const MAX_INPUT: usize = 16 * 1024 * 1024;

fn check(text: &str) -> Res<()> {
    if text.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    Ok(())
}

/// Index after the code span starting at `c[i]` (a backtick), or after the opening run if unmatched.
fn skip_code_span(c: &[char], i: usize, n: usize) -> usize {
    let mut j = i;
    while j < n && c[j] == '`' {
        j += 1;
    }
    let run = j - i;
    let mut k = j;
    while k < n {
        if c[k] == '`' {
            let mut m = k;
            while m < n && c[m] == '`' {
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

/// Inline maths spans in leaf text: (open, open_len, close, close_len).
fn math_spans(c: &[char]) -> Vec<(usize, usize, usize, usize)> {
    let n = c.len();
    let mut out = Vec::new();
    let mut i = 0;
    while i < n {
        let ch = c[i];
        if ch == '\\' {
            i += if i + 1 < n && (c[i + 1].is_ascii_punctuation() || c[i + 1] == '\n') { 2 } else { 1 };
        } else if ch == '`' {
            i = skip_code_span(c, i, n);
        } else if ch == '<' {
            i = html_inline_end(c, i, n).unwrap_or(i + 1);
        } else if ch == ']' {
            i = link_tail(c, i + 1, n).unwrap_or(i + 1);
        } else if ch == '$' {
            let mut j = i;
            while j < n && c[j] == '$' {
                j += 1;
            }
            let r = j - i;
            if r > 2 || j >= n || u_ws(c[j]) {
                i = j;
                continue;
            }
            let mut k = j;
            let mut found = None;
            while k < n {
                if c[k] == '\\' && k + 1 < n {
                    k += 2;
                    continue;
                }
                if c[k] == '$' {
                    let mut m = k;
                    while m < n && c[m] == '$' {
                        m += 1;
                    }
                    if m - k == r && !u_ws(c[k - 1]) && !(m < n && c[m].is_ascii_digit()) {
                        found = Some((k, m - k));
                    }
                    break;
                }
                k += 1;
            }
            match found {
                None => i = j,
                Some((q, m)) => {
                    out.push((i, r, q, m));
                    i = q + m;
                }
            }
        } else {
            i += 1;
        }
    }
    out
}

fn bt_runs(chars: &[char]) -> HashSet<usize> {
    let mut runs = HashSet::new();
    let mut k = 0;
    while k < chars.len() {
        if chars[k] == '`' {
            let mut m = k;
            while m < chars.len() && chars[m] == '`' {
                m += 1;
            }
            runs.insert(m - k);
            k = m;
        } else {
            k += 1;
        }
    }
    runs
}

/// Column of `$$` in a line holding only `[ \t>]* $$ [ \t]*`.
fn display_pos(line: &[char]) -> Option<usize> {
    let mut p = 0;
    while p < line.len() && matches!(line[p], ' ' | '\t' | '>') {
        p += 1;
    }
    if p + 2 > line.len() || line[p] != '$' || line[p + 1] != '$' {
        return None;
    }
    line[p + 2..].iter().all(|&x| x == ' ' || x == '\t').then_some(p)
}

/// `$...$` becomes a code span; a `$$` line pair becomes a fenced `math` block.
pub fn math_fallback(text: &str, first_line: u32) -> Res<String> {
    check(text)?;
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let nl = starts.len();
    let leaves = parse(&c, &starts, &ends, 1)?;
    let mut dead = vec![false; nl];
    for leaf in &leaves {
        if matches!(leaf.kind, Kind::CodeFence | Kind::CodeIndented | Kind::Html) {
            for d in dead.iter_mut().take(leaf.end_line.min(nl)).skip(leaf.first_line) {
                *d = true;
            }
        }
    }
    let mut inmath = vec![false; nl];
    let mut edits: Vec<Edit> = Vec::new();
    let mut opener: Option<usize> = None;
    for l in 0..nl {
        let Some(p) = display_pos(&c[starts[l]..ends[l]]) else {
            continue;
        };
        match opener {
            None => {
                if !dead[l] {
                    opener = Some(l);
                }
            }
            Some(op) => {
                let body: Vec<char> = (op + 1..l).flat_map(|k| c[starts[k]..ends[k]].iter().copied()).collect();
                let runs = bt_runs(&body);
                let w = runs.iter().copied().max().unwrap_or(0).max(2) + 1;
                let o = starts[op] + display_pos(&c[starts[op]..ends[op]]).unwrap_or(0);
                let q = starts[l] + p;
                edits.push(Edit { start: o, end: o + 2, text: format!("{}math", "`".repeat(w)) });
                edits.push(Edit { start: q, end: q + 2, text: "`".repeat(w) });
                for m in inmath.iter_mut().take(l + 1).skip(op) {
                    *m = true;
                }
                opener = None;
            }
        }
    }
    if let Some(op) = opener {
        let o = display_pos(&c[starts[op]..ends[op]]).unwrap_or(0);
        return Err(Diagnostic::new(Code::InvalidSyntax, "unclosed $$ display maths")
            .at_position(first_line + op as u32, (o + 1) as u32));
    }
    for leaf in &leaves {
        if !matches!(leaf.kind, Kind::Paragraph | Kind::Heading) {
            continue;
        }
        if !leaf.segments.iter().any(|&(x, y)| c[x..y].contains(&'$')) {
            continue;
        }
        let a = leaf.first_line;
        let mut group: Vec<(usize, usize)> = Vec::new();
        for k in 0..=leaf.segments.len() {
            if k < leaf.segments.len() && !inmath[a + k] {
                group.push(leaf.segments[k]);
                continue;
            }
            if !group.is_empty() {
                let (s, origin) = leaf_text(&c, &group);
                for (o, r, q, m) in math_spans(&s) {
                    let body = &s[o + r..q];
                    let runs = bt_runs(body);
                    let mut w = 1;
                    while runs.contains(&w) {
                        w += 1;
                    }
                    let pad = if body[0] == '`' || body[body.len() - 1] == '`' { " " } else { "" };
                    edits.push(Edit { start: origin[o], end: origin[o] + r, text: format!("{}{}", "`".repeat(w), pad) });
                    edits.push(Edit { start: origin[q], end: origin[q] + m, text: format!("{}{}", pad, "`".repeat(w)) });
                }
                group.clear();
            }
        }
    }
    edits.sort_by(|x, y| x.start.cmp(&y.start).then(x.end.cmp(&y.end)));
    Ok(apply_edits(&c, 0, c.len(), &edits))
}

struct Item {
    def: bool,
    label: String,
    off: usize,
    line: usize,
    col: usize,
}

/// End (exclusive) of `[^label]` at `s[i]`.
fn fn_match(s: &[char], i: usize, n: usize) -> Option<usize> {
    let mut j = i + 2;
    while j < n && !u_ws(s[j]) && s[j] != '[' && s[j] != ']' {
        j += 1;
    }
    (j > i + 2 && j < n && s[j] == ']').then_some(j + 1)
}

/// Footnote definitions and references in paragraph and heading leaves (first line in `lo..hi`).
fn fn_items(c: &[char], starts: &[usize], leaves: &[Leaf], lo: Option<usize>, hi: Option<usize>) -> Vec<Item> {
    let mut out = Vec::new();
    for leaf in leaves {
        if !matches!(leaf.kind, Kind::Paragraph | Kind::Heading) {
            continue;
        }
        let a = leaf.first_line;
        if let (Some(l), Some(h)) = (lo, hi) {
            if !(l <= a && a < h) {
                continue;
            }
        }
        let has = leaf.segments.iter().any(|&(x, y)| c[x..y].windows(2).any(|w| w[0] == '[' && w[1] == '^'));
        if !has {
            continue;
        }
        let (s, origin) = leaf_text(c, &leaf.segments);
        let n = s.len();
        let mut i = 0;
        while i < n {
            let ch = s[i];
            if ch == '\\' {
                i += if i + 1 < n && (s[i + 1].is_ascii_punctuation() || s[i + 1] == '\n') { 2 } else { 1 };
            } else if ch == '`' {
                i = skip_code_span(&s, i, n);
            } else if ch == '<' {
                i = html_inline_end(&s, i, n).unwrap_or(i + 1);
            } else if ch == '[' && i + 1 < n && s[i + 1] == '^' {
                let Some(m) = fn_match(&s, i, n) else {
                    i += 1;
                    continue;
                };
                let def = (i == 0 || s[i - 1] == '\n') && m < n && s[m] == ':';
                let line = a + s[..i].iter().filter(|&&x| x == '\n').count();
                out.push(Item {
                    def,
                    label: s[i + 2..m - 1].iter().collect(),
                    off: origin[i + 2],
                    line,
                    col: origin[i] - starts[line] + 1,
                });
                i = m;
            } else {
                i += 1;
            }
        }
    }
    out
}

/// Labels of the referenced footnotes in order of first reference.
pub fn footnote_check(text: &str, first_line: u32) -> Res<Vec<String>> {
    check(text)?;
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let items = fn_items(&c, &starts, &leaves, None, None);
    let defs: HashSet<&str> = items.iter().filter(|x| x.def).map(|x| x.label.as_str()).collect();
    let mut seen: HashSet<&str> = HashSet::new();
    let mut order: Vec<String> = Vec::new();
    for it in &items {
        let (line, col) = (first_line + it.line as u32, it.col as u32);
        if it.def {
            if !seen.insert(it.label.as_str()) {
                return Err(Diagnostic::new(Code::DuplicateId, format!("footnote [^{}] is defined twice", it.label))
                    .at_position(line, col));
            }
        } else {
            if !defs.contains(it.label.as_str()) {
                return Err(Diagnostic::new(Code::UnresolvedReference, format!("footnote [^{}] has no definition", it.label))
                    .at_position(line, col));
            }
            if !order.contains(&it.label) {
                order.push(it.label.clone());
            }
        }
    }
    Ok(order)
}

fn scope_ok(scope: &str) -> Res<()> {
    if scope.is_empty() || scope.chars().any(|ch| u_ws(ch) || ch == '[' || ch == ']') {
        return Err(Diagnostic::new(Code::InvalidSyntax, "scope must be non-empty without whitespace or brackets"));
    }
    Ok(())
}

pub(crate) fn fn_edits(
    c: &[char],
    starts: &[usize],
    leaves: &[Leaf],
    scope: &str,
    lo: Option<usize>,
    hi: Option<usize>,
) -> Vec<Edit> {
    fn_items(c, starts, leaves, lo, hi)
        .into_iter()
        .map(|it| Edit { start: it.off, end: it.off, text: format!("{}--", scope) })
        .collect()
}

/// `[^id]` becomes `[^scope--id]` for every reference and definition outside code.
pub fn footnote_rename(text: &str, scope: &str) -> Res<String> {
    check(text)?;
    scope_ok(scope)?;
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let edits = fn_edits(&c, &starts, &leaves, scope, None, None);
    Ok(apply_edits(&c, 0, c.len(), &edits))
}

/// Scope of the `n`th (1-based) embedding of `path`: its injectively encoded path, `~n` after the first.
pub(crate) fn fn_scope(path: &str, n: usize) -> String {
    let mut s = String::new();
    for ch in path.chars() {
        if ch.is_ascii_alphanumeric() {
            s.push(ch);
        } else {
            let mut buf = [0u8; 4];
            for b in ch.encode_utf8(&mut buf).bytes() {
                s.push_str(&format!("_{:02x}", b));
            }
        }
    }
    if n > 1 {
        s.push_str(&format!("~{}", n));
    }
    s
}
