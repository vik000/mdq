//! Strict YAML subset loader (spec section 5; decisions G23 in docs/TEST_PLAN.md).
//!
//! Supported: block mappings and sequences (a sequence may sit at its key's
//! indent), flow sequences/mappings (may span lines), plain, single- and
//! double-quoted scalars (one line each), literal `|` and folded `>` block
//! scalars with `+`/`-` chomping, `#` comments, one optional leading `---`.
//!
//! Rejected with MDQ_METADATA_ERROR: duplicate mapping keys (any level, flow
//! included), anchors/aliases/tags, complex keys (`? `), directives, extra
//! document markers, tabs in indentation, control characters, multi-line plain
//! or quoted scalars, explicit block-scalar indentation indicators, and
//! numbers whose meaning differs between YAML versions (`007`, `0x1F`, `+1`,
//! `.5`, `1.`, `.inf`, `.nan`): quote them to get a string.
//! Mapping keys are always strings. Types follow the YAML 1.2 core schema:
//! `yes`/`no`/`on`/`off` and timestamps are strings. Numbers are returned as
//! exact canonical decimal text. Limits: depth 64, 100 000 nodes, 2 MiB.

use std::collections::HashSet;

use crate::decimal::Decimal;
use crate::diag::{Code, Diagnostic};

type Res<T> = Result<T, Diagnostic>;

pub const MAX_DEPTH: usize = 64;
pub const MAX_NODES: usize = 100_000;
pub const MAX_INPUT_BYTES: usize = 2 * 1024 * 1024;
const MAX_EXPONENT: i64 = 400;
const MAX_NUMBER_DIGITS: usize = 400;

#[derive(Debug, Clone, PartialEq)]
pub enum Yaml {
    Null,
    Bool(bool),
    /// Canonical decimal text (no exponent, no trailing zeros, no `-0`).
    Number(String),
    Str(String),
    Seq(Vec<Yaml>),
    /// Entries in document order; keys are unique.
    Map(Vec<(String, Yaml)>),
}

impl Yaml {
    pub fn kind(&self) -> &'static str {
        match self {
            Yaml::Null => "null",
            Yaml::Bool(_) => "a boolean",
            Yaml::Number(_) => "a number",
            Yaml::Str(_) => "a string",
            Yaml::Seq(_) => "a sequence",
            Yaml::Map(_) => "a mapping",
        }
    }
}

#[derive(Clone, Copy)]
struct Cur {
    l: usize,
    c: usize,
}

struct Parser {
    lines: Vec<Vec<char>>,
    first_line: u32,
    i: usize,
    nodes: usize,
}

fn all_blank(line: &[char]) -> bool {
    line.iter().all(|&ch| ch == ' ' || ch == '\t')
}

/// Blank or comment-only.
fn is_blank(line: &[char]) -> bool {
    for &ch in line {
        if ch == ' ' || ch == '\t' {
            continue;
        }
        return ch == '#';
    }
    true
}

fn indent_of(line: &[char]) -> usize {
    line.iter().take_while(|&&c| c == ' ').count()
}

fn is_seq_item(line: &[char], ind: usize) -> bool {
    line.get(ind).copied() == Some('-') && matches!(line.get(ind + 1).copied(), None | Some(' '))
}

fn starts_with_marker(line: &[char], marker: &str) -> bool {
    let m: Vec<char> = marker.chars().collect();
    line.len() >= m.len()
        && line[..m.len()] == m[..]
        && matches!(line.get(m.len()).copied(), None | Some(' ') | Some('\t'))
}

