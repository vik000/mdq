//! Citations: BibTeX and CSL JSON entries, `[@key]` resolution, bibliography
//! (spec section 7; G39 in docs/TEST_PLAN.md).

use crate::blocks::{line_bounds, parse, Kind};
use crate::diag::{Code, Diagnostic};
use crate::headings::{collapse_ws, u_ws};
use crate::json::{self, Json};
use crate::links::{apply_edits, Edit};
use crate::rich::leaf_text;
use crate::vars::{escape_inline, html_inline_end};
use std::collections::{HashMap, HashSet};

type Res<T> = Result<T, Diagnostic>;

const MAX_INPUT: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Bibtex,
    CslJson,
}

impl Format {
    pub fn parse(s: &str) -> Option<Format> {
        match s {
            "bibtex" => Some(Format::Bibtex),
            "csl-json" => Some(Format::CslJson),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Name {
    pub family: String,
    pub given: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Entry {
    pub key: String,
    pub typ: String,
    pub authors: Vec<Name>,
    pub et_al: bool,
    pub year: Option<String>,
    pub title: Option<String>,
    pub container: Option<String>,
}

fn check(text: &str) -> Res<()> {
    if text.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    Ok(())
}

fn strip_ws(s: &str) -> String {
    s.trim_matches(u_ws).to_string()
}

/// The first run of exactly four ASCII digits.
fn year_of(s: &str) -> Option<String> {
    let c: Vec<char> = s.chars().collect();
    let mut i = 0;
    while i < c.len() {
        if c[i].is_ascii_digit() {
            let mut j = i;
            while j < c.len() && c[j].is_ascii_digit() {
                j += 1;
            }
            if j - i == 4 {
                return Some(c[i..j].iter().collect());
            }
            i = j;
        } else {
            i += 1;
        }
    }
    None
}

// ------------------------------------------------------------------ BibTeX
const MONTHS: [(&str, &str); 12] = [
    ("jan", "January"),
    ("feb", "February"),
    ("mar", "March"),
    ("apr", "April"),
    ("may", "May"),
    ("jun", "June"),
    ("jul", "July"),
    ("aug", "August"),
    ("sep", "September"),
    ("oct", "October"),
    ("nov", "November"),
    ("dec", "December"),
];

fn bib_clean(s: &str) -> String {
    let c: Vec<char> = s.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < c.len() {
        if c[i] == '\\' && i + 1 < c.len() && "&%$#_{}".contains(c[i + 1]) {
            out.push(c[i + 1]);
            i += 2;
        } else if c[i] == '{' || c[i] == '}' {
            i += 1;
        } else {
            out.push(c[i]);
            i += 1;
        }
    }
    collapse_ws(&out)
}

fn split_authors(raw: &str) -> Vec<String> {
    let c: Vec<char> = raw.chars().collect();
    let n = c.len();
    let mut parts: Vec<String> = Vec::new();
    let mut depth: i32 = 0;
    let mut cur = String::new();
    let mut i = 0;
    while i < n {
        let ch = c[i];
        if ch == '{' {
            depth += 1;
        } else if ch == '}' {
            depth = (depth - 1).max(0);
        }
        if depth == 0
            && matches!(ch, ' ' | '\t' | '\r' | '\n')
            && i + 3 < n
            && c[i + 1..i + 4].iter().collect::<String>().eq_ignore_ascii_case("and")
            && (i + 4 >= n || matches!(c[i + 4], ' ' | '\t' | '\r' | '\n'))
        {
            parts.push(std::mem::take(&mut cur));
            i += 4;
            continue;
        }
        cur.push(ch);
        i += 1;
    }
    parts.push(cur);
    parts.iter().map(|p| strip_ws(p)).filter(|p| !p.is_empty()).collect()
}

fn one_brace_group(p: &[char]) -> bool {
    let mut depth = 0;
    for (i, &ch) in p.iter().enumerate() {
        if ch == '{' {
            depth += 1;
        } else if ch == '}' {
            depth -= 1;
            if depth == 0 {
                return i == p.len() - 1;
            }
        }
    }
    false
}

fn bib_name(p: &str) -> Name {
    let c: Vec<char> = p.chars().collect();
    let mut depth: i32 = 0;
    let mut comma = None;
    for (i, &ch) in c.iter().enumerate() {
        if ch == '{' {
            depth += 1;
        } else if ch == '}' {
            depth = (depth - 1).max(0);
        } else if ch == ',' && depth == 0 {
            comma = Some(i);
            break;
        }
    }
    if let Some(k) = comma {
        let a: String = c[..k].iter().collect();
        let b: String = c[k + 1..].iter().collect();
        return Name { family: bib_clean(&a), given: bib_clean(&b) };
    }
    if c[0] == '{' && one_brace_group(&c) {
        return Name { family: bib_clean(p), given: String::new() };
    }
    let mut toks: Vec<String> = Vec::new();
    let mut depth: i32 = 0;
    let mut cur = String::new();
    for &ch in &c {
        if ch == '{' {
            depth += 1;
        } else if ch == '}' {
            depth = (depth - 1).max(0);
        }
        if depth == 0 && matches!(ch, ' ' | '\t' | '\r' | '\n') {
            if !cur.is_empty() {
                toks.push(std::mem::take(&mut cur));
            }
        } else {
            cur.push(ch);
        }
    }
    if !cur.is_empty() {
        toks.push(cur);
    }
    let last = toks.pop().unwrap_or_default();
    Name { family: bib_clean(&last), given: bib_clean(&toks.join(" ")) }
}

fn bib_entry(typ: &str, key: &str, fields: &HashMap<String, String>) -> Entry {
    let mut authors = Vec::new();
    let mut et_al = false;
    for p in split_authors(fields.get("author").map(|x| x.as_str()).unwrap_or("")) {
        if p == "others" {
            et_al = true;
        } else {
            authors.push(bib_name(&p));
        }
    }
    let mut year = fields.get("year").and_then(|y| year_of(y));
    if year.is_none() {
        year = fields.get("date").and_then(|y| year_of(y));
    }
    let title = fields.get("title").map(|t| bib_clean(t)).filter(|t| !t.is_empty());
    let container = ["journal", "booktitle", "publisher"]
        .iter()
        .find_map(|k| fields.get(*k).map(|v| bib_clean(v)))
        .filter(|t| !t.is_empty());
    Entry { key: key.to_string(), typ: typ.to_string(), authors, et_al, year, title, container }
}

struct Bib {
    c: Vec<char>,
}

impl Bib {
    fn bad_code(&self, code: Code, msg: &str, i: usize) -> Diagnostic {
        let line = 1 + self.c[..i].iter().filter(|&&x| x == '\n').count();
        let ls = self.c[..i].iter().rposition(|&x| x == '\n').map(|k| k + 1).unwrap_or(0);
        Diagnostic::new(code, format!("BibTeX: {}", msg)).at_position(line as u32, (i - ls + 1) as u32)
    }

    fn bad(&self, msg: &str, i: usize) -> Diagnostic {
        self.bad_code(Code::SourceInvalid, msg, i)
    }

    fn ws(&self, mut i: usize) -> usize {
        while i < self.c.len() && matches!(self.c[i], ' ' | '\t' | '\n' | '\r') {
            i += 1;
        }
        i
    }

    fn braced(&self, i: usize) -> Res<(usize, String)> {
        let mut depth = 0;
        for k in i..self.c.len() {
            if self.c[k] == '{' {
                depth += 1;
            } else if self.c[k] == '}' {
                depth -= 1;
                if depth == 0 {
                    return Ok((k + 1, self.c[i + 1..k].iter().collect()));
                }
            }
        }
        Err(self.bad("unbalanced braces", i))
    }

    fn quoted(&self, i: usize) -> Res<(usize, String)> {
        let mut depth = 0;
        for k in i + 1..self.c.len() {
            match self.c[k] {
                '{' => depth += 1,
                '}' => depth -= 1,
                '"' if depth == 0 => return Ok((k + 1, self.c[i + 1..k].iter().collect())),
                _ => {}
            }
        }
        Err(self.bad("unterminated string", i))
    }

    fn name_end(&self, mut k: usize) -> usize {
        while k < self.c.len() && (self.c[k].is_ascii_alphanumeric() || "_-:.".contains(self.c[k])) {
            k += 1;
        }
        k
    }

    fn value(&self, mut i: usize, macros: &HashMap<String, String>) -> Res<(usize, String)> {
        let n = self.c.len();
        let mut parts = String::new();
        loop {
            i = self.ws(i);
            if i >= n {
                return Err(self.bad("unexpected end of input", i));
            }
            let ch = self.c[i];
            if ch == '{' {
                let (j, v) = self.braced(i)?;
                parts.push_str(&v);
                i = j;
            } else if ch == '"' {
                let (j, v) = self.quoted(i)?;
                parts.push_str(&v);
                i = j;
            } else if ch.is_ascii_digit() {
                let mut k = i;
                while k < n && self.c[k].is_ascii_digit() {
                    k += 1;
                }
                parts.extend(self.c[i..k].iter());
                i = k;
            } else if ch.is_ascii_alphabetic() || ch == '_' {
                let k = self.name_end(i);
                let name: String = self.c[i..k].iter().collect::<String>().to_lowercase();
                match macros.get(&name) {
                    Some(v) => parts.push_str(v),
                    None => return Err(self.bad(&format!("undefined macro {:?}", name), i)),
                }
                i = k;
            } else {
                return Err(self.bad("expected a field value", i));
            }
            i = self.ws(i);
            if i < n && self.c[i] == '#' {
                i += 1;
                continue;
            }
            return Ok((i, parts));
        }
    }
}

fn bibtex_parse(text: &str) -> Res<Vec<Entry>> {
    check(text)?;
    let t = text.strip_prefix('\u{feff}').unwrap_or(text);
    let b = Bib { c: t.chars().collect() };
    let c = &b.c;
    let n = c.len();
    let mut macros: HashMap<String, String> = MONTHS.iter().map(|(a, b)| (a.to_string(), b.to_string())).collect();
    let mut out = Vec::new();
    let mut keys: HashSet<String> = HashSet::new();
    let mut i = 0;
    while i < n {
        if c[i] != '@' {
            i += 1;
            continue;
        }
        let start = i;
        i += 1;
        let mut k = i;
        while k < n && c[k].is_ascii_alphabetic() {
            k += 1;
        }
        let typ: String = c[i..k].iter().collect::<String>().to_lowercase();
        if typ.is_empty() {
            return Err(b.bad("missing entry type after '@'", start));
        }
        i = b.ws(k);
        if i >= n || (c[i] != '{' && c[i] != '(') {
            return Err(b.bad("expected '{' after the entry type", i));
        }
        let close = if c[i] == '{' { '}' } else { ')' };
        let open_at = i;
        if typ == "comment" || typ == "preamble" {
            if close == '}' {
                i = b.braced(i)?.0;
            } else {
                while i < n && c[i] != ')' {
                    i += 1;
                }
                i += 1;
            }
            continue;
        }
        i += 1;
        if typ == "string" {
            i = b.ws(i);
            let k = b.name_end(i);
            let name: String = c[i..k].iter().collect::<String>().to_lowercase();
            if name.is_empty() {
                return Err(b.bad("expected a macro name", i));
            }
            i = b.ws(k);
            if i >= n || c[i] != '=' {
                return Err(b.bad("expected '='", i));
            }
            let (j, v) = b.value(i + 1, &macros)?;
            macros.insert(name, v);
            i = b.ws(j);
            if i >= n || c[i] != close {
                return Err(b.bad(&format!("expected '{}'", close), i));
            }
            i += 1;
            continue;
        }
        i = b.ws(i);
        let mut k = i;
        while k < n && !",{}()\"= \t\r\n".contains(c[k]) {
            k += 1;
        }
        let key: String = c[i..k].iter().collect();
        if key.is_empty() {
            return Err(b.bad("missing citation key", i));
        }
        if !keys.insert(key.clone()) {
            return Err(b.bad_code(Code::DuplicateId, &format!("duplicate citation key {:?}", key), i));
        }
        i = b.ws(k);
        let mut fields: HashMap<String, String> = HashMap::new();
        if i < n && c[i] == ',' {
            i += 1;
        } else if i >= n || c[i] != close {
            return Err(b.bad("expected ',' after the citation key", i));
        }
        loop {
            i = b.ws(i);
            if i >= n {
                return Err(b.bad("unterminated entry", open_at));
            }
            if c[i] == close {
                i += 1;
                break;
            }
            let k = b.name_end(i);
            let name: String = c[i..k].iter().collect::<String>().to_lowercase();
            if name.is_empty() {
                return Err(b.bad("expected a field name", i));
            }
            let at = i;
            i = b.ws(k);
            if i >= n || c[i] != '=' {
                return Err(b.bad("expected '=' after the field name", i));
            }
            let (j, v) = b.value(i + 1, &macros)?;
            i = j;
            if fields.contains_key(&name) {
                return Err(b.bad(&format!("duplicate field {:?} in {:?}", name, key), at));
            }
            fields.insert(name, v);
            i = b.ws(i);
            if i < n && c[i] == ',' {
                i += 1;
            } else if i < n && c[i] == close {
                i += 1;
                break;
            } else {
                return Err(b.bad("expected ',' or the end of the entry", i));
            }
        }
        out.push(bib_entry(&typ, &key, &fields));
    }
    Ok(out)
}

// ---------------------------------------------------------------- CSL JSON
fn te(msg: String) -> Diagnostic {
    Diagnostic::new(Code::SourceTypeError, format!("CSL JSON: {}", msg))
}

fn as_str<'a>(v: &'a Json) -> Option<&'a str> {
    match v {
        Json::Str(s) => Some(s),
        _ => None,
    }
}

fn csl_parse(text: &str) -> Res<Vec<Entry>> {
    let root = json::parse(text, "CSL JSON")?;
    let Json::Arr(items) = root else {
        return Err(te("expected an array of items".into()));
    };
    let mut out = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for (k, it) in items.iter().enumerate() {
        if !matches!(it, Json::Obj(_)) {
            return Err(te(format!("item {} is not an object", k)));
        }
        let key = match it.get("id") {
            Some(Json::Num(n)) => n.clone(),
            Some(Json::Str(s)) if !s.is_empty() => s.clone(),
            _ => return Err(te(format!("item {} needs a non-empty string id", k))),
        };
        if !seen.insert(key.clone()) {
            return Err(Diagnostic::new(Code::DuplicateId, format!("CSL JSON: duplicate id {:?}", key)));
        }
        let mut authors = Vec::new();
        match it.get("author") {
            None | Some(Json::Null) => {}
            Some(Json::Arr(list)) => {
                for a in list {
                    if !matches!(a, Json::Obj(_)) {
                        return Err(te(format!("{}: author entries must be objects", key)));
                    }
                    let fam = a.get("family").and_then(as_str);
                    let giv = match a.get("given") {
                        None => Some(""),
                        Some(v) => as_str(v),
                    };
                    let lit = a.get("literal").and_then(as_str);
                    if let (Some(f), Some(g)) = (fam, giv) {
                        authors.push(Name { family: collapse_ws(f), given: collapse_ws(g) });
                    } else if let Some(l) = lit {
                        authors.push(Name { family: collapse_ws(l), given: String::new() });
                    } else {
                        return Err(te(format!("{}: an author needs family or literal", key)));
                    }
                }
            }
            Some(_) => return Err(te(format!("{}: author must be an array", key))),
        }
        let mut year = None;
        match it.get("issued") {
            None | Some(Json::Null) => {}
            Some(iss @ Json::Obj(_)) => match iss.get("date-parts") {
                None | Some(Json::Null) => {
                    for f in ["raw", "literal"] {
                        if let Some(s) = iss.get(f).and_then(as_str) {
                            year = year_of(s);
                            break;
                        }
                    }
                }
                Some(Json::Arr(dp)) => {
                    let first = match dp.first() {
                        Some(Json::Arr(a)) => a.first(),
                        _ => None,
                    };
                    match first {
                        Some(Json::Str(s)) | Some(Json::Num(s)) => year = year_of(s),
                        _ => return Err(te(format!("{}: bad date-parts", key))),
                    }
                }
                Some(_) => return Err(te(format!("{}: bad date-parts", key))),
            },
            Some(_) => return Err(te(format!("{}: issued must be an object", key))),
        }
        let s = |name: &str| -> Res<Option<String>> {
            match it.get(name) {
                None | Some(Json::Null) => Ok(None),
                Some(Json::Str(v)) => Ok(Some(collapse_ws(v)).filter(|v| !v.is_empty())),
                Some(_) => Err(te(format!("{}: {} must be a string", key, name))),
            }
        };
        let typ = s("type")?.unwrap_or_default();
        let title = s("title")?;
        let container = match s("container-title")? {
            Some(v) => Some(v),
            None => s("publisher")?,
        };
        out.push(Entry { key, typ, authors, et_al: false, year, title, container });
    }
    Ok(out)
}

/// Parse a bibliography in the given format.
pub fn bib_parse(text: &str, format: Format) -> Res<Vec<Entry>> {
    match format {
        Format::Bibtex => bibtex_parse(text),
        Format::CslJson => csl_parse(text),
    }
}

// --------------------------------------------------------------- resolving
struct Cite {
    key: String,
    loc: Option<String>,
    start: usize,
    end: usize,
    line: usize,
    col: usize,
}

fn key0(ch: char) -> bool {
    ch.is_ascii_alphanumeric() || ch == '_'
}

fn key_any(ch: char) -> bool {
    key0(ch) || ":.+/-".contains(ch)
}

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

fn cite_items(c: &[char], starts: &[usize], leaves: &[crate::blocks::Leaf]) -> Vec<Cite> {
    let mut out = Vec::new();
    for leaf in leaves {
        if !matches!(leaf.kind, Kind::Paragraph | Kind::Heading) {
            continue;
        }
        if !leaf.segments.iter().any(|&(x, y)| c[x..y].windows(2).any(|w| w[0] == '[' && w[1] == '@')) {
            continue;
        }
        let a = leaf.first_line;
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
            } else if ch == '[' && i + 1 < n && s[i + 1] == '@' {
                let mut j = i + 2;
                if j >= n || !key0(s[j]) {
                    i += 1;
                    continue;
                }
                while j < n && key_any(s[j]) {
                    j += 1;
                }
                while ".:+/-".contains(s[j - 1]) {
                    j -= 1;
                }
                let key: String = s[i + 2..j].iter().collect();
                let loc;
                let end;
                if j < n && s[j] == ',' {
                    let mut k = j + 1;
                    while k < n && s[k] != '[' && s[k] != ']' && s[k] != '\n' {
                        k += 1;
                    }
                    if k >= n || s[k] != ']' {
                        i += 1;
                        continue;
                    }
                    let l: String = s[j + 1..k].iter().collect::<String>().trim_matches(|x| x == ' ' || x == '\t').to_string();
                    if l.is_empty() {
                        i += 1;
                        continue;
                    }
                    loc = Some(l);
                    end = k + 1;
                } else if j < n && s[j] == ']' {
                    loc = None;
                    end = j + 1;
                } else {
                    i += 1;
                    continue;
                }
                if end < n && "([:".contains(s[end]) {
                    i = end;
                    continue;
                }
                let line = a + s[..i].iter().filter(|&&x| x == '\n').count();
                out.push(Cite { key, loc, start: origin[i], end: origin[end - 1] + 1, line, col: origin[i] - starts[line] + 1 });
                i = end;
            } else {
                i += 1;
            }
        }
    }
    out
}

