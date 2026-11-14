//! Document index (headings, block IDs) and reference resolution (spec sections 2, 3, 3.1; G34).
//!
//! A block ID is a trailing `^id` (`[A-Za-z][A-Za-z0-9_-]*`) after white space at the end of a
//! paragraph or heading, or alone on the last line of a multi-line paragraph. Lines are 0-based
//! and `end_line` is exclusive.

use crate::blocks::{line_bounds, parse, Kind};
use crate::diag::{Code, Diagnostic};
use crate::headings::{atx_strip, collapse_ws, heading_plain_text};
use unicode_normalization::UnicodeNormalization;

type Res<T> = Result<T, Diagnostic>;

const MAX_INPUT: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct Heading {
    pub ordinal: usize,
    pub level: usize,
    pub text: String,
    pub id: Option<String>,
    pub start_line: usize,
    pub end_line: usize,
    pub section_end: usize,
}

#[derive(Debug, Clone)]
pub struct Block {
    pub id: String,
    pub kind: &'static str,
    pub start_line: usize,
    pub end_line: usize,
}

#[derive(Debug, Clone)]
pub struct Resolved {
    pub kind: &'static str,
    pub start_line: usize,
    pub end_line: usize,
    pub id: Option<String>,
    pub heading: Option<Heading>,
}

fn id_start(c: char) -> bool {
    c.is_ascii_alphabetic()
}

fn id_rest(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_' || c == '-'
}

/// Trailing marker of the last text: `(id, char offset of '^')`.
fn marker(texts: &[Vec<char>]) -> Option<(String, usize)> {
    let last = &texts[texts.len() - 1];
    let mut t = last.len();
    while t > 0 && (last[t - 1] == ' ' || last[t - 1] == '\t') {
        t -= 1;
    }
    let mut j = t;
    while j > 0 && id_rest(last[j - 1]) {
        j -= 1;
    }
    if j == 0 || last[j - 1] != '^' || j == t || !id_start(last[j]) {
        return None;
    }
    let k = j - 1;
    if k > 0 && last[k - 1] != ' ' && last[k - 1] != '\t' {
        return None;
    }
    let blank_before = last[..k].iter().all(|&c| c == ' ' || c == '\t');
    if blank_before && texts.len() < 2 {
        return None;
    }
    Some((last[j..t].iter().collect(), k))
}

pub(crate) struct Index {
    pub(crate) headings: Vec<Heading>,
    pub(crate) blocks: Vec<Block>,
    pub(crate) end: usize,
}

pub(crate) fn build(text: &str, first_line: u32) -> Res<Index> {
    build_with(text, first_line, true)
}

/// `build` without the duplicate-ID check: for combined output, where IDs from different source
/// documents may repeat (uniqueness is per source document, spec 3).
pub(crate) fn build_lenient(text: &str) -> Res<Index> {
    build_with(text, 1, false)
}

fn build_with(text: &str, first_line: u32, check_dups: bool) -> Res<Index> {
    if text.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let mut headings: Vec<Heading> = Vec::new();
    let mut blocks: Vec<Block> = Vec::new();
    let mut seen: Vec<(String, u32)> = Vec::new();
    for leaf in &leaves {
        let heading = match leaf.kind {
            Kind::Heading => true,
            Kind::Paragraph => false,
            _ => continue,
        };
        let (a, b) = (leaf.first_line, leaf.end_line);
        let mut texts: Vec<Vec<char>> = leaf.segments.iter().map(|&(s, e)| c[s..e].to_vec()).collect();
        if texts.is_empty() {
            texts.push(Vec::new());
        }
        let atx = heading && b - a == 1;
        if atx {
            let n = texts.len() - 1;
            let s: String = texts[n].iter().collect();
            texts[n] = atx_strip(&s).chars().collect();
        }
        let mut bid: Option<String> = None;
        if let Some((id, cut)) = marker(&texts) {
            let seg_start = leaf.segments.last().map(|x| x.0).unwrap_or(0);
            let off = seg_start + cut;
            let li = starts.partition_point(|&s| s <= off) - 1;
            let line = first_line + li as u32;
            if let Some((_, first)) = seen.iter().find(|(s, _)| *s == id).filter(|_| check_dups) {
                return Err(Diagnostic::new(
                    Code::DuplicateId,
                    format!("duplicate block id ^{} (first at line {})", id, first),
                )
                .at_position(line, (off - starts[li] + 1) as u32));
            }
            seen.push((id.clone(), line));
            let n = texts.len() - 1;
            texts[n].truncate(cut);
            blocks.push(Block {
                id: id.clone(),
                kind: if heading { "heading" } else { "paragraph" },
                start_line: a,
                end_line: b,
            });
            bid = Some(id);
        }
        if heading {
            let level = heading_level(&c, &starts, &ends, a, b);
            let joined: Vec<String> = texts.iter().map(|t| t.iter().collect()).collect();
            let text = heading_plain_text(&joined.join("\n"), false)?;
            headings.push(Heading {
                ordinal: headings.len(),
                level,
                text,
                id: bid,
                start_line: a,
                end_line: b,
                section_end: 0,
            });
        }
    }
    let end = leaves.last().map(|l| l.end_line).unwrap_or(0);
    for i in 0..headings.len() {
        let lv = headings[i].level;
        headings[i].section_end = headings[i + 1..]
            .iter()
            .find(|g| g.level <= lv)
            .map(|g| g.start_line)
            .unwrap_or(end);
    }
    Ok(Index { headings, blocks, end })
}