/// Load `text`. `mapping_only`: the document must be a mapping (an empty
/// document is then `{}`); otherwise an empty document is null.
/// `first_line` is the file line number of `text`'s first line.
pub fn load(text: &str, mapping_only: bool, first_line: u32) -> Res<Yaml> {
    if text.len() > MAX_INPUT_BYTES {
        return Err(Diagnostic::new(
            Code::ResourceLimit,
            format!("YAML input is larger than {MAX_INPUT_BYTES} bytes"),
        ));
    }
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut lines: Vec<Vec<char>> = Vec::new();
    for raw in text.split('\n') {
        let raw = raw.strip_suffix('\r').unwrap_or(raw);
        lines.push(raw.chars().collect());
    }
    if text.ends_with('\n') {
        lines.pop();
    }
    let mut p = Parser { lines, first_line, i: 0, nodes: 0 };
    p.check_chars()?;
    p.check_markers()?;
    let first = match p.next_content()? {
        Some(j) => Some((j, indent_of(&p.lines[j]))), // before a `-` gets blanked out
        None => None,
    };
    let root = p.parse_block(0, 0)?;
    if let Some(j) = p.next_content()? {
        let col = indent_of(&p.lines[j]);
        return Err(p.meta_err(j, col, "unexpected content (check the indentation)"));
    }
    match root {
        None => Ok(if mapping_only { Yaml::Map(Vec::new()) } else { Yaml::Null }),
        Some(v) => {
            if mapping_only && !matches!(v, Yaml::Map(_)) {
                let (j, col) = first.unwrap_or((0, 0));
                Err(p.meta_err(j, col, format!("the document must be a mapping, found {}", v.kind())))
            } else {
                Ok(v)
            }
        }
    }
}

impl Parser {
    fn meta_err(&self, l: usize, c: usize, msg: impl Into<String>) -> Diagnostic {
        Diagnostic::new(Code::MetadataError, msg).at_position(self.first_line + l as u32, c as u32 + 1)
    }

    fn limit_err(&self, l: usize, c: usize, msg: impl Into<String>) -> Diagnostic {
        Diagnostic::new(Code::ResourceLimit, msg).at_position(self.first_line + l as u32, c as u32 + 1)
    }

    fn count_node(&mut self, l: usize, c: usize) -> Res<()> {
        self.nodes += 1;
        if self.nodes > MAX_NODES {
            return Err(self.limit_err(l, c, format!("more than {MAX_NODES} YAML nodes")));
        }
        Ok(())
    }

    fn check_depth(&self, depth: usize, l: usize, c: usize) -> Res<()> {
        if depth > MAX_DEPTH {
            return Err(self.limit_err(l, c, format!("YAML nesting deeper than {MAX_DEPTH} levels")));
        }
        Ok(())
    }

    fn check_chars(&self) -> Res<()> {
        for (l, line) in self.lines.iter().enumerate() {
            for (c, &ch) in line.iter().enumerate() {
                let u = ch as u32;
                let bad = (u < 0x20 && ch != '\t')
                    || (0x7f..=0x9f).contains(&u)
                    || u == 0xfeff
                    || u == 0xfffe
                    || u == 0xffff;
                if bad {
                    return Err(self.meta_err(l, c, format!("control character U+{u:04X} is not allowed")));
                }
            }
        }
        Ok(())
    }

    /// One optional `---` before the content is allowed (and blanked); any other
    /// `---` or `...` line means several documents, which are not supported.
    fn check_markers(&mut self) -> Res<()> {
        let mut seen_content = false;
        let mut start_used = false;
        for l in 0..self.lines.len() {
            let is_start = starts_with_marker(&self.lines[l], "---");
            let is_end = starts_with_marker(&self.lines[l], "...");
            if is_start || is_end {
                let rest_blank = is_blank(&self.lines[l][3..]);
                if is_start && !seen_content && !start_used && rest_blank {
                    self.lines[l].clear();
                    start_used = true;
                    continue;
                }
                return Err(self.meta_err(
                    l,
                    0,
                    "document markers (`---`, `...`) are only allowed once, at the start: multiple documents are not supported",
                ));
            }
            if !is_blank(&self.lines[l]) {
                seen_content = true;
            }
        }
        Ok(())
    }

    /// Advance `self.i` over blank/comment lines; the next content line, if any.
    fn next_content(&mut self) -> Res<Option<usize>> {
        while self.i < self.lines.len() {
            if !is_blank(&self.lines[self.i]) {
                let mut k = 0;
                while k < self.lines[self.i].len() {
                    let ch = self.lines[self.i][k];
                    if ch == '\t' {
                        return Err(self.meta_err(self.i, k, "tab characters are not allowed in indentation"));
                    }
                    if ch != ' ' {
                        break;
                    }
                    k += 1;
                }
                return Ok(Some(self.i));
            }
            self.i += 1;
        }
        Ok(None)
    }

    // ------------------------------------------------------------ blocks

