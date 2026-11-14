//! Reference links `[[...]]` and block anchors in compiled output (spec 3, 3.1; G57).
//!
//! Two phases, because a reference is resolved against its OWN source file but rendered against the
//! COMBINED output:
//!
//! 1. `mark_references` (per source file, before imports are expanded): every `[[path.md]]`,
//!    `[[path.md#Heading]]`, `[[path.md#^id]]` (optionally `|Label`; empty path = this file) is
//!    resolved to a source target and replaced by an inert placeholder (private-use characters
//!    around a number). Errors are reported at the reference.
//! 2. `anchor_output` + `resolve_placeholders` (on the combined text): block-ID markers become
//!    `<a id="..."></a>` anchors (deduplicated), heading slugs follow 3.1 with every block anchor
//!    reserved, and each placeholder becomes `[label](#anchor)` when its target was rendered, or
//!    `[label](file.md#anchor)` (a link to the referenced file) when it was not.

use crate::blocks::{line_bounds, parse, Kind};
use crate::diag::{Code, Diagnostic};
use crate::frontmatter;
use crate::headings::{percent_encode_anchor, slug_dedupe, slugify};
use crate::links::rebase_link;
use crate::paths::path_normalise;
use crate::refs;
use crate::rich::leaf_text;
use crate::vars::{escape_inline, html_inline_end};

type Res<T> = Result<T, Diagnostic>;

const OPEN: char = '\u{E000}';
const CLOSE: char = '\u{E001}';

/// What a reference points to, in source terms (never an output slug).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Target {
    /// The whole file.
    File,
    /// The heading with this ordinal in the file (first match of the written text).
    Heading { ordinal: usize, text: String },
    /// The block with this ID.
    Block(String),
}

#[derive(Debug, Clone)]
pub struct Reference {
    /// Root-relative path of the referenced file.
    pub path: String,
    pub target: Target,
    /// Markdown text of the link label (as written, or the escaped default).
    pub label: String,
}