pub fn doc_index(text: &str, first_line: u32) -> Res<(Vec<Heading>, Vec<Block>)> {
    let i = build(text, first_line)?;
    Ok((i.headings, i.blocks))
}

pub fn resolve_ref(text: &str, fragment: &str, first_line: u32) -> Res<Resolved> {
    if fragment.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let ix = build(text, first_line)?;
    resolve_in(&ix, fragment)
}

pub(crate) fn resolve_in(ix: &Index, fragment: &str) -> Res<Resolved> {
    if fragment.is_empty() {
        return Ok(Resolved { kind: "document", start_line: 0, end_line: ix.end, id: None, heading: None });
    }
    let as_heading = |h: &Heading| Resolved {
        kind: "heading",
        start_line: h.start_line,
        end_line: h.end_line,
        id: h.id.clone(),
        heading: Some(h.clone()),
    };
    if let Some(bid) = fragment.strip_prefix('^') {
        let mut ch = bid.chars();
        let valid = matches!(ch.next(), Some(f) if id_start(f)) && ch.all(id_rest);
        if !valid {
            return Err(Diagnostic::new(Code::InvalidSyntax, format!("invalid block id {:?}", fragment)));
        }
        if let Some(b) = ix.blocks.iter().find(|b| b.id == bid) {
            if b.kind == "heading" {
                let h = ix.headings.iter().find(|h| h.id.as_deref() == Some(bid)).expect("indexed heading");
                return Ok(as_heading(h));
            }
            return Ok(Resolved {
                kind: "paragraph",
                start_line: b.start_line,
                end_line: b.end_line,
                id: Some(b.id.clone()),
                heading: None,
            });
        }
        return Err(Diagnostic::new(Code::UnresolvedReference, format!("no block with id {:?}", fragment)));
    }
    let key = collapse_ws(&fragment.nfc().collect::<String>());
    match ix.headings.iter().find(|h| h.text == key) {
        Some(h) => Ok(as_heading(h)),
        None => Err(Diagnostic::new(Code::UnresolvedReference, format!("no heading matches {:?}", fragment))),
    }
}

/// Level (1-6) of the heading leaf spanning lines `a..b`: the `#` count (ATX) or `=`/`-` underline (setext).
pub(crate) fn heading_level(c: &[char], starts: &[usize], ends: &[usize], a: usize, b: usize) -> usize {
    if b - a == 1 {
        let line = &c[starts[a]..ends[a]];
        let p = line.iter().position(|&x| x == '#').unwrap_or(0);
        line[p..].iter().take_while(|&&x| x == '#').count()
    } else {
        let line = &c[starts[b - 1]..ends[b - 1]];
        let mut e = line.len();
        while e > 0 && (line[e - 1] == ' ' || line[e - 1] == '\t') {
            e -= 1;
        }
        if e > 0 && line[e - 1] == '=' {
            1
        } else {
            2
        }
    }
}

/// One block-ID marker in a text: the ID, the index of its leaf in `blocks::parse` order, and the
/// char range to delete (the marker and the blanks before it, never past its segment's start).
#[derive(Debug, Clone)]
pub(crate) struct Marker {
    pub(crate) id: String,
    pub(crate) leaf: usize,
    pub(crate) cut_start: usize,
    pub(crate) cut_end: usize,
}

/// Every block-ID marker, in document order. Duplicates are not checked here.
pub(crate) fn block_markers(text: &str) -> Res<Vec<Marker>> {
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let mut out = Vec::new();
    for (k, leaf) in leaves.iter().enumerate() {
        let heading = match leaf.kind {
            Kind::Heading => true,
            Kind::Paragraph => false,
            _ => continue,
        };
        let mut texts: Vec<Vec<char>> = leaf.segments.iter().map(|&(s, e)| c[s..e].to_vec()).collect();
        if texts.is_empty() {
            continue;
        }
        if heading && leaf.end_line - leaf.first_line == 1 {
            let n = texts.len() - 1;
            let s: String = texts[n].iter().collect();
            texts[n] = atx_strip(&s).chars().collect();
        }
        if let Some((id, cut)) = marker(&texts) {
            let seg_start = leaf.segments.last().map(|x| x.0).unwrap_or(0);
            let caret = seg_start + cut;
            let end = caret + 1 + id.chars().count();
            let mut start = caret;
            while start > seg_start && matches!(c[start - 1], ' ' | '\t') {
                start -= 1;
            }
            out.push(Marker { id, leaf: k, cut_start: start, cut_end: end });
        }
    }
    Ok(out)
}

/// Remove every block-ID marker (`^id`) from the text (spec section 2). Line structure is kept:
/// a marker alone on the last line of a paragraph leaves an empty line.
pub fn strip_block_ids(text: &str) -> Res<String> {
    let c: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    let mut pos = 0;
    for m in block_markers(text)? {
        out.extend(c[pos..m.cut_start].iter());
        pos = m.cut_end;
    }
    out.extend(c[pos..].iter());
    Ok(out)
}