    /// The block node at the next content line, if its indent is >= `min_indent`.
    fn parse_block(&mut self, min_indent: usize, depth: usize) -> Res<Option<Yaml>> {
        let Some(j) = self.next_content()? else {
            return Ok(None);
        };
        let ind = indent_of(&self.lines[j]);
        if ind < min_indent {
            return Ok(None);
        }
        self.check_depth(depth, j, ind)?;
        if is_seq_item(&self.lines[j], ind) {
            return Ok(Some(self.parse_seq(ind, depth)?));
        }
        if self.map_entry_at(j, ind)?.is_some() {
            return Ok(Some(self.parse_map(ind, depth)?));
        }
        self.count_node(j, ind)?;
        let v = self.parse_inline(j, ind, ind as isize - 1, depth + 1)?;
        Ok(Some(v))
    }

    fn parse_map(&mut self, ind: usize, depth: usize) -> Res<Yaml> {
        let mut entries: Vec<(String, Yaml)> = Vec::new();
        let mut seen: HashSet<String> = HashSet::new();
        loop {
            let Some(j) = self.next_content()? else {
                break;
            };
            let lind = indent_of(&self.lines[j]);
            if lind < ind {
                break;
            }
            if lind > ind {
                return Err(self.meta_err(j, lind, "unexpected indentation"));
            }
            if is_seq_item(&self.lines[j], ind) {
                return Err(self.meta_err(j, ind, "unexpected sequence item in a mapping"));
            }
            let Some((key, vcol)) = self.map_entry_at(j, ind)? else {
                return Err(self.meta_err(j, ind, "expected a `key: value` entry"));
            };
            if !seen.insert(key.clone()) {
                return Err(self.meta_err(j, ind, format!("duplicate mapping key {key:?}")));
            }
            self.count_node(j, ind)?;
            let value = self.parse_value(j, vcol, ind, depth + 1)?;
            entries.push((key, value));
        }
        Ok(Yaml::Map(entries))
    }

    fn parse_seq(&mut self, ind: usize, depth: usize) -> Res<Yaml> {
        let mut items: Vec<Yaml> = Vec::new();
        loop {
            let Some(j) = self.next_content()? else {
                break;
            };
            let lind = indent_of(&self.lines[j]);
            if lind < ind {
                break;
            }
            if lind > ind {
                return Err(self.meta_err(j, lind, "unexpected indentation"));
            }
            if !is_seq_item(&self.lines[j], ind) {
                break;
            }
            self.count_node(j, ind)?;
            let mut c = ind + 1;
            while self.lines[j].get(c).copied() == Some(' ') {
                c += 1;
            }
            let rest_empty = matches!(self.lines[j].get(c).copied(), None | Some('#'));
            if rest_empty {
                self.i = j + 1;
                let v = self.parse_block(ind + 1, depth + 1)?.unwrap_or(Yaml::Null);
                items.push(v);
            } else {
                // Blank out the `-` so the item reads as a node indented at column `c`.
                self.lines[j][ind] = ' ';
                let v = self.parse_block(c, depth + 1)?.unwrap_or(Yaml::Null);
                items.push(v);
            }
        }
        Ok(Yaml::Seq(items))
    }

    /// The value after `key:` (the colon ends just before column `vcol`).
    fn parse_value(&mut self, j: usize, vcol: usize, parent: usize, depth: usize) -> Res<Yaml> {
        self.check_depth(depth, j, vcol)?;
        let mut c = vcol;
        while matches!(self.lines[j].get(c).copied(), Some(' ') | Some('\t')) {
            c += 1;
        }
        match self.lines[j].get(c).copied() {
            None | Some('#') => {
                self.i = j + 1;
                let Some(n) = self.next_content()? else {
                    return Ok(Yaml::Null);
                };
                let nind = indent_of(&self.lines[n]);
                if nind > parent {
                    return Ok(self.parse_block(parent + 1, depth)?.unwrap_or(Yaml::Null));
                }
                if nind == parent && is_seq_item(&self.lines[n], nind) {
                    return self.parse_seq(nind, depth);
                }
                Ok(Yaml::Null)
            }
            Some(_) => {
                self.count_node(j, c)?;
                self.parse_inline(j, c, parent as isize, depth + 1)
            }
        }
    }

