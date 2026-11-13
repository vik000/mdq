//! Variable formatting, Markdown escaping and `{{ }}` interpolation
//! (spec section 5 "Variables"; decisions G26/G27 in docs/TEST_PLAN.md).
//!
//! * `format_scalar`: string unchanged, bool `true`/`false`, null empty,
//!   number canonical decimal (no exponent, no trailing zeros); list/mapping
//!   are rejected (MDQ_UNSUPPORTED_FEATURE).
//! * `escape_inline`: backslash/entity escaping so the text renders literally.
//! * `interpolate`: expands `{{a.b.c}}` outside code (fenced, indented, inline),
//!   HTML (block and inline) and backslash-escaped delimiters. Block detection
//!   covers top-level constructs only; container-nested code is E3 (G27).

use crate::diag::{Code, Diagnostic};
use crate::yaml::Yaml;

type Res<T> = Result<T, Diagnostic>;

const MAX_INPUT: usize = 16 * 1024 * 1024;
pub(crate) const MAX_OUTPUT: usize = 64 * 1024 * 1024;
const MAX_NUMBER_DIGITS: usize = 400;
const MAX_EXPONENT: i64 = 400;

fn err(code: Code, msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(code, msg)
}

// ---------------------------------------------------------------- numbers

/// Canonical plain decimal text of a finite decimal literal
/// (`[+-]digits[.digits][e[+-]digits]`, also `.5`): no exponent, no trailing
/// zeros, no leading zeros, `-0` is `0`.
pub fn canonical_number(s: &str) -> Res<String> {
    let bad = || err(Code::MetadataError, format!("not a finite decimal number: {:?}", s));
    let limit = || err(Code::ResourceLimit, "number has too many digits or too large an exponent");
    let b = s.as_bytes();
    let mut i = 0;
    let mut neg = false;
    if i < b.len() && (b[i] == b'+' || b[i] == b'-') {
        neg = b[i] == b'-';
        i += 1;
    }
    let mut digits = String::new();
    let mut int_len: i64 = 0;
    let mut seen_point = false;
    let mut any = false;
    while i < b.len() {
        let ch = b[i];
        if ch.is_ascii_digit() {
            digits.push(ch as char);
            any = true;
            if !seen_point {
                int_len += 1;
            }
        } else if ch == b'.' && !seen_point {
            seen_point = true;
        } else {
            break;
        }
        i += 1;
    }
    if !any {
        return Err(bad());
    }
    let mut exp: i64 = 0;
    if i < b.len() && (b[i] == b'e' || b[i] == b'E') {
        i += 1;
        let mut eneg = false;
        if i < b.len() && (b[i] == b'+' || b[i] == b'-') {
            eneg = b[i] == b'-';
            i += 1;
        }
        let start = i;
        while i < b.len() && b[i].is_ascii_digit() {
            exp = exp * 10 + (b[i] - b'0') as i64;
            if exp > 100_000 {
                return Err(limit());
            }
            i += 1;
        }
        if i == start {
            return Err(bad());
        }
        if eneg {
            exp = -exp;
        }
    }
    if i != b.len() {
        return Err(bad());
    }
    if digits.len() > MAX_NUMBER_DIGITS {
        return Err(limit());
    }
    let lead = digits.bytes().take_while(|&d| d == b'0').count();
    let point = int_len + exp - lead as i64;
    let rest = digits[lead..].trim_end_matches('0');
    if rest.is_empty() {
        return Ok("0".to_string());
    }
    // Magnitude limit: at most 401 integer digits or 399 leading fraction zeros.
    if point > MAX_EXPONENT + 1 || point < -(MAX_EXPONENT - 1) {
        return Err(limit());
    }
    let len = rest.len() as i64;
    let mut out = String::new();
    if neg {
        out.push('-');
    }
    if point <= 0 {
        out.push_str("0.");
        for _ in 0..(-point) {
            out.push('0');
        }
        out.push_str(rest);
    } else if point >= len {
        out.push_str(rest);
        for _ in 0..(point - len) {
            out.push('0');
        }
    } else {
        out.push_str(&rest[..point as usize]);
        out.push('.');
        out.push_str(&rest[point as usize..]);
    }
    Ok(out)
}