/// `[[...]]` occurrences (not `![[`, not in code spans, inline HTML or after a backslash), as
/// (start, end, inner text). `[[toc]]` is left alone (the TOC directive).
fn find_refs(s: &[char]) -> Vec<(usize, usize, String)> {
    let n = s.len();
    let mut out = Vec::new();
    let mut i = 0;
    while i < n {
        let ch = s[i];
        if ch == '\\' {
            i += if i + 1 < n && s[i + 1].is_ascii_punctuation() { 2 } else { 1 };
            // `\![[x]]` is an escaped embed: the whole construct stays literal text.
            if i >= 2 && s[i - 1] == '!' && i + 1 < n && s[i] == '[' && s[i + 1] == '[' {
                i += 2;
            }
        } else if ch == '`' {
            let mut j = i;
            while j < n && s[j] == '`' {
                j += 1;
            }
            let run = j - i;
            let mut k = j;
            let mut end = None;
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
        } else if ch == '!' && i + 1 < n && s[i + 1] == '[' {
            i += 2; // an embed `![[` (or an image `![`): not a reference
        } else if ch == '[' && i + 1 < n && s[i + 1] == '[' {
            let mut j = i + 2;
            while j < n && s[j] != '[' && s[j] != ']' && s[j] != '\n' {
                j += 1;
            }
            if j + 1 < n && s[j] == ']' && s[j + 1] == ']' {
                let inner: String = s[i + 2..j].iter().collect();
                if inner.trim() != "toc" {
                    out.push((i, j + 2, inner));
                }
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

/// The body of a file and its index (headings, block IDs).
fn index_of(path: &str, text: &str) -> Res<refs::Index> {
    let sp = frontmatter::split(text).map_err(|d| in_file(d, path))?;
    refs::build(sp.body, sp.body_line).map_err(|d| in_file(d, path))
}

fn in_file(mut d: Diagnostic, path: &str) -> Diagnostic {
    if d.path.is_none() {
        d.path = Some(path.to_string());
    }
    d
}

fn dir_of(path: &str) -> &str {
    path.rfind('/').map(|i| &path[..i]).unwrap_or("")
}

/// Phase 1: replace every reference in `body` (the evaluated body of `path`, first line
/// `body_line`) by a placeholder, appending its resolved target to `table`. `read` returns the
/// full text (with frontmatter) of a root-relative file, or None.
pub fn mark_references(
    body: &str,
    body_line: u32,
    path: &str,
    read: &mut dyn FnMut(&str) -> Res<Option<String>>,
    table: &mut Vec<Reference>,
) -> Res<String> {
    let c: Vec<char> = body.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let mut edits: Vec<(usize, usize, String)> = Vec::new();
    for leaf in &leaves {
        if !matches!(leaf.kind, Kind::Paragraph | Kind::Heading) {
            continue;
        }
        let (s, origin) = leaf_text(&c, &leaf.segments);
        for (st, en, inner) in find_refs(&s) {
            let off = origin[st];
            let li = starts.partition_point(|&x| x <= off) - 1;
            let at = |d: Diagnostic| d.at(path, body_line + li as u32, (off - starts[li] + 1) as u32);
            if leaf.kind == Kind::Heading {
                return Err(at(Diagnostic::new(Code::UnsupportedFeature, "reference links inside headings are not supported")));
            }
            let (target_text, label) = match inner.split_once('|') {
                Some((t, l)) if !l.trim().is_empty() => (t, Some(l.to_string())),
                Some(_) => return Err(at(Diagnostic::new(Code::InvalidSyntax, "a reference label after | is empty"))),
                None => (inner.as_str(), None),
            };
            let (file_part, fragment) = match target_text.split_once('#') {
                Some((f, frag)) => (f.trim(), Some(frag.trim())),
                None => (target_text.trim(), None),
            };
            if file_part.is_empty() && fragment.is_none_or(|f| f.is_empty()) {
                return Err(at(Diagnostic::new(Code::InvalidSyntax, "an empty reference [[...]]")));
            }
            let target_path = if file_part.is_empty() {
                path.to_string()
            } else {
                path_normalise(dir_of(path), file_part).map_err(at)?
            };
            let text = read(&target_path)?.ok_or_else(|| {
                at(Diagnostic::new(Code::MissingImport, "the referenced file does not exist"))
            })?;
            let (target, default_label) = match fragment {
                None | Some("") => (Target::File, escape_inline(file_part, false)?),
                Some(frag) => {
                    let ix = index_of(&target_path, &text)?;
                    let r = refs::resolve_in(&ix, frag).map_err(|d| {
                        at(Diagnostic::new(Code::UnresolvedReference, d.message))
                    })?;
                    if let Some(id) = frag.strip_prefix('^') {
                        let _ = r;
                        (Target::Block(id.to_string()), escape_inline(id, false)?)
                    } else {
                        let (ordinal, h) = ix
                            .headings
                            .iter()
                            .enumerate()
                            .find(|(_, h)| h.start_line == r.start_line)
                            .map(|(k, h)| (k, h.clone()))
                            .ok_or_else(|| at(Diagnostic::new(Code::UnresolvedReference, "no heading matches")))?;
                        let label = escape_inline(&h.text, false)?;
                        (Target::Heading { ordinal, text: h.text }, label)
                    }
                }
            };
            let n = table.len();
            table.push(Reference { path: target_path, target, label: label.unwrap_or(default_label) });
            edits.push((origin[st], origin[en - 1] + 1, format!("{OPEN}{n}{CLOSE}")));
        }
    }
    let mut out = String::with_capacity(body.len());
    let mut pos = 0;
    for (a, b, rep) in edits {
        out.extend(c[pos..a].iter());
        out.push_str(&rep);
        pos = b;
    }
    out.extend(c[pos..].iter());
    Ok(out)
}

/// A block anchor in the combined output.
#[derive(Debug, Clone)]
pub struct BlockAnchor {
    pub id: String,
    /// The anchor name used in the output (the ID, or `id-2`... when the ID repeats across files).
    pub anchor: String,
    /// 0-based line of the block in the combined text.
    pub line: usize,
}

/// A heading in the combined output with its 3.1 slug.
#[derive(Debug, Clone)]
pub struct HeadingAnchor {
    pub text: String,
    pub slug: String,
    pub line: usize,
}

/// Phase 2a: turn every block-ID marker into an `<a id>` anchor at the start of its block and
/// compute the heading slugs (3.1) with every block anchor reserved. Keeps the line structure.
pub fn anchor_output(text: &str) -> Res<(String, Vec<BlockAnchor>, Vec<HeadingAnchor>)> {
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let marks = refs::block_markers(text)?;
    let ids: Vec<String> = marks.iter().map(|m| m.id.clone()).collect();
    let names = slug_dedupe(&ids, &[]);
    let mut edits: Vec<(usize, usize, String)> = Vec::new();
    let mut blocks = Vec::new();
    for (m, name) in marks.iter().zip(&names) {
        let leaf = &leaves[m.leaf];
        let start = leaf.segments.first().map(|x| x.0).unwrap_or(starts[leaf.first_line]);
        edits.push((start, start, format!("<a id=\"{}\"></a>", name)));
        edits.push((m.cut_start, m.cut_end, String::new()));
        blocks.push(BlockAnchor { id: m.id.clone(), anchor: name.clone(), line: leaf.first_line });
    }
    edits.sort_by_key(|e| (e.0, e.1));
    let mut out = String::with_capacity(text.len() + 32 * blocks.len());
    let mut pos = 0;
    for (a, b, rep) in &edits {
        out.extend(c[pos..*a].iter());
        out.push_str(rep);
        pos = *b;
    }
    out.extend(c[pos..].iter());
    // Heading slugs come from the original text (marker-free plain text), reserving block anchors.
    let ix = refs::build_lenient(text)?;
    let reserved: Vec<String> = names.clone();
    let slugs = slug_dedupe(&ix.headings.iter().map(|h| slugify(&h.text)).collect::<Vec<_>>(), &reserved);
    let heads = ix
        .headings
        .iter()
        .zip(slugs)
        .map(|(h, slug)| HeadingAnchor { text: h.text.clone(), slug, line: h.start_line })
        .collect();
    Ok((out, blocks, heads))
}

/// Phase 2b: replace the placeholders. `path_of` maps a 0-based line of the combined text to its
/// source file; `read` gives a file's text (for links to files that were not rendered).
pub fn resolve_placeholders(
    text: &str,
    table: &[Reference],
    blocks: &[BlockAnchor],
    heads: &[HeadingAnchor],
    path_of: &dyn Fn(usize) -> Option<String>,
    entry: &str,
    read: &mut dyn FnMut(&str) -> Res<Option<String>>,
) -> Res<String> {
    if table.is_empty() {
        return Ok(text.to_string());
    }
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != OPEN {
            out.push(ch);
            continue;
        }
        let mut num = String::new();
        for d in chars.by_ref() {
            if d == CLOSE {
                break;
            }
            num.push(d);
        }
        let Some(r) = num.parse::<usize>().ok().and_then(|n| table.get(n)) else {
            return Err(Diagnostic::new(Code::InvalidSyntax, "corrupted reference placeholder"));
        };
        out.push_str(&format!("[{}]({})", r.label, destination(r, blocks, heads, path_of, entry, read)?));
    }
    Ok(out)
}

/// `#anchor` of the first rendered occurrence, else `file.md#anchor` of the referenced file.
fn destination(
    r: &Reference,
    blocks: &[BlockAnchor],
    heads: &[HeadingAnchor],
    path_of: &dyn Fn(usize) -> Option<String>,
    entry: &str,
    read: &mut dyn FnMut(&str) -> Res<Option<String>>,
) -> Res<String> {
    let from = |line: usize| path_of(line).as_deref() == Some(r.path.as_str());
    let local = match &r.target {
        Target::Block(id) => blocks.iter().find(|b| b.id == *id && from(b.line)).map(|b| b.anchor.clone()),
        Target::Heading { text, .. } => heads.iter().find(|h| h.text == *text && from(h.line)).map(|h| h.slug.clone()),
        Target::File => heads.iter().find(|h| from(h.line)).map(|h| h.slug.clone()),
    };
    if let Some(a) = local {
        return Ok(format!("#{}", percent_encode_anchor(&a)));
    }
    // Not rendered here: link the referenced file, with the anchor it has as a document of its own.
    let file = rebase_link(&r.path, "", dir_of(entry))?;
    let file = if file.contains(['(', ')', ' ', '<', '>']) { format!("<{}>", file) } else { file };
    let anchor = match &r.target {
        Target::File => None,
        Target::Block(id) => Some(id.clone()),
        Target::Heading { ordinal, .. } => {
            let text = read(&r.path)?.ok_or_else(|| Diagnostic::new(Code::MissingImport, "the referenced file does not exist"))?;
            let ix = index_of(&r.path, &text)?;
            let reserved: Vec<String> = ix.blocks.iter().map(|b| b.id.clone()).collect();
            let slugs = slug_dedupe(&ix.headings.iter().map(|h| slugify(&h.text)).collect::<Vec<_>>(), &reserved);
            slugs.get(*ordinal).cloned()
        }
    };
    Ok(match anchor {
        Some(a) => format!("{}#{}", file, percent_encode_anchor(&a)),
        None => file,
    })
}
