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
const MAX_OUTPUT: usize = 64 * 1024 * 1024;
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
            '#' | '+' | '-' | '>' => ls,
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

fn starts_with(c: &[char], i: usize, b: usize, pat: &str) -> bool {
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
fn tag_end(c: &[char], i: usize, b: usize) -> Option<usize> {
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

// ---------------------------------------------------------------- block scan

const HTML_BLOCK_NAMES: &[&str] = &[
    "address", "article", "aside", "base", "basefont", "blockquote", "body", "caption", "center", "col",
    "colgroup", "dd", "details", "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "frame", "frameset", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hr",
    "html", "iframe", "legend", "li", "link", "main", "menu", "menuitem", "nav", "noframes", "ol",
    "optgroup", "option", "p", "param", "search", "section", "summary", "table", "tbody", "td", "tfoot",
    "th", "thead", "title", "tr", "track", "ul",
];

/// (indent in columns, index of the first non-blank char) of the line [s, e).
fn indent_of(c: &[char], s: usize, e: usize) -> (usize, usize) {
    let mut col = 0;
    let mut i = s;
    while i < e {
        if c[i] == ' ' {
            col += 1;
        } else if c[i] == '\t' {
            col += 4 - (col % 4);
        } else {
            break;
        }
        i += 1;
    }
    (col, i)
}

fn rest_blank(c: &[char], from: usize, e: usize) -> bool {
    c[from..e].iter().all(|&ch| ch == ' ' || ch == '\t')
}

/// Opening code fence at `fi`: (fence char, run length).
fn fence_start(c: &[char], fi: usize, e: usize) -> Option<(char, usize)> {
    let ch = c[fi];
    if ch != '`' && ch != '~' {
        return None;
    }
    let mut j = fi;
    while j < e && c[j] == ch {
        j += 1;
    }
    let len = j - fi;
    if len < 3 {
        return None;
    }
    if ch == '`' && c[j..e].contains(&'`') {
        return None;
    }
    Some((ch, len))
}

fn is_fence_close(c: &[char], s: usize, e: usize, ch: char, len: usize) -> bool {
    let (indent, fi) = indent_of(c, s, e);
    if indent >= 4 || fi >= e || c[fi] != ch {
        return false;
    }
    let mut j = fi;
    while j < e && c[j] == ch {
        j += 1;
    }
    j - fi >= len && rest_blank(c, j, e)
}

fn is_atx(c: &[char], fi: usize, e: usize) -> bool {
    let mut j = fi;
    while j < e && c[j] == '#' {
        j += 1;
    }
    let n = j - fi;
    n >= 1 && n <= 6 && (j == e || c[j] == ' ' || c[j] == '\t')
}

fn is_thematic(c: &[char], fi: usize, e: usize) -> bool {
    let ch = c[fi];
    if ch != '*' && ch != '-' && ch != '_' {
        return false;
    }
    let mut count = 0;
    for &x in &c[fi..e] {
        if x == ch {
            count += 1;
        } else if x != ' ' && x != '\t' {
            return false;
        }
    }
    count >= 3
}

fn is_setext_eq(c: &[char], fi: usize, e: usize) -> bool {
    let mut j = fi;
    while j < e && c[j] == '=' {
        j += 1;
    }
    j > fi && rest_blank(c, j, e)
}

fn name_matches(c: &[char], from: usize, e: usize, name: &str) -> bool {
    let mut k = from;
    for p in name.chars() {
        if k >= e || c[k].to_ascii_lowercase() != p {
            return false;
        }
        k += 1;
    }
    true
}

/// HTML block start kind (1..=7) of the line whose first non-blank char `<` is at `fi`.
fn html_start(c: &[char], fi: usize, e: usize) -> Option<u8> {
    for name in ["script", "pre", "style", "textarea"] {
        if name_matches(c, fi + 1, e, name) {
            let after = fi + 1 + name.len();
            if after >= e || c[after] == ' ' || c[after] == '\t' || c[after] == '>' {
                return Some(1);
            }
        }
    }
    if starts_with(c, fi, e, "<!--") {
        return Some(2);
    }
    if starts_with(c, fi, e, "<?") {
        return Some(3);
    }
    if starts_with(c, fi, e, "<![CDATA[") {
        return Some(5);
    }
    if starts_with(c, fi, e, "<!") && fi + 2 < e && c[fi + 2].is_ascii_alphabetic() {
        return Some(4);
    }
    let mut j = fi + 1;
    if j < e && c[j] == '/' {
        j += 1;
    }
    for name in HTML_BLOCK_NAMES.iter() {
        let name: &str = name;
        if name_matches(c, j, e, name) {
            let after = j + name.len();
            if after >= e
                || c[after] == ' '
                || c[after] == '\t'
                || c[after] == '>'
                || (c[after] == '/' && after + 1 < e && c[after + 1] == '>')
            {
                return Some(6);
            }
        }
    }
    if let Some(end) = tag_end(c, fi, e) {
        if rest_blank(c, end, e) {
            return Some(7);
        }
    }
    None
}

fn html_ends_on_line(c: &[char], s: usize, e: usize, kind: u8) -> bool {
    let lower: String = c[s..e].iter().collect::<String>().to_ascii_lowercase();
    match kind {
        1 => ["</script>", "</pre>", "</style>", "</textarea>"].iter().any(|n| lower.contains(*n)),
        2 => lower.contains("-->"),
        3 => lower.contains("?>"),
        4 => lower.contains('>'),
        5 => lower.contains("]]>"),
        _ => false,
    }
}

#[derive(Clone, Copy)]
enum Mode {
    Normal,
    Fence(char, usize),
    Html(u8),
}

struct Doc {
    c: Vec<char>,
    starts: Vec<usize>,
    first_line: u32,
}

impl Doc {
    fn lc(&self, pos: usize) -> (u32, u32) {
        let idx = match self.starts.binary_search(&pos) {
            Ok(i) => i,
            Err(i) => i - 1,
        };
        (self.first_line + idx as u32, (pos - self.starts[idx]) as u32 + 1)
    }
}

fn line_bounds(c: &[char]) -> (Vec<usize>, Vec<usize>) {
    let n = c.len();
    let mut starts = vec![0usize];
    let mut ends: Vec<usize> = Vec::new();
    let mut i = 0;
    while i < n {
        if c[i] == '\n' {
            ends.push(i);
            starts.push(i + 1);
        } else if c[i] == '\r' {
            ends.push(i);
            if i + 1 < n && c[i + 1] == '\n' {
                i += 1;
            }
            starts.push(i + 1);
        }
        i += 1;
    }
    ends.push(n);
    (starts, ends)
}

struct Ctx<'a> {
    doc: &'a Doc,
    vars: &'a Yaml,
}

impl<'a> Ctx<'a> {
    fn push_range(&self, out: &mut String, a: usize, b: usize) {
        out.extend(self.doc.c[a..b].iter());
    }

    /// Expand directives in the inline run c[a..b].
    fn inline(&self, a: usize, b: usize, out: &mut String) -> Res<()> {
        let c = &self.doc.c;
        let mut i = a;
        while i < b {
            let ch = c[i];
            if ch == '\\' {
                if i + 1 < b && c[i + 1].is_ascii_punctuation() {
                    let take = if c[i + 1] == '{' && i + 2 < b && c[i + 2] == '{' { 3 } else { 2 };
                    self.push_range(out, i, i + take);
                    i += take;
                } else {
                    out.push('\\');
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
                match found {
                    Some(m) => {
                        self.push_range(out, i, m);
                        i = m;
                    }
                    None => {
                        self.push_range(out, i, j);
                        i = j;
                    }
                }
            } else if ch == '<' {
                match html_inline_end(c, i, b) {
                    Some(m) => {
                        self.push_range(out, i, m);
                        i = m;
                    }
                    None => {
                        out.push('<');
                        i += 1;
                    }
                }
            } else if ch == '{' && i + 1 < b && c[i + 1] == '{' {
                i = self.directive(i, b, out)?;
            } else {
                out.push(ch);
                i += 1;
            }
        }
        Ok(())
    }

    fn directive(&self, i: usize, b: usize, out: &mut String) -> Res<usize> {
        let c = &self.doc.c;
        let (line, col) = self.doc.lc(i);
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
                return Err(bad());
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
            return Err(bad());
        }
        let name: String = c[name_start..name_end].iter().collect();
        let value = match lookup(self.vars, &name) {
            Some(v) => v,
            None => {
                return Err(err(Code::UndefinedVariable, format!("undefined variable \"{}\"", name))
                    .at_position(line, col));
            }
        };
        let text = match format_scalar(value) {
            Ok(t) => t,
            Err(d) => {
                return Err(err(d.code, format!("{} (variable \"{}\")", d.message, name)).at_position(line, col));
            }
        };
        let at_start = {
            let prefix = match out.rfind(|ch: char| ch == '\n' || ch == '\r') {
                Some(p) => &out[p + 1..],
                None => out.as_str(),
            };
            block_start_prefix(prefix)
        };
        let escaped = escape_inline(&text, at_start).map_err(|d| d.at_position(line, col))?;
        out.push_str(&escaped);
        if out.len() > MAX_OUTPUT {
            return Err(err(Code::ResourceLimit, "interpolated output is too large").at_position(line, col));
        }
        Ok(j + 2)
    }

    fn flush(&self, chunk: &mut Option<usize>, upto: usize, starts: &[usize], out: &mut String) -> Res<()> {
        if let Some(first) = chunk.take() {
            let a = starts[first];
            let b = if upto < starts.len() { starts[upto] } else { self.doc.c.len() };
            self.inline(a, b, out)?;
        }
        Ok(())
    }
}

/// Expand `{{name}}` references in `text` (the Markdown body; `first_line` is
/// the 1-based line number of its first line, for diagnostics).
pub fn interpolate(text: &str, vars: &Yaml, first_line: u32) -> Res<String> {
    if text.len() > MAX_INPUT {
        return Err(err(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let doc = Doc { c, starts, first_line };
    let ctx = Ctx { doc: &doc, vars };
    let nl = doc.starts.len();
    let total = doc.c.len();
    let mut out = String::with_capacity(text.len());
    let mut mode = Mode::Normal;
    let mut para = false;
    let mut chunk: Option<usize> = None;

    for k in 0..nl {
        let s = doc.starts[k];
        let e = ends[k];
        let term_end = if k + 1 < nl { doc.starts[k + 1] } else { total };
        let c = &doc.c;

        match mode {
            Mode::Fence(ch, len) => {
                ctx.push_range(&mut out, s, term_end);
                if is_fence_close(c, s, e, ch, len) {
                    mode = Mode::Normal;
                }
                continue;
            }
            Mode::Html(kind) => {
                let (_, fi) = indent_of(c, s, e);
                if kind >= 6 && fi >= e {
                    mode = Mode::Normal; // blank line ends the block; handled below
                } else {
                    ctx.push_range(&mut out, s, term_end);
                    if kind <= 5 && html_ends_on_line(c, s, e, kind) {
                        mode = Mode::Normal;
                    }
                    continue;
                }
            }
            Mode::Normal => {}
        }

        let (indent, fi) = indent_of(c, s, e);
        if fi >= e {
            ctx.flush(&mut chunk, k, &doc.starts, &mut out)?;
            ctx.push_range(&mut out, s, term_end);
            para = false;
            continue;
        }
        if indent >= 4 && !para {
            ctx.flush(&mut chunk, k, &doc.starts, &mut out)?;
            ctx.push_range(&mut out, s, term_end);
            continue;
        }
        if indent < 4 {
            if let Some((fch, flen)) = fence_start(c, fi, e) {
                ctx.flush(&mut chunk, k, &doc.starts, &mut out)?;
                ctx.push_range(&mut out, s, term_end);
                mode = Mode::Fence(fch, flen);
                para = false;
                continue;
            }
            if c[fi] == '<' {
                if let Some(kind) = html_start(c, fi, e) {
                    if kind != 7 || !para {
                        ctx.flush(&mut chunk, k, &doc.starts, &mut out)?;
                        ctx.push_range(&mut out, s, term_end);
                        para = false;
                        if !(kind <= 5 && html_ends_on_line(c, fi, e, kind)) {
                            mode = Mode::Html(kind);
                        }
                        continue;
                    }
                }
            }
            if c[fi] == '#' && is_atx(c, fi, e) {
                ctx.flush(&mut chunk, k, &doc.starts, &mut out)?;
                chunk = Some(k);
                ctx.flush(&mut chunk, k + 1, &doc.starts, &mut out)?;
                para = false;
                continue;
            }
            if is_thematic(c, fi, e) || (para && is_setext_eq(c, fi, e)) {
                ctx.flush(&mut chunk, k, &doc.starts, &mut out)?;
                ctx.push_range(&mut out, s, term_end);
                para = false;
                continue;
            }
        }
        if chunk.is_none() {
            chunk = Some(k);
        }
        para = true;
    }
    ctx.flush(&mut chunk, nl, &doc.starts, &mut out)?;
    Ok(out)
}