fn suffix(j: usize) -> String {
    let mut n = j + 1;
    let mut out: Vec<char> = Vec::new();
    while n > 0 {
        n -= 1;
        out.push((b'a' + (n % 26) as u8) as char);
        n /= 26;
    }
    out.iter().rev().collect()
}

fn label(e: &Entry) -> String {
    if e.authors.is_empty() {
        let t = collapse_ws(e.title.as_deref().unwrap_or(&e.key));
        return t.split(' ').take(3).collect::<Vec<_>>().join(" ");
    }
    let f: Vec<&str> = e.authors.iter().map(|a| if a.family.is_empty() { a.given.as_str() } else { a.family.as_str() }).collect();
    if f.len() >= 3 || e.et_al {
        format!("{} et al.", f[0])
    } else if f.len() == 1 {
        f[0].to_string()
    } else {
        format!("{} and {}", f[0], f[1])
    }
}

fn authors_full(e: &Entry) -> String {
    let names: Vec<String> =
        e.authors.iter().map(|a| if a.given.is_empty() { a.family.clone() } else { format!("{}, {}", a.family, a.given) }).collect();
    if names.is_empty() {
        return String::new();
    }
    let s = if names.len() == 1 {
        names[0].clone()
    } else {
        format!("{} and {}", names[..names.len() - 1].join(", "), names[names.len() - 1])
    };
    if e.et_al {
        format!("{}, et al.", s)
    } else {
        s
    }
}