/// Text form of a scalar variable value.
pub fn format_scalar(v: &Yaml) -> Res<String> {
    match v {
        Yaml::Null => Ok(String::new()),
        Yaml::Bool(b) => Ok((if *b { "true" } else { "false" }).to_string()),
        Yaml::Str(s) => Ok(s.clone()),
        Yaml::Number(n) => canonical_number(n),
        Yaml::Seq(_) => Err(err(
            Code::UnsupportedFeature,
            "a list cannot be interpolated without an explicit renderer",
        )),
        Yaml::Map(_) => Err(err(
            Code::UnsupportedFeature,
            "a mapping cannot be interpolated without an explicit renderer",
        )),
    }
}

/// Look up a dotted name (`a.b.c`) in a mapping.
pub fn lookup<'a>(vars: &'a Yaml, name: &str) -> Option<&'a Yaml> {
    let mut cur = vars;
    for seg in name.split('.') {
        match cur {
            Yaml::Map(entries) => {
                let found = entries.iter().find(|entry| entry.0.as_str() == seg)?;
                cur = &found.1;
            }
            _ => return None,
        }
    }
    Some(cur)
}

// ---------------------------------------------------------------- escaping

fn www_before(c: &[char], i: usize) -> bool {
    if i < 3 {
        return false;
    }
    let w: String = c[i - 3..i].iter().collect::<String>().to_ascii_lowercase();
    w == "www"
}

/// A `#` that starts a run of `#` reaching the end of its line right after a space or tab: in an
/// ATX heading it would become the optional closing sequence and vanish from the text.
fn closing_hash(c: &[char], i: usize) -> bool {
    if i == 0 || (c[i - 1] != ' ' && c[i - 1] != '\t') {
        return false;
    }
    c[i..].iter().take_while(|&&x| x != '\n' && x != '\r').all(|&x| x == '#')
}

/// Escape `s` so that, written into inline Markdown, it renders as the same
/// text. `at_line_start` says whether the value starts where a block marker
/// could start (line start, or after `> `, `- `, `1. `, `# `); the rules for
/// the start of every later line inside the value always apply.
pub fn escape_inline(s: &str, at_line_start: bool) -> Res<String> {
    let c: Vec<char> = s.chars().collect();
    let n = c.len();
    let mut out = String::with_capacity(s.len() + s.len() / 4 + 4);
    let mut ls = at_line_start;
    let mut digits_ok = at_line_start;
    let mut ndigits: usize = 0;
    let mut i = 0;
    while i < n {
        let ch = c[i];
        if ch == '\0' {
            return Err(err(Code::InvalidSyntax, "a NUL character cannot be written into Markdown text"));
        }
        if ch == '\n' || ch == '\r' {
            out.push(ch);
            if ch == '\r' && i + 1 < n && c[i + 1] == '\n' {
                out.push('\n');
                i += 1;
            }
            i += 1;
            ls = true;
            digits_ok = true;
            ndigits = 0;
            continue;
        }
        if ch == ' ' || ch == '\t' {
            let eol = i + 1 == n || c[i + 1] == '\n' || c[i + 1] == '\r';
            if ls || eol {
                out.push_str(if ch == ' ' { "&#32;" } else { "&#9;" });
            } else {
                out.push(ch);
            }
            ls = false;
            digits_ok = false;
            i += 1;
            continue;
        }
        if ch.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&ch) {
            // Unicode white space (NBSP, U+2028, ...) is trimmed by some parsers at the
            // edges of a line: protect it with a numeric character reference.
            let eol = i + 1 == n || c[i + 1] == '\n' || c[i + 1] == '\r';
            if ls || eol {
                out.push_str(&format!("&#{};", ch as u32));
            } else {
                out.push(ch);
            }
            ls = false;
            digits_ok = false;
            i += 1;
            continue;
        }
        let prev_eq = i > 0 && c[i - 1] == '=';
        let next_eq = i + 1 < n && c[i + 1] == '=';
        let is_list_num = digits_ok && ndigits >= 1 && ndigits <= 9;
        let esc = match ch {
            '\\' | '`' | '*' | '_' | '~' | '[' | ']' | '<' | '|' | '{' | '$' | '&' | '@' => true,
            '#' => ls || closing_hash(&c, i),
            '+' | '-' | '>' => ls,
            '=' => ls || prev_eq || next_eq,
            ':' => ls || (i + 2 < n && c[i + 1] == '/' && c[i + 2] == '/'),
            '.' => is_list_num || www_before(&c, i),
            ')' => is_list_num,
            _ => false,
        };
        if esc {
            out.push('\\');
        }
        out.push(ch);
        if ch.is_ascii_digit() && digits_ok {
            ndigits += 1;
        } else {
            digits_ok = false;
        }
        ls = false;
        i += 1;
    }
    Ok(out)
}