    /// If line `l` has a mapping entry starting at column `start`: its key and
    /// the column just after the colon.
    fn map_entry_at(&self, l: usize, start: usize) -> Res<Option<(String, usize)>> {
        let line = &self.lines[l];
        let at = |k: usize| line.get(k).copied();
        match at(start) {
            None => Ok(None),
            Some('"') | Some('\'') => {
                let (key, end) = self.parse_quoted(l, start)?;
                let mut k = end;
                while matches!(at(k), Some(' ') | Some('\t')) {
                    k += 1;
                }
                if at(k) == Some(':') && matches!(at(k + 1), None | Some(' ') | Some('\t')) {
                    Ok(Some((key, k + 1)))
                } else {
                    Ok(None)
                }
            }
            Some('[') | Some('{') | Some('|') | Some('>') => Ok(None),
            Some('&') | Some('*') | Some('!') => {
                Err(self.meta_err(l, start, "anchors, aliases and tags are not supported"))
            }
            Some('%') | Some('@') | Some('`') | Some(',') | Some(']') | Some('}') => Err(self.meta_err(
                l,
                start,
                "a plain scalar cannot start with this character (quote it)",
            )),
            Some('?') if matches!(at(start + 1), None | Some(' ')) => {
                Err(self.meta_err(l, start, "complex mapping keys (`? `) are not supported"))
            }
            Some(_) => {
                let mut k = start;
                while k < line.len() {
                    let ch = line[k];
                    if ch == '#' && k > start && (line[k - 1] == ' ' || line[k - 1] == '\t') {
                        break;
                    }
                    if ch == ':' && matches!(at(k + 1), None | Some(' ') | Some('\t')) {
                        let raw: String = line[start..k].iter().collect();
                        let key = raw.trim_end_matches(|x| x == ' ' || x == '\t').to_string();
                        if key.is_empty() {
                            return Err(self.meta_err(l, start, "empty mapping key"));
                        }
                        return Ok(Some((key, k + 1)));
                    }
                    k += 1;
                }
                Ok(None)
            }
        }
    }

    // ------------------------------------------------------------ values

    /// A value starting at (l, c): flow collection, quoted/plain scalar or block
    /// scalar. Sets `self.i` to the line after it. `parent` is the indent of the
    /// owning construct (block scalar content must be indented more).
    fn parse_inline(&mut self, l: usize, c: usize, parent: isize, depth: usize) -> Res<Yaml> {
        let ch = self.lines[l][c];
        match ch {
            '[' | '{' => {
                let mut cur = Cur { l, c };
                let v = self.parse_flow_value(&mut cur, depth)?;
                self.expect_line_end(cur)?;
                self.i = cur.l + 1;
                Ok(v)
            }
            '"' | '\'' => {
                let (s, end) = self.parse_quoted(l, c)?;
                self.expect_line_end(Cur { l, c: end })?;
                self.i = l + 1;
                Ok(Yaml::Str(s))
            }
            '|' | '>' => self.parse_block_scalar(l, c, parent),
            '&' | '*' | '!' => Err(self.meta_err(l, c, "anchors, aliases and tags are not supported")),
            '%' | '@' | '`' | ',' | ']' | '}' => Err(self.meta_err(
                l,
                c,
                "a plain scalar cannot start with this character (quote it)",
            )),
            '?' | '-' if matches!(self.lines[l].get(c + 1).copied(), None | Some(' ')) => {
                Err(self.meta_err(l, c, "this indicator is not allowed here (quote the text if it is a string)"))
            }
            _ => {
                let text = self.scan_plain_block(l, c)?;
                self.i = l + 1;
                self.resolve_plain(&text, l, c)
            }
        }
    }

    fn scan_plain_block(&self, l: usize, c: usize) -> Res<String> {
        let line = &self.lines[l];
        let mut k = c;
        let mut end = c;
        while k < line.len() {
            let ch = line[k];
            if ch == '#' && k > c && (line[k - 1] == ' ' || line[k - 1] == '\t') {
                break;
            }
            if ch == ':' && matches!(line.get(k + 1).copied(), None | Some(' ') | Some('\t')) {
                return Err(self.meta_err(
                    l,
                    k,
                    "unexpected `:` (a mapping value is not allowed here; quote the text if it is a string)",
                ));
            }
            if ch != ' ' && ch != '\t' {
                end = k + 1;
            }
            k += 1;
        }
        Ok(line[c..end].iter().collect())
    }

