//! Plain-Markdown fallbacks for rich constructs: highlights, callouts, details, columns
//! (spec section 7; G37 in docs/TEST_PLAN.md).

use crate::blocks::{line_bounds, parse, Kind};
use crate::containers::{container_parse, Container};
use crate::diag::{Code, Diagnostic};
use crate::headings::strip_inline_marks;
use crate::refs::heading_level;
use crate::vars::escape_inline;
use std::collections::HashSet;

type Res<T> = Result<T, Diagnostic>;

const MAX_INPUT: usize = 16 * 1024 * 1024;

fn check(text: &str) -> Res<()> {
    if text.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    Ok(())
}

/// A leaf's text (segments joined by LF) and, for each char, its source offset (`usize::MAX` for LF).
fn leaf_text(c: &[char], segs: &[(usize, usize)]) -> (Vec<char>, Vec<usize>) {
    let mut s = Vec::new();
    let mut origin = Vec::new();
    for (k, &(x, y)) in segs.iter().enumerate() {
        if k > 0 {
            s.push('\n');
            origin.push(usize::MAX);
        }
        s.extend_from_slice(&c[x..y]);
        origin.extend(x..y);
    }
    (s, origin)
}

/// Remove the delimiters of `==highlight==` (same rules as `~~`); text is preserved.
pub fn highlight_fallback(text: &str, first_line: u32) -> Res<String> {
    check(text)?;
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let mut dels: HashSet<usize> = HashSet::new();
    for leaf in &leaves {
        if !matches!(leaf.kind, Kind::Paragraph | Kind::Heading) {
            continue;
        }
        let (a, b) = (leaf.first_line, leaf.end_line);
        if !c[starts[a]..ends[b - 1]].contains(&'=') {
            continue;
        }
        let (s, origin) = leaf_text(&c, &leaf.segments);
        let ss: String = s.iter().collect();
        let mut marks: Vec<(usize, usize)> = Vec::new();
        let before = strip_inline_marks(&ss, &mut marks);
        if marks.is_empty() {
            continue;
        }
        let mut local: HashSet<usize> = HashSet::new();
        for &(p, q) in &marks {
            local.extend([p, p + 1, q, q + 1]);
        }
        let s2: String = s.iter().enumerate().filter(|(i, _)| !local.contains(i)).map(|(_, ch)| *ch).collect();
        let after = strip_inline_marks(&s2, &mut Vec::new());
        if after != before {
            let col = leaf.segments.first().map(|x| x.0).unwrap_or(starts[a]) - starts[a] + 1;
            return Err(Diagnostic::new(
                Code::UnsupportedFeature,
                "removing the == delimiters would change how neighbouring * or _ emphasis is read; separate them with a space or escape them",
            )
            .at_position(first_line + a as u32, col as u32));
        }
        dels.extend(local.iter().map(|&x| origin[x]));
    }
    Ok(c.iter().enumerate().filter(|(i, _)| !dels.contains(i)).map(|(_, ch)| *ch).collect())
}

fn callout_label(t: &str) -> Option<&'static str> {
    match t.to_ascii_uppercase().as_str() {
        "NOTE" => Some("Note"),
        "TIP" => Some("Tip"),
        "IMPORTANT" => Some("Important"),
        "WARNING" => Some("Warning"),
        "CAUTION" => Some("Caution"),
        _ => None,
    }
}

fn quote_depth(c: &[char], start: usize, end: usize) -> usize {
    let mut n = 0;
    let mut i = start;
    while i < end && matches!(c[i], ' ' | '\t' | '>') {
        if c[i] == '>' {
            n += 1;
        }
        i += 1;
    }
    n
}

/// A quote whose first line is `[!TYPE]` alone becomes a quote with a bold label line.
pub fn callout_fallback(text: &str, first_line: u32) -> Res<String> {
    check(text)?;
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let mut edits: Vec<(usize, usize, String)> = Vec::new();
    for leaf in &leaves {
        if leaf.kind != Kind::Paragraph {
            continue;
        }
        let a = leaf.first_line;
        let segs = &leaf.segments;
        let (x0, y0) = segs[0];
        let seg = &c[x0..y0];
        // `[!` letters `]` then only blanks
        if seg.len() < 4 || seg[0] != '[' || seg[1] != '!' {
            continue;
        }
        let mut j = 2;
        while j < seg.len() && seg[j].is_ascii_alphabetic() {
            j += 1;
        }
        if j == 2 || j >= seg.len() || seg[j] != ']' || !seg[j + 1..].iter().all(|&x| x == ' ' || x == '\t') {
            continue;
        }
        let ty: String = seg[2..j].iter().collect();
        let prefix = &c[starts[a]..x0];
        let depth = prefix.iter().filter(|&&x| x == '>').count();
        let last = prefix.iter().rev().find(|&&x| x != ' ' && x != '\t');
        if depth < 1 || last != Some(&'>') {
            continue;
        }
        if a > 0 && quote_depth(&c, starts[a - 1], ends[a - 1]) >= depth {
            continue;
        }
        let Some(label) = callout_label(&ty) else {
            return Err(Diagnostic::new(Code::UnsupportedFeature, format!("unknown callout type [!{}]", ty))
                .at_position(first_line + a as u32, (x0 - starts[a] + 1) as u32));
        };
        edits.push((x0, y0, format!("**{}**", label)));
        if segs.len() > 1 && c[starts[a + 1]..segs[1].0].iter().filter(|&&x| x == '>').count() >= depth {
            let blank: String = prefix.iter().map(|&x| if matches!(x, '>' | ' ' | '\t') { x } else { ' ' }).collect();
            let blank = blank.trim_end_matches([' ', '\t']).to_string();
            let eol: String = c[ends[a]..starts[a + 1]].iter().collect();
            edits.push((starts[a + 1], starts[a + 1], format!("{}{}", blank, eol)));
        }
    }
    edits.sort_by(|x, y| (x.0, x.1).cmp(&(y.0, y.1)));
    let mut out = String::new();
    let mut p = 0;
    for (x, y, t) in &edits {
        out.extend(c[p..*x].iter());
        out.push_str(t);
        p = *y;
    }
    out.extend(c[p..].iter());
    Ok(out)
}