/// True when `prefix` (the output so far on the current line) holds only
/// whitespace and block markers (`>`, `- `, `+ `, `* `, `1. `, `# `).
fn block_start_prefix(p: &str) -> bool {
    let c: Vec<char> = p.chars().collect();
    let n = c.len();
    let mut i = 0;
    loop {
        while i < n && (c[i] == ' ' || c[i] == '\t') {
            i += 1;
        }
        if i >= n {
            return true;
        }
        match c[i] {
            '>' => {
                i += 1;
            }
            '-' | '+' | '*' if i + 1 < n && (c[i + 1] == ' ' || c[i + 1] == '\t') => {
                i += 1;
            }
            '#' => {
                let mut j = i;
                while j < n && c[j] == '#' {
                    j += 1;
                }
                if j - i <= 6 && j < n && (c[j] == ' ' || c[j] == '\t') {
                    i = j;
                } else {
                    return false;
                }
            }
            d if d.is_ascii_digit() => {
                let mut j = i;
                while j < n && c[j].is_ascii_digit() {
                    j += 1;
                }
                if j - i <= 9 && j + 1 < n && (c[j] == '.' || c[j] == ')') && (c[j + 1] == ' ' || c[j + 1] == '\t') {
                    i = j + 1;
                } else {
                    return false;
                }
            }
            _ => return false,
        }
    }
}

// ---------------------------------------------------------------- HTML helpers

pub(crate) fn starts_with(c: &[char], i: usize, b: usize, pat: &str) -> bool {
    let mut k = i;
    for p in pat.chars() {
        if k >= b || c[k] != p {
            return false;
        }
        k += 1;
    }
    true
}

fn find(c: &[char], from: usize, b: usize, pat: &str) -> Option<usize> {
    let mut k = from;
    while k < b {
        if starts_with(c, k, b, pat) {
            return Some(k);
        }
        k += 1;
    }
    None
}

fn is_ws_html(ch: char) -> bool {
    ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r'
}