    /// Only blanks and an optional comment (after a blank) may follow.
    fn expect_line_end(&self, cur: Cur) -> Res<()> {
        let line = &self.lines[cur.l];
        let mut k = cur.c;
        while matches!(line.get(k).copied(), Some(' ') | Some('\t')) {
            k += 1;
        }
        match line.get(k).copied() {
            None => Ok(()),
            Some('#') if k > cur.c => Ok(()),
            Some(_) => Err(self.meta_err(cur.l, k, "unexpected characters after the value")),
        }
    }

    // ----------------------------------------------------------- quoting

    fn parse_quoted(&self, l: usize, c: usize) -> Res<(String, usize)> {
        let line = &self.lines[l];
        let quote = line[c];
        let mut out = String::new();
        let mut k = c + 1;
        loop {
            let Some(ch) = line.get(k).copied() else {
                return Err(self.meta_err(
                    l,
                    c,
                    "unterminated quoted scalar (quoted scalars must fit on one line)",
                ));
            };
            if quote == '\'' {
                if ch == '\'' {
                    if line.get(k + 1).copied() == Some('\'') {
                        out.push('\'');
                        k += 2;
                        continue;
                    }
                    return Ok((out, k + 1));
                }
                out.push(ch);
                k += 1;
            } else if ch == '"' {
                return Ok((out, k + 1));
            } else if ch == '\\' {
                let Some(e) = line.get(k + 1).copied() else {
                    return Err(self.meta_err(l, c, "unterminated quoted scalar (quoted scalars must fit on one line)"));
                };
                let (decoded, used): (char, usize) = match e {
                    '0' => ('\0', 2),
                    'a' => ('\x07', 2),
                    'b' => ('\x08', 2),
                    't' | '\t' => ('\t', 2),
                    'n' => ('\n', 2),
                    'v' => ('\x0b', 2),
                    'f' => ('\x0c', 2),
                    'r' => ('\r', 2),
                    'e' => ('\x1b', 2),
                    ' ' => (' ', 2),
                    '"' => ('"', 2),
                    '/' => ('/', 2),
                    '\\' => ('\\', 2),
                    'N' => ('\u{85}', 2),
                    '_' => ('\u{a0}', 2),
                    'L' => ('\u{2028}', 2),
                    'P' => ('\u{2029}', 2),
                    'x' => self.hex_escape(l, k, 2)?,
                    'u' => self.hex_escape(l, k, 4)?,
                    'U' => self.hex_escape(l, k, 8)?,
                    _ => return Err(self.meta_err(l, k, format!("unknown escape `\\{e}`"))),
                };
                out.push(decoded);
                k += used;
            } else {
                out.push(ch);
                k += 1;
            }
        }
    }

    /// `\xNN`, `\uNNNN`, `\UNNNNNNNN` at line[k..]: the character and the escape length.
    fn hex_escape(&self, l: usize, k: usize, n: usize) -> Res<(char, usize)> {
        let line = &self.lines[l];
        let digits: String = line.iter().skip(k + 2).take(n).collect();
        if digits.chars().count() != n || !digits.chars().all(|d| d.is_ascii_hexdigit()) {
            return Err(self.meta_err(l, k, "invalid hexadecimal escape"));
        }
        let v = u32::from_str_radix(&digits, 16)
            .map_err(|_| self.meta_err(l, k, "invalid hexadecimal escape"))?;
        match char::from_u32(v) {
            Some(ch) => Ok((ch, 2 + n)),
            None => Err(self.meta_err(l, k, "escape is not a Unicode scalar value")),
        }
    }

    // ------------------------------------------------------ plain scalars

    fn resolve_plain(&self, text: &str, l: usize, c: usize) -> Res<Yaml> {
        match text {
            "" | "~" | "null" | "Null" | "NULL" => return Ok(Yaml::Null),
            "true" | "True" | "TRUE" => return Ok(Yaml::Bool(true)),
            "false" | "False" | "FALSE" => return Ok(Yaml::Bool(false)),
            _ => {}
        }
        match self.number(text, l, c)? {
            Some(n) => Ok(Yaml::Number(n)),
            None => Ok(Yaml::Str(text.to_string())),
        }
    }