/// Replace `[@key]` / `[@key, locator]` by `(Label year, locator)` and add the bibliography of the
/// cited entries at a paragraph `[[bibliography]]` or, without one, under `## References`.
pub fn citation_resolve(text: &str, bibs: &[(Format, String)], first_line: u32) -> Res<String> {
    check(text)?;
    let mut entries: HashMap<String, Entry> = HashMap::new();
    for (f, t) in bibs {
        for e in bib_parse(t, *f)? {
            if entries.contains_key(&e.key) {
                return Err(Diagnostic::new(Code::DuplicateId, format!("citation key {:?} is defined twice", e.key)));
            }
            entries.insert(e.key.clone(), e);
        }
    }
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let leaves = parse(&c, &starts, &ends, 1)?;
    let items = cite_items(&c, &starts, &leaves);
    for it in &items {
        if !entries.contains_key(&it.key) {
            return Err(Diagnostic::new(Code::UnresolvedReference, format!("no bibliography entry for @{}", it.key))
                .at_position(first_line + it.line as u32, it.col as u32));
        }
    }
    let mut cited: Vec<String> = Vec::new();
    for it in &items {
        if !cited.contains(&it.key) {
            cited.push(it.key.clone());
        }
    }
    let mut groups: Vec<((String, String), Vec<String>)> = Vec::new();
    for k in &cited {
        let e = &entries[k];
        let g = (label(e), e.year.clone().unwrap_or_else(|| "n.d.".to_string()));
        match groups.iter_mut().find(|x| x.0 == g) {
            Some(x) => x.1.push(k.clone()),
            None => groups.push((g, vec![k.clone()])),
        }
    }
    let mut year_of_key: HashMap<String, String> = HashMap::new();
    for ((_, y), ks) in &groups {
        for (j, k) in ks.iter().enumerate() {
            year_of_key.insert(k.clone(), if ks.len() > 1 { format!("{}{}", y, suffix(j)) } else { y.clone() });
        }
    }
    let mut edits: Vec<Edit> = Vec::new();
    for it in &items {
        let lab = format!("{} {}", escape_inline(&label(&entries[&it.key]), false)?, year_of_key[&it.key]);
        let text = match &it.loc {
            Some(l) => format!("({}, {})", lab, escape_inline(l, false)?),
            None => format!("({})", lab),
        };
        edits.push(Edit { start: it.start, end: it.end, text });
    }
    let mut sorted = cited.clone();
    sorted.sort_by_key(|k| {
        let e = &entries[k];
        let lead = authors_full(e).to_lowercase();
        let lead = if lead.is_empty() { e.title.clone().unwrap_or_default().to_lowercase() } else { lead };
        (lead, year_of_key[k].clone(), k.clone())
    });
    let mut lines: Vec<String> = Vec::new();
    for k in &sorted {
        let e = &entries[k];
        let au = authors_full(e);
        let mut s = String::new();
        if !au.is_empty() {
            s.push_str(&escape_inline(&au, false)?);
            s.push(' ');
        }
        s.push_str(&format!("({}).", year_of_key[k]));
        if let Some(t) = &e.title {
            s.push_str(&format!(" {}.", escape_inline(t, false)?));
        }
        if let Some(t) = &e.container {
            s.push_str(&format!(" {}.", escape_inline(t, false)?));
        }
        lines.push(format!("- {}", s));
    }
    let eol: &str = match c.iter().position(|&x| x == '\r' || x == '\n') {
        Some(p) if c[p] == '\r' && c.get(p + 1) == Some(&'\n') => "\r\n",
        Some(p) if c[p] == '\r' => "\r",
        _ => "\n",
    };
    let mut placed = false;
    for leaf in &leaves {
        if leaf.kind == Kind::Paragraph && leaf.segments.len() == 1 {
            let (x, y) = leaf.segments[0];
            if x == starts[leaf.first_line] && c[x..y].iter().collect::<String>().trim_matches(|z| z == ' ' || z == '\t') == "[[bibliography]]" {
                edits.push(Edit { start: x, end: y, text: lines.join(eol) });
                placed = true;
            }
        }
    }
    edits.sort_by(|a, b| a.start.cmp(&b.start).then(a.end.cmp(&b.end)));
    let mut out = apply_edits(&c, 0, c.len(), &edits);
    if !placed && !cited.is_empty() {
        if !out.is_empty() && !out.ends_with(&['\r', '\n'][..]) {
            out.push_str(eol);
        }
        if !out.is_empty() {
            out.push_str(eol);
        }
        out.push_str(&format!("## References{eol}{eol}{}{eol}", lines.join(eol)));
    }
    Ok(out)
}