/// End (exclusive) of a complete open or closing tag starting at `i` (`<`).
pub(crate) fn tag_end(c: &[char], i: usize, b: usize) -> Option<usize> {
    let mut j = i + 1;
    let closing = j < b && c[j] == '/';
    if closing {
        j += 1;
    }
    if !(j < b && c[j].is_ascii_alphabetic()) {
        return None;
    }
    j += 1;
    while j < b && (c[j].is_ascii_alphanumeric() || c[j] == '-') {
        j += 1;
    }
    if closing {
        while j < b && is_ws_html(c[j]) {
            j += 1;
        }
        return if j < b && c[j] == '>' { Some(j + 1) } else { None };
    }
    loop {
        let before = j;
        while j < b && is_ws_html(c[j]) {
            j += 1;
        }
        let had_ws = j > before;
        if j < b && c[j] == '>' {
            return Some(j + 1);
        }
        if j + 1 < b && c[j] == '/' && c[j + 1] == '>' {
            return Some(j + 2);
        }
        if !had_ws {
            return None;
        }
        if !(j < b && (c[j].is_ascii_alphabetic() || c[j] == '_' || c[j] == ':')) {
            return None;
        }
        j += 1;
        while j < b && (c[j].is_ascii_alphanumeric() || c[j] == '_' || c[j] == '.' || c[j] == ':' || c[j] == '-') {
            j += 1;
        }
        let mut k = j;
        while k < b && is_ws_html(c[k]) {
            k += 1;
        }
        if k < b && c[k] == '=' {
            k += 1;
            while k < b && is_ws_html(c[k]) {
                k += 1;
            }
            if k >= b {
                return None;
            }
            if c[k] == '"' || c[k] == '\'' {
                let q = c[k];
                k += 1;
                while k < b && c[k] != q {
                    k += 1;
                }
                if k >= b {
                    return None;
                }
                k += 1;
            } else {
                let st = k;
                while k < b && !is_ws_html(c[k]) && !matches!(c[k], '"' | '\'' | '=' | '<' | '>' | '`') {
                    k += 1;
                }
                if k == st {
                    return None;
                }
            }
            j = k;
        }
    }
}

/// End (exclusive) of inline raw HTML or an autolink starting at `i` (`<`).
fn html_inline_end(c: &[char], i: usize, b: usize) -> Option<usize> {
    if starts_with(c, i, b, "<!--") {
        if starts_with(c, i, b, "<!-->") {
            return Some(i + 5);
        }
        if starts_with(c, i, b, "<!--->") {
            return Some(i + 6);
        }
        return find(c, i + 4, b, "-->").map(|p| p + 3);
    }
    if starts_with(c, i, b, "<?") {
        return find(c, i + 2, b, "?>").map(|p| p + 2);
    }
    if starts_with(c, i, b, "<![CDATA[") {
        return find(c, i + 9, b, "]]>").map(|p| p + 3);
    }
    if starts_with(c, i, b, "<!") && i + 2 < b && c[i + 2].is_ascii_alphabetic() {
        return find(c, i + 2, b, ">").map(|p| p + 1);
    }
    if let Some(e) = tag_end(c, i, b) {
        return Some(e);
    }
    // URI autolink: <scheme:...>
    let mut j = i + 1;
    if j < b && c[j].is_ascii_alphabetic() {
        j += 1;
        while j < b && (c[j].is_ascii_alphanumeric() || c[j] == '+' || c[j] == '.' || c[j] == '-') {
            j += 1;
        }
        let scheme_len = j - (i + 1);
        if scheme_len >= 2 && scheme_len <= 32 && j < b && c[j] == ':' {
            j += 1;
            while j < b && c[j] != '>' && c[j] != '<' && !is_ws_html(c[j]) && !c[j].is_control() {
                j += 1;
            }
            if j < b && c[j] == '>' {
                return Some(j + 1);
            }
        }
    }
    // e-mail autolink: <local@domain>
    let mut j = i + 1;
    let st = j;
    while j < b && (c[j].is_ascii_alphanumeric() || ".!#$%&'*+/=?^_`{|}~-".contains(c[j])) {
        j += 1;
    }
    if j > st && j < b && c[j] == '@' {
        j += 1;
        let ds = j;
        while j < b && (c[j].is_ascii_alphanumeric() || c[j] == '-' || c[j] == '.') {
            j += 1;
        }
        if j > ds && j < b && c[j] == '>' {
            return Some(j + 1);
        }
    }
    None
}

// ---------------------------------------------------------------- directive scan

/// One live `{{name}}` directive. `start`/`end` are char offsets into the source text.
#[derive(Debug, Clone)]
pub struct Directive {
    pub start: usize,
    pub end: usize,
    pub name: String,
    pub line: u32,
    pub column: u32,
}