    /// `Some(canonical)` for a plain decimal number, `None` for a string,
    /// an error for numbers that other YAML versions read differently.
    fn number(&self, text: &str, l: usize, c: usize) -> Res<Option<String>> {
        let b: Vec<char> = text.chars().collect();
        let amb = |what: &str| {
            self.meta_err(
                l,
                c,
                format!("ambiguous number {text:?} ({what}); quote it to keep a string, or write a plain decimal"),
            )
        };
        let mut k = 0;
        let mut sign: Option<char> = None;
        if matches!(b.first().copied(), Some('+') | Some('-')) {
            sign = b.first().copied();
            k = 1;
        }
        let rest: String = b[k..].iter().collect();
        if matches!(rest.as_str(), ".inf" | ".Inf" | ".INF") {
            return Err(amb("infinity is not supported"));
        }
        if sign.is_none() && matches!(rest.as_str(), ".nan" | ".NaN" | ".NAN") {
            return Err(amb("NaN is not supported"));
        }
        if sign.is_none() {
            if let Some(r) = rest.strip_prefix("0x") {
                if !r.is_empty() && r.chars().all(|d| d.is_ascii_hexdigit()) {
                    return Err(amb("hexadecimal"));
                }
            }
            if let Some(r) = rest.strip_prefix("0o") {
                if !r.is_empty() && r.chars().all(|d| ('0'..='7').contains(&d)) {
                    return Err(amb("octal"));
                }
            }
        }
        let mut p = k;
        while p < b.len() && b[p].is_ascii_digit() {
            p += 1;
        }
        let int: String = b[k..p].iter().collect();
        let mut frac = String::new();
        let mut has_dot = false;
        if p < b.len() && b[p] == '.' {
            let q0 = p + 1;
            let mut q = q0;
            while q < b.len() && b[q].is_ascii_digit() {
                q += 1;
            }
            if !(int.is_empty() && q == q0) {
                has_dot = true;
                frac = b[q0..q].iter().collect();
                p = q;
            }
        }
        if int.is_empty() && !has_dot {
            return Ok(None);
        }
        let mut exp_s = String::new();
        if p < b.len() && (b[p] == 'e' || b[p] == 'E') {
            let mut q = p + 1;
            if q < b.len() && (b[q] == '+' || b[q] == '-') {
                q += 1;
            }
            let ds = q;
            while q < b.len() && b[q].is_ascii_digit() {
                q += 1;
            }
            if q == ds {
                return Ok(None);
            }
            exp_s = b[p + 1..q].iter().collect();
            p = q;
        }
        if p != b.len() {
            return Ok(None);
        }
        if sign == Some('+') {
            return Err(amb("leading `+`"));
        }
        if !(int == "0" || (!int.is_empty() && !int.starts_with('0'))) {
            return Err(amb("leading zeros or a missing integer part"));
        }
        if has_dot && frac.is_empty() {
            return Err(amb("trailing `.`"));
        }
        let exp: i64 = if exp_s.is_empty() {
            0
        } else {
            exp_s.parse::<i64>().map_err(|_| self.meta_err(l, c, "number exponent out of range"))?
        };
        let digits = format!("{int}{frac}");
        if exp.abs() > MAX_EXPONENT || digits.len() > MAX_NUMBER_DIGITS {
            return Err(self.meta_err(l, c, "number out of range"));
        }
        let scale = frac.len() as i64 - exp;
        let body = if scale <= 0 {
            format!("{digits}{}", "0".repeat((-scale) as usize))
        } else {
            let s = scale as usize;
            if digits.len() > s {
                format!("{}.{}", &digits[..digits.len() - s], &digits[digits.len() - s..])
            } else {
                format!("0.{}{}", "0".repeat(s - digits.len()), digits)
            }
        };
        let signed = if sign == Some('-') { format!("-{body}") } else { body };
        let d = Decimal::parse(&signed).map_err(|e| self.meta_err(l, c, e.message))?;
        Ok(Some(d.to_canonical()))
    }

    // ------------------------------------------------------ block scalars