fn ends_blank(t: &str) -> bool {
    let e = if t.ends_with("\r\n") {
        t.len() - 2
    } else if t.ends_with('\n') || t.ends_with('\r') {
        t.len() - 1
    } else {
        return false;
    };
    let rest = t[..e].trim_end_matches([' ', '\t']);
    rest.ends_with('\n') || rest.ends_with('\r')
}

fn rest_nonblank(c: &[char], a: usize, b: usize) -> bool {
    let mut i = a;
    while i < b && (c[i] == ' ' || c[i] == '\t') {
        i += 1;
    }
    i < b && c[i] != '\n' && c[i] != '\r'
}

#[derive(Clone, Copy, PartialEq)]
enum Mode {
    Details,
    Columns,
}

struct Walker {
    c: Vec<char>,
    starts: Vec<usize>,
    ends: Vec<usize>,
    heads: Vec<(usize, usize)>,
    mode: Mode,
}

impl Walker {
    fn names(&self, n: &str) -> bool {
        match self.mode {
            Mode::Details => n == "details",
            Mode::Columns => n == "columns" || n == "column",
        }
    }

    fn eol_of(&self, off: usize) -> String {
        let li = self.starts.partition_point(|&x| x <= off) - 1;
        let e1 = if li + 1 < self.starts.len() { self.starts[li + 1] } else { self.c.len() };
        self.c[self.ends[li]..e1].iter().collect()
    }

    fn replacement(&self, k: &Container, eol: &str, scope_start: usize, scope_level: Option<usize>) -> Res<String> {
        if self.mode == Mode::Columns {
            return self.emit(k.body_start, k.body_end, &k.children, scope_start, scope_level);
        }
        let base = self
            .heads
            .iter()
            .rev()
            .find(|(off, _)| scope_start <= *off && *off < k.start)
            .map(|x| x.1)
            .or(scope_level)
            .unwrap_or(1);
        let level = (base + 1).min(6);
        let raw = k.attrs.iter().rev().find(|(n, _)| n == "title").map(|x| x.1.as_str()).unwrap_or("");
        let collapsed = raw.split_whitespace().collect::<Vec<_>>().join(" ");
        let title = if collapsed.is_empty() { "Details".to_string() } else { collapsed };
        let body = self.emit(k.body_start, k.body_end, &k.children, k.body_start, Some(level))?;
        let mut out = format!("{} {}{}", "#".repeat(level), escape_inline(&title, true)?, eol);
        if !body.is_empty() {
            out.push_str(eol);
            out.push_str(&body);
        }
        Ok(out)
    }

    fn emit(&self, a: usize, b: usize, kids: &[Container], scope_start: usize, scope_level: Option<usize>) -> Res<String> {
        let mut out = String::new();
        let mut p = a;
        for k in kids {
            out.extend(self.c[p..k.start].iter());
            if self.names(&k.name) {
                let eol = self.eol_of(k.start);
                let repl = self.replacement(k, &eol, scope_start, scope_level)?;
                if !repl.is_empty() && !out.is_empty() && !ends_blank(&out) {
                    out.push_str(&eol);
                }
                out.push_str(&repl);
                if rest_nonblank(&self.c, k.end, b) && !repl.is_empty() && !ends_blank(&repl) {
                    out.push_str(&eol);
                }
            } else {
                out.extend(self.c[k.start..k.body_start].iter());
                out.push_str(&self.emit(k.body_start, k.body_end, &k.children, scope_start, scope_level)?);
                out.extend(self.c[k.body_end..k.end].iter());
            }
            p = k.end;
        }
        out.extend(self.c[p..b].iter());
        Ok(out)
    }
}

fn walk(text: &str, first_line: u32, mode: Mode) -> Res<String> {
    check(text)?;
    let tree = container_parse(text, first_line)?;
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let heads = leaves
        .iter()
        .filter(|l| l.kind == Kind::Heading)
        .map(|l| (starts[l.first_line], heading_level(&c, &starts, &ends, l.first_line, l.end_line)))
        .collect();
    let w = Walker { c, starts, ends, heads, mode };
    let n = w.c.len();
    w.emit(0, n, &tree, 0, None)
}

/// `:::details {title="T"}` becomes a heading one level below the nearest preceding heading
/// (at most 6; level 2 when there is none) followed by the body.
pub fn details_fallback(text: &str, first_line: u32) -> Res<String> {
    walk(text, first_line, Mode::Details)
}

/// `columns` and `column` containers are removed; their bodies stay in source order.
pub fn columns_fallback(text: &str, first_line: u32) -> Res<String> {
    walk(text, first_line, Mode::Columns)
}