fn lc(starts: &[usize], first_line: u32, pos: usize) -> (u32, u32) {
    let idx = match starts.binary_search(&pos) {
        Ok(i) => i,
        Err(i) => i - 1,
    };
    (first_line + idx as u32, (pos - starts[idx]) as u32 + 1)
}

/// Live directives of one inline run `v` (`vmap` maps its chars to source offsets). Stops at the
/// first malformed `{{` and returns its error alongside the directives found before it.
fn scan_leaf(
    v: &[char],
    vmap: &[usize],
    starts: &[usize],
    first_line: u32,
    out: &mut Vec<Directive>,
) -> Option<Diagnostic> {
    let c = v;
    let b = v.len();
    let mut i = 0;
    while i < b {
        let ch = c[i];
        if ch == '\\' {
            if i + 1 < b && c[i + 1].is_ascii_punctuation() {
                i += if c[i + 1] == '{' && i + 2 < b && c[i + 2] == '{' { 3 } else { 2 };
            } else {
                i += 1;
            }
        } else if ch == '`' {
            let mut j = i;
            while j < b && c[j] == '`' {
                j += 1;
            }
            let n = j - i;
            let mut k = j;
            let mut found: Option<usize> = None;
            while k < b {
                if c[k] == '`' {
                    let mut m = k;
                    while m < b && c[m] == '`' {
                        m += 1;
                    }
                    if m - k == n {
                        found = Some(m);
                        break;
                    }
                    k = m;
                } else {
                    k += 1;
                }
            }
            i = found.unwrap_or(j);
        } else if ch == '<' {
            i = html_inline_end(c, i, b).unwrap_or(i + 1);
        } else if ch == '{' && i + 1 < b && c[i + 1] == '{' {
            let (line, col) = lc(starts, first_line, vmap[i]);
            let bad = || {
                err(
                    Code::InvalidSyntax,
                    "malformed variable reference: expected {{name}} with dotted ASCII identifier segments (write \\{{ for literal braces)",
                )
                .at_position(line, col)
            };
            let mut j = i + 2;
            while j < b && (c[j] == ' ' || c[j] == '\t') {
                j += 1;
            }
            let name_start = j;
            loop {
                if j < b && (c[j].is_ascii_alphabetic() || c[j] == '_') {
                    j += 1;
                    while j < b && (c[j].is_ascii_alphanumeric() || c[j] == '_') {
                        j += 1;
                    }
                } else {
                    return Some(bad());
                }
                if j < b && c[j] == '.' {
                    j += 1;
                } else {
                    break;
                }
            }
            let name_end = j;
            while j < b && (c[j] == ' ' || c[j] == '\t') {
                j += 1;
            }
            if !(j + 1 < b && c[j] == '}' && c[j + 1] == '}') {
                return Some(bad());
            }
            out.push(Directive {
                start: vmap[i],
                end: vmap[j + 1] + 1,
                name: c[name_start..name_end].iter().collect(),
                line,
                column: col,
            });
            i = j + 2;
        } else {
            i += 1;
        }
    }
    None
}

/// All live directives in source order, plus the error of the first malformed `{{` (if any).
/// Directives inside code, HTML blocks, code spans and inline HTML are not live. A block-structure
/// error (nesting limit) is returned as `Err`.
fn scan_all(c: &[char], starts: &[usize], ends: &[usize], first_line: u32) -> Res<(Vec<Directive>, Option<Diagnostic>)> {
    let leaves = crate::blocks::parse(c, starts, ends, first_line)?;
    let mut found: Vec<Directive> = Vec::new();
    for leaf in leaves {
        if leaf.kind != crate::blocks::Kind::Paragraph && leaf.kind != crate::blocks::Kind::Heading {
            continue;
        }
        let mut v: Vec<char> = Vec::new();
        let mut vmap: Vec<usize> = Vec::new();
        let mut prev_end = 0usize;
        for (idx, &(s, e)) in leaf.segments.iter().enumerate() {
            if idx > 0 {
                v.push('\n');
                vmap.push(prev_end);
            }
            for p in s..e {
                v.push(c[p]);
                vmap.push(p);
            }
            prev_end = e;
        }
        if let Some(d) = scan_leaf(&v, &vmap, starts, first_line, &mut found) {
            return Ok((found, Some(d)));
        }
    }
    Ok((found, None))
}