    fn parse_block_scalar(&mut self, l: usize, c: usize, parent: isize) -> Res<Yaml> {
        let (folded, chomp, header_end) = {
            let line = &self.lines[l];
            let folded = line[c] == '>';
            let mut chomp = 'c';
            let mut k = c + 1;
            loop {
                match line.get(k).copied() {
                    Some('+') if chomp == 'c' => {
                        chomp = 'k';
                        k += 1;
                    }
                    Some('-') if chomp == 'c' => {
                        chomp = 's';
                        k += 1;
                    }
                    Some(d) if d.is_ascii_digit() => {
                        return Err(self.meta_err(l, k, "explicit indentation indicators are not supported"));
                    }
                    _ => break,
                }
            }
            (folded, chomp, k)
        };
        self.expect_line_end(Cur { l, c: header_end })?;

        let total = self.lines.len();
        let mut e = l + 1;
        while e < total && (all_blank(&self.lines[e]) || (indent_of(&self.lines[e]) as isize) > parent) {
            e += 1;
        }
        let mut content_indent: Option<usize> = None;
        for k in (l + 1)..e {
            if !all_blank(&self.lines[k]) {
                let ind = indent_of(&self.lines[k]);
                match content_indent {
                    None => content_indent = Some(ind),
                    Some(ci) => {
                        if ind < ci {
                            return Err(self.meta_err(
                                k,
                                ind,
                                "block scalar line is indented less than its first line",
                            ));
                        }
                    }
                }
            }
        }
        let ci = content_indent.unwrap_or(0);
        let mut texts: Vec<String> = Vec::new();
        for k in (l + 1)..e {
            let line = &self.lines[k];
            let s: String = if line.len() > ci { line[ci..].iter().collect() } else { String::new() };
            texts.push(s);
        }
        let mut trailing = 0usize;
        while let Some(last) = texts.last() {
            if last.is_empty() {
                texts.pop();
                trailing += 1;
            } else {
                break;
            }
        }
        let mut out = String::new();
        if folded {
            let mut pending = 0usize;
            let mut prev_more = false;
            let mut first = true;
            for t in &texts {
                if t.is_empty() {
                    pending += 1;
                    continue;
                }
                let more = t.starts_with(' ') || t.starts_with('\t');
                if first {
                    for _ in 0..pending {
                        out.push('\n');
                    }
                } else if pending == 0 {
                    out.push(if prev_more || more { '\n' } else { ' ' });
                } else {
                    let n = if prev_more || more { pending + 1 } else { pending };
                    for _ in 0..n {
                        out.push('\n');
                    }
                }
                out.push_str(t);
                first = false;
                prev_more = more;
                pending = 0;
            }
        } else {
            out = texts.join("\n");
        }
        match chomp {
            's' => {}
            'c' => {
                if !texts.is_empty() {
                    out.push('\n');
                }
            }
            _ => {
                if !texts.is_empty() {
                    out.push('\n');
                }
                for _ in 0..trailing {
                    out.push('\n');
                }
            }
        }
        self.i = e;
        Ok(Yaml::Str(out))
    }

    // ------------------------------------------------------------- flow

    fn ch_at(&self, cur: Cur) -> Option<char> {
        self.lines[cur.l].get(cur.c).copied()
    }

    /// Skip blanks, line breaks and comments inside a flow collection opened at `start`.
    fn flow_skip(&self, cur: &mut Cur, start: Cur) -> Res<()> {
        loop {
            match self.ch_at(*cur) {
                Some(' ') | Some('\t') => cur.c += 1,
                Some('#') => {
                    let after_blank =
                        cur.c == 0 || matches!(self.lines[cur.l][cur.c - 1], ' ' | '\t');
                    if after_blank {
                        cur.c = self.lines[cur.l].len();
                    } else {
                        return Ok(());
                    }
                }
                Some(_) => return Ok(()),
                None => {
                    if cur.l + 1 >= self.lines.len() {
                        return Err(self.meta_err(start.l, start.c, "unterminated flow collection"));
                    }
                    cur.l += 1;
                    cur.c = 0;
                }
            }
        }
    }

    fn parse_flow_value(&mut self, cur: &mut Cur, depth: usize) -> Res<Yaml> {
        self.check_depth(depth, cur.l, cur.c)?;
        self.count_node(cur.l, cur.c)?;
        let start = *cur;
        match self.ch_at(*cur) {
            Some('[') => {
                cur.c += 1;
                let mut items: Vec<Yaml> = Vec::new();
                loop {
                    self.flow_skip(cur, start)?;
                    match self.ch_at(*cur) {
                        Some(']') => {
                            cur.c += 1;
                            break;
                        }
                        Some(',') => {
                            return Err(self.meta_err(cur.l, cur.c, "empty entry in a flow sequence"));
                        }
                        _ => {}
                    }
                    let v = self.parse_flow_value(cur, depth + 1)?;
                    items.push(v);
                    self.flow_skip(cur, start)?;
                    match self.ch_at(*cur) {
                        Some(',') => cur.c += 1,
                        Some(']') => {
                            cur.c += 1;
                            break;
                        }
                        _ => {
                            return Err(self.meta_err(cur.l, cur.c, "expected `,` or `]` in a flow sequence"));
                        }
                    }
                }
                Ok(Yaml::Seq(items))
            }
            Some('{') => {
                cur.c += 1;
                let mut entries: Vec<(String, Yaml)> = Vec::new();
                let mut seen: HashSet<String> = HashSet::new();
                loop {
                    self.flow_skip(cur, start)?;
                    match self.ch_at(*cur) {
                        Some('}') => {
                            cur.c += 1;
                            break;
                        }
                        Some(',') => {
                            return Err(self.meta_err(cur.l, cur.c, "empty entry in a flow mapping"));
                        }
                        _ => {}
                    }
                    let kstart = *cur;
                    let key = match self.ch_at(*cur) {
                        Some('"') | Some('\'') => {
                            let (s, end) = self.parse_quoted(cur.l, cur.c)?;
                            cur.c = end;
                            s
                        }
                        Some('[') | Some('{') => {
                            return Err(self.meta_err(cur.l, cur.c, "collections cannot be mapping keys"));
                        }
                        Some('&') | Some('*') | Some('!') => {
                            return Err(self.meta_err(cur.l, cur.c, "anchors, aliases and tags are not supported"));
                        }
                        _ => self.scan_plain_flow(cur)?,
                    };
                    self.flow_skip(cur, start)?;
                    if self.ch_at(*cur) != Some(':') {
                        return Err(self.meta_err(cur.l, cur.c, "expected `:` after a key in a flow mapping"));
                    }
                    cur.c += 1;
                    if !seen.insert(key.clone()) {
                        return Err(self.meta_err(kstart.l, kstart.c, format!("duplicate mapping key {key:?}")));
                    }
                    self.flow_skip(cur, start)?;
                    let v = match self.ch_at(*cur) {
                        Some(',') | Some('}') => Yaml::Null,
                        _ => self.parse_flow_value(cur, depth + 1)?,
                    };
                    entries.push((key, v));
                    self.flow_skip(cur, start)?;
                    match self.ch_at(*cur) {
                        Some(',') => cur.c += 1,
                        Some('}') => {
                            cur.c += 1;
                            break;
                        }
                        _ => {
                            return Err(self.meta_err(cur.l, cur.c, "expected `,` or `}` in a flow mapping"));
                        }
                    }
                }
                Ok(Yaml::Map(entries))
            }
            Some('"') | Some('\'') => {
                let (s, end) = self.parse_quoted(cur.l, cur.c)?;
                cur.c = end;
                Ok(Yaml::Str(s))
            }
            Some('&') | Some('*') | Some('!') => {
                Err(self.meta_err(cur.l, cur.c, "anchors, aliases and tags are not supported"))
            }
            Some('|') | Some('>') => {
                Err(self.meta_err(cur.l, cur.c, "block scalars are not allowed inside flow collections"))
            }
            Some(_) => {
                let text = self.scan_plain_flow(cur)?;
                self.resolve_plain(&text, start.l, start.c)
            }
            None => Err(self.meta_err(start.l, start.c, "unterminated flow collection")),
        }
    }

    /// A plain scalar inside a flow collection (stops at flow indicators).
    fn scan_plain_flow(&self, cur: &mut Cur) -> Res<String> {
        let line = &self.lines[cur.l];
        let mut k = cur.c;
        let mut end = cur.c;
        while k < line.len() {
            let ch = line[k];
            if matches!(ch, ',' | '[' | ']' | '{' | '}') {
                break;
            }
            if ch == '#' && k > cur.c && (line[k - 1] == ' ' || line[k - 1] == '\t') {
                break;
            }
            if ch == ':'
                && matches!(
                    line.get(k + 1).copied(),
                    None | Some(' ') | Some('\t') | Some(',') | Some(']') | Some('}') | Some('[') | Some('{')
                )
            {
                break;
            }
            if ch != ' ' && ch != '\t' {
                end = k + 1;
            }
            k += 1;
        }
        if end == cur.c {
            return Err(self.meta_err(cur.l, cur.c, "unexpected character in a flow collection"));
        }
        let text: String = line[cur.c..end].iter().collect();
        cur.c = end;
        Ok(text)
    }
}