/// Live directives of `text`; a malformed `{{` is an error.
pub fn directives(text: &str, first_line: u32) -> Res<Vec<Directive>> {
    if text.len() > MAX_INPUT {
        return Err(err(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = crate::blocks::line_bounds(&c);
    let (found, pending) = scan_all(&c, &starts, &ends, first_line)?;
    match pending {
        Some(d) => Err(d),
        None => Ok(found),
    }
}

/// Variables plus the loop variables in scope (innermost last). A loop variable hides every
/// variable of the same first name segment.
pub struct Scope<'a> {
    vars: &'a Yaml,
    loops: Vec<(String, &'a Yaml)>,
}

impl<'a> Scope<'a> {
    pub fn new(vars: &'a Yaml) -> Scope<'a> {
        Scope { vars, loops: Vec::new() }
    }

    pub fn push(&mut self, name: &str, value: &'a Yaml) {
        self.loops.push((name.to_string(), value));
    }

    pub fn pop(&mut self) {
        self.loops.pop();
    }

    pub fn get(&self, name: &str) -> Option<&'a Yaml> {
        let (first, rest) = match name.split_once('.') {
            Some((a, b)) => (a, Some(b)),
            None => (name, None),
        };
        for (n, v) in self.loops.iter().rev() {
            if n == first {
                return match rest {
                    None => Some(*v),
                    Some(r) => lookup(v, r),
                };
            }
        }
        lookup(self.vars, name)
    }
}

/// Expand `{{name}}` references in `text` (the Markdown body; `first_line` is
/// the 1-based line number of its first line, for diagnostics).
pub fn interpolate(text: &str, vars: &Yaml, first_line: u32) -> Res<String> {
    interpolate_with(text, &Scope::new(vars), first_line)
}

/// `interpolate` with loop variables in scope.
pub fn interpolate_with(text: &str, scope: &Scope, first_line: u32) -> Res<String> {
    if text.len() > MAX_INPUT {
        return Err(err(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = crate::blocks::line_bounds(&c);
    let (found, pending) = scan_all(&c, &starts, &ends, first_line)?;
    let mut out = String::with_capacity(text.len());
    let mut pos = 0usize;
    for d in found {
        let at = |dd: Diagnostic| dd.at_position(d.line, d.column);
        let value = match scope.get(&d.name) {
            Some(v) => v,
            None => {
                return Err(err(Code::UndefinedVariable, format!("undefined variable \"{}\"", d.name))
                    .at_position(d.line, d.column));
            }
        };
        let text_val = match format_scalar(value) {
            Ok(t) => t,
            Err(e) => {
                return Err(err(e.code, format!("{} (variable \"{}\")", e.message, d.name))
                    .at_position(d.line, d.column));
            }
        };
        out.extend(c[pos..d.start].iter());
        let prefix = match out.rfind(|ch: char| ch == '\n' || ch == '\r') {
            Some(p) => &out[p + 1..],
            None => out.as_str(),
        };
        let escaped = escape_inline(&text_val, block_start_prefix(prefix)).map_err(at)?;
        out.push_str(&escaped);
        pos = d.end;
        if out.len() > MAX_OUTPUT {
            return Err(err(Code::ResourceLimit, "interpolated output is too large").at_position(d.line, d.column));
        }
    }
    if let Some(d) = pending {
        return Err(d);
    }
    out.extend(c[pos..].iter());
    if out.len() > MAX_OUTPUT {
        return Err(err(Code::ResourceLimit, "interpolated output is too large"));
    }
    Ok(out)
}
