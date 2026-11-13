//! Source declarations and URL templates (sources-and-permissions.md section 2; G42 in
//! docs/TEST_PLAN.md). Offline only: no I/O, no host or IP checks (E6.3, E6.4).

use crate::decimal;
use crate::diag::{Code, Diagnostic};
use crate::json::{self, Json};
use crate::yaml::{self, Yaml};
use std::collections::HashMap;

type Res<T> = Result<T, Diagnostic>;

const MAX_URL: usize = 8192;
const CRED_KEYS: [&str; 16] = [
    "headers", "header", "auth", "authorization", "token", "password", "passwd", "api_key", "apikey", "secret",
    "username", "user", "credentials", "cookie", "cookies", "bearer",
];
const HTTP_KEYS: [&str; 6] = ["kind", "url", "method", "format", "select", "arguments"];
const VAR_KEYS: [&str; 4] = ["type", "unit", "source", "arguments"];

fn bad(msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::SourceInvalid, msg.into())
}

fn is_ident(s: &str) -> bool {
    let mut it = s.chars();
    match it.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' => it.all(|c| c.is_ascii_alphanumeric() || c == '_'),
        _ => false,
    }
}

/// `None` if `s` is a syntactically valid RFC 6901 JSON Pointer, else a message.
pub(crate) fn pointer_syntax(s: &str) -> Option<String> {
    if s.is_empty() {
        return None;
    }
    if !s.starts_with('/') {
        return Some("a JSON Pointer is empty or starts with '/'".to_string());
    }
    let c: Vec<char> = s.chars().collect();
    for (k, &ch) in c.iter().enumerate() {
        if ch == '~' && !matches!(c.get(k + 1), Some('0') | Some('1')) {
            return Some("'~' must be followed by 0 or 1".to_string());
        }
    }
    None
}

enum Part {
    Lit(String),
    Ph(String),
}

/// Validate the form of an absolute HTTPS URL template; returns its parts and placeholder names.
fn url_template_parts(t: &str, what: &str) -> Res<(Vec<Part>, Vec<String>)> {
    let c: Vec<char> = t.chars().collect();
    if c.len() > MAX_URL {
        return Err(Diagnostic::new(Code::ResourceLimit, format!("{what} is longer than {MAX_URL} characters")));
    }
    for &ch in &c {
        let o = ch as u32;
        if o <= 0x20 || o >= 0x7F {
            return Err(bad(format!("{what} contains whitespace, a control or a non-ASCII character (percent-encode it)")));
        }
        if "\\\"<>^`|".contains(ch) {
            return Err(bad(format!("{what} contains the character {ch:?}")));
        }
    }
    if c.len() < 8 || !c[..8].iter().collect::<String>().eq_ignore_ascii_case("https://") {
        return Err(bad(format!("{what} must be an absolute https:// URL")));
    }
    let rest = &c[8..];
    let cut = rest.iter().position(|&x| matches!(x, '/' | '?' | '#')).unwrap_or(rest.len());
    let auth: String = rest[..cut].iter().collect();
    let tail: String = rest[cut..].iter().collect();
    if auth.contains('{') || auth.contains('}') {
        return Err(bad(format!("{what}: placeholders are not allowed in the scheme or host")));
    }
    if auth.contains('@') {
        return Err(bad(format!("{what} must not contain credentials")));
    }
    let (host, port): (String, String) = if auth.starts_with('[') {
        let Some(end) = auth.find(']') else {
            return Err(bad(format!("{what} has an unterminated IPv6 host")));
        };
        let after = &auth[end + 1..];
        if after.is_empty() {
            (auth[..=end].to_string(), String::new())
        } else if let Some(p) = after.strip_prefix(':') {
            (auth[..=end].to_string(), p.to_string())
        } else {
            return Err(bad(format!("{what} has a malformed host")));
        }
    } else if let Some(i) = auth.rfind(':') {
        (auth[..i].to_string(), auth[i + 1..].to_string())
    } else {
        (auth.clone(), String::new())
    };
    if host.is_empty() || host == "[]" {
        return Err(bad(format!("{what} has no host")));
    }
    if !port.is_empty() {
        let ok = port.chars().all(|x| x.is_ascii_digit()) && port.parse::<u32>().map(|n| n <= 65535).unwrap_or(false);
        if !ok {
            return Err(bad(format!("{what} has an invalid port")));
        }
    }
    if tail.contains('#') {
        return Err(bad(format!("{what} must not contain a fragment")));
    }
    let mut parts = Vec::new();
    let mut names = Vec::new();
    let mut buf = String::new();
    let mut i = 0;
    while i < c.len() {
        let ch = c[i];
        if ch == '{' {
            if i > 0 && c[i - 1] == '$' {
                return Err(bad(format!("{what}: environment-variable syntax ${{...}} is not allowed")));
            }
            let Some(j) = c[i..].iter().position(|&x| x == '}').map(|p| p + i) else {
                return Err(bad(format!("{what} has an unclosed '{{'")));
            };
            let name: String = c[i + 1..j].iter().collect();
            if !is_ident(&name) {
                return Err(bad(format!("{what} has an invalid placeholder {{{name}}}")));
            }
            if !buf.is_empty() {
                parts.push(Part::Lit(std::mem::take(&mut buf)));
            }
            names.push(name.clone());
            parts.push(Part::Ph(name));
            i = j + 1;
        } else if ch == '}' {
            return Err(bad(format!("{what} has a stray '}}'")));
        } else if ch == '%' {
            let ok = i + 2 < c.len() && c[i + 1].is_ascii_hexdigit() && c[i + 2].is_ascii_hexdigit();
            if !ok {
                return Err(bad(format!("{what} has an invalid percent escape")));
            }
            buf.extend(&c[i..i + 3]);
            i += 3;
        } else {
            buf.push(ch);
            i += 1;
        }
    }
    if !buf.is_empty() {
        parts.push(Part::Lit(buf));
    }
    Ok((parts, names))
}

fn url_component(v: &str) -> String {
    if v == "." || v == ".." {
        return v.replace('.', "%2E");
    }
    let mut out = String::new();
    for b in v.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'.' | b'_' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn expand_checked(template: &str, args: &[(String, String)], what: &str) -> Res<String> {
    let (parts, names) = url_template_parts(template, what)?;
    let mut amap: HashMap<&str, &str> = HashMap::new();
    for (k, v) in args {
        if !is_ident(k) {
            return Err(bad(format!("invalid argument name {k:?}")));
        }
        if v.is_empty() {
            return Err(bad(format!("argument '{k}' is empty")));
        }
        amap.insert(k.as_str(), v.as_str());
    }
    for n in &names {
        if !amap.contains_key(n.as_str()) {
            return Err(bad(format!("{what} placeholder {{{n}}} has no argument")));
        }
    }
    for (k, _) in args {
        if !names.contains(k) {
            return Err(bad(format!("argument '{k}' matches no placeholder in {what}")));
        }
    }
    let mut out = String::new();
    for p in &parts {
        match p {
            Part::Lit(s) => out.push_str(s),
            Part::Ph(n) => out.push_str(&url_component(amap[n.as_str()])),
        }
    }
    if out.len() > MAX_URL {
        return Err(Diagnostic::new(Code::ResourceLimit, format!("expanded {what} is longer than {MAX_URL} characters")));
    }
    Ok(out)
}

/// Fill the `{name}` placeholders of an absolute HTTPS URL template. Values are percent-encoded as URL
/// components (only `A-Za-z0-9-._~` stay; a value made only of dots has its dots encoded).
pub fn url_template_expand(template: &str, args: &[(String, String)]) -> Res<String> {
    expand_checked(template, args, "url")
}

#[derive(Debug, Clone, PartialEq)]
pub struct Source {
    pub kind: String,
    pub reader: Option<String>,
    pub url: Option<String>,
    pub method: Option<String>,
    pub format: Option<String>,
    pub select: Option<String>,
    pub arguments: Vec<(String, String)>,
    pub named: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Declared {
    pub name: String,
    pub vtype: String,
    pub unit: Option<String>,
    pub source: Option<Source>,
    /// The URL with its arguments substituted (http sources only).
    pub url: Option<String>,
}

fn get<'a>(m: &'a [(String, Yaml)], k: &str) -> Option<&'a Yaml> {
    m.iter().find(|(n, _)| n == k).map(|x| &x.1)
}

fn arg_text(name: &str, v: &Yaml) -> Res<String> {
    match v {
        Yaml::Str(s) if !s.is_empty() => Ok(s.clone()),
        Yaml::Str(_) => Err(bad(format!("argument '{name}' is empty"))),
        Yaml::Number(n) => Ok(n.clone()),
        Yaml::Bool(b) => Ok(if *b { "true" } else { "false" }.to_string()),
        _ => Err(bad(format!("argument '{name}' must be a string, number or boolean"))),
    }
}

fn src_args(v: Option<&Yaml>, path: &str) -> Res<Vec<(String, String)>> {
    match v {
        None | Some(Yaml::Null) => Ok(Vec::new()),
        Some(Yaml::Map(m)) => {
            let mut out = Vec::new();
            for (k, x) in m {
                if !is_ident(k) {
                    return Err(bad(format!("{path}: invalid argument name {k:?}")));
                }
                out.push((k.clone(), arg_text(k, x)?));
            }
            Ok(out)
        }
        Some(_) => Err(bad(format!("{path} must be a mapping"))),
    }
}

fn check_keys(m: &[(String, Yaml)], allowed: &[&str], path: &str) -> Res<()> {
    for (k, _) in m {
        if CRED_KEYS.contains(&k.to_lowercase().as_str()) {
            return Err(bad(format!("{path}.{k}: credentials are not allowed in the core format")));
        }
        if !allowed.contains(&k.as_str()) {
            return Err(bad(format!("{path}.{k}: unknown key")));
        }
    }
    Ok(())
}

fn src_decl(d: &Yaml, path: &str) -> Res<Source> {
    let Yaml::Map(m) = d else {
        return Err(bad(format!("{path} must be a mapping")));
    };
    let Some(Yaml::Str(kind)) = get(m, "kind") else {
        return Err(bad(format!("{path}.kind is required and must be a string")));
    };
    if let Some(ident) = kind.strip_prefix("reader:") {
        let ok = ident.chars().next().is_some_and(|c| c.is_ascii_alphabetic())
            && ident.chars().count() <= 64
            && ident.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-'));
        if !ok {
            return Err(bad(format!("{path}.kind: invalid reader identifier")));
        }
        check_keys(m, &["kind", "arguments"], path)?;
        return Ok(Source {
            kind: kind.clone(),
            reader: Some(ident.to_string()),
            url: None,
            method: None,
            format: None,
            select: None,
            arguments: src_args(get(m, "arguments"), &format!("{path}.arguments"))?,
            named: None,
        });
    }
    if kind != "http" {
        return Err(bad(format!("{path}.kind: unknown kind {kind:?}")));
    }
    check_keys(m, &HTTP_KEYS, path)?;
    let Some(Yaml::Str(url)) = get(m, "url") else {
        return Err(bad(format!("{path}.url is required and must be a string")));
    };
    url_template_parts(url, &format!("{path}.url"))?;
    match get(m, "method") {
        None => {}
        Some(Yaml::Str(s)) if s == "GET" => {}
        Some(_) => return Err(bad(format!("{path}.method: only GET is supported"))),
    }
    let fmt = match get(m, "format") {
        Some(Yaml::Str(s)) if matches!(s.as_str(), "json" | "text" | "csv") => s.clone(),
        _ => return Err(bad(format!("{path}.format is required: json, text or csv"))),
    };
    let select = match get(m, "select") {
        None | Some(Yaml::Null) => None,
        Some(Yaml::Str(s)) => {
            if fmt == "text" {
                return Err(bad(format!("{path}.select is not allowed for format text")));
            }
            if fmt == "csv" {
                return Err(bad(format!("{path}.select: CSV selection is reserved")));
            }
            if let Some(msg) = pointer_syntax(s) {
                return Err(bad(format!("{path}.select: {msg}")));
            }
            Some(s.clone())
        }
        Some(_) => return Err(bad(format!("{path}.select must be a string"))),
    };
    Ok(Source {
        kind: "http".to_string(),
        reader: None,
        url: Some(url.clone()),
        method: Some("GET".to_string()),
        format: Some(fmt),
        select,
        arguments: src_args(get(m, "arguments"), &format!("{path}.arguments"))?,
        named: None,
    })
}

fn walk(
    vs: &[(String, Yaml)],
    prefix: &str,
    named: &[(String, Source)],
    out: &mut Vec<Declared>,
) -> Res<()> {
    for (name, v) in vs {
        let Yaml::Map(m) = v else { continue };
        let path = format!("variables.{prefix}{name}");
        if get(m, "source").is_none() && get(m, "type").is_none() {
            walk(m, &format!("{prefix}{name}."), named, out)?;
            continue;
        }
        check_keys(m, &VAR_KEYS, &path)?;
        let ty = match get(m, "type") {
            Some(Yaml::Str(s)) if matches!(s.as_str(), "number" | "string" | "boolean" | "date") => s.clone(),
            _ => return Err(bad(format!("{path}.type is required: number, string, boolean or date"))),
        };
        let unit = match get(m, "unit") {
            None | Some(Yaml::Null) => None,
            Some(u) => {
                if ty != "number" {
                    return Err(bad(format!("{path}.unit is only allowed for type number")));
                }
                match u {
                    Yaml::Str(s) if !s.trim().is_empty() => Some(s.clone()),
                    _ => return Err(bad(format!("{path}.unit must be a non-empty string"))),
                }
            }
        };
        let mut spec = None;
        let mut url = None;
        if let Some(s) = get(m, "source") {
            let mut sp = match s {
                Yaml::Str(r) => {
                    let Some((_, d)) = named.iter().find(|(n, _)| n == r) else {
                        return Err(bad(format!("{path}.source: unknown source {r:?}")));
                    };
                    let mut d = d.clone();
                    d.named = Some(r.clone());
                    d
                }
                Yaml::Map(_) => src_decl(s, &format!("{path}.source"))?,
                _ => return Err(bad(format!("{path}.source must be a name or a mapping"))),
            };
            for (k, x) in src_args(get(m, "arguments"), &format!("{path}.arguments"))? {
                match sp.arguments.iter_mut().find(|(n, _)| *n == k) {
                    Some(e) => e.1 = x,
                    None => sp.arguments.push((k, x)),
                }
            }
            if sp.kind == "http" {
                url = Some(expand_checked(sp.url.as_deref().unwrap_or(""), &sp.arguments, &format!("{path}.source.url"))?);
            }
            spec = Some(sp);
        } else if get(m, "arguments").is_some() {
            return Err(bad(format!("{path}.arguments needs a source")));
        }
        out.push(Declared { name: format!("{prefix}{name}"), vtype: ty, unit, source: spec, url });
    }
    Ok(())
}

/// Validate the `sources` and `variables` of effective metadata (YAML text) and list the declared
/// variables in document order. Plain values and namespaces without `source`/`type` are ignored.
pub fn source_validate(text: &str, first_line: u32) -> Res<Vec<Declared>> {
    let Yaml::Map(root) = yaml::load(text, true, first_line)? else {
        return Ok(Vec::new());
    };
    let mut named: Vec<(String, Source)> = Vec::new();
    match get(&root, "sources") {
        None | Some(Yaml::Null) => {}
        Some(Yaml::Map(m)) => {
            for (name, d) in m {
                named.push((name.clone(), src_decl(d, &format!("sources.{name}"))?));
            }
        }
        Some(_) => return Err(bad("sources must be a mapping")),
    }
    let mut out = Vec::new();
    match get(&root, "variables") {
        None | Some(Yaml::Null) => {}
        Some(Yaml::Map(m)) => walk(m, "", &named, &mut out)?,
        Some(_) => return Err(bad("variables must be a mapping")),
    }
    Ok(out)
}

// ---- E6.2: JSON Pointer selection and type checks (G43) ----

fn unresolved(msg: String) -> Diagnostic {
    Diagnostic::new(Code::SourceUnresolved, msg)
}

fn type_err(msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::SourceTypeError, msg.into())
}

fn kind_of(v: &Json) -> &'static str {
    match v {
        Json::Null => "null",
        Json::Bool(_) => "a boolean",
        Json::Num(_) => "a number",
        Json::Str(_) => "a string",
        Json::Arr(_) => "an array",
        Json::Obj(_) => "an object",
    }
}

/// Parse strict JSON and follow an RFC 6901 pointer. Errors never echo document values.
pub fn json_pointer_select(text: &str, pointer: &str) -> Res<Json> {
    if let Some(m) = pointer_syntax(pointer) {
        return Err(bad(format!("JSON Pointer: {m}")));
    }
    let root = json::parse(text, "source JSON")?;
    let mut cur = &root;
    let mut done = String::new();
    if !pointer.is_empty() {
        for raw in pointer[1..].split('/') {
            let tok = raw.replace("~1", "/").replace("~0", "~");
            let here = format!("{done}/{}", tok.replace('~', "~0").replace('/', "~1"));
            let fail = |why: &str| unresolved(format!("JSON Pointer {pointer}: {why} at {here}"));
            cur = match cur {
                Json::Obj(m) => match m.iter().find(|(k, _)| *k == tok) {
                    Some((_, v)) => v,
                    None => return Err(fail("no member")),
                },
                Json::Arr(a) => {
                    if tok == "-" {
                        return Err(fail("'-' refers to the element after the last"));
                    }
                    let digits = !tok.is_empty() && tok.bytes().all(|b| b.is_ascii_digit());
                    if !digits || (tok.len() > 1 && tok.starts_with('0')) {
                        return Err(fail("array index must be digits without leading zeros"));
                    }
                    match tok.parse::<usize>().ok().and_then(|i| a.get(i)) {
                        Some(v) => v,
                        None => return Err(fail("array index out of range")),
                    }
                }
                other => return Err(fail(&format!("cannot select inside {}", kind_of(other)))),
            };
            done = here;
        }
    }
    Ok(cur.clone())
}

/// Canonical decimal text of a JSON-grammar number (exponents expanded, exact, no rounding).
fn num_canonical(s: &str) -> Res<String> {
    let out = || type_err("number out of range");
    let b = s.as_bytes();
    let mut i = 0;
    if b.first() == Some(&b'-') {
        i += 1;
    }
    match b.get(i) {
        Some(b'0') => i += 1,
        Some(c) if c.is_ascii_digit() => {
            while b.get(i).is_some_and(|c| c.is_ascii_digit()) {
                i += 1;
            }
        }
        _ => return Err(type_err("not a finite decimal number")),
    }
    if b.get(i) == Some(&b'.') {
        i += 1;
        let st = i;
        while b.get(i).is_some_and(|c| c.is_ascii_digit()) {
            i += 1;
        }
        if i == st {
            return Err(type_err("not a finite decimal number"));
        }
    }
    let mant_end = i;
    if matches!(b.get(i), Some(b'e') | Some(b'E')) {
        i += 1;
        if matches!(b.get(i), Some(b'+') | Some(b'-')) {
            i += 1;
        }
        let st = i;
        while b.get(i).is_some_and(|c| c.is_ascii_digit()) {
            i += 1;
        }
        if i == st {
            return Err(type_err("not a finite decimal number"));
        }
    }
    if i != b.len() {
        return Err(type_err("not a finite decimal number"));
    }
    let exp: i64 = if mant_end < s.len() { s[mant_end + 1..].parse::<i64>().map_err(|_| out())? } else { 0 };
    let mant = &s[..mant_end];
    let neg = mant.starts_with('-');
    let mant = mant.trim_start_matches('-');
    let (ip, fp) = mant.split_once('.').unwrap_or((mant, ""));
    let digits = format!("{ip}{fp}");
    if digits.bytes().all(|c| c == b'0') {
        return Ok("0".to_string());
    }
    let point = (ip.len() as i64).saturating_add(exp);
    if point.abs() > 20000 || digits.len() > 10000 {
        return Err(out());
    }
    let plain = if point <= 0 {
        format!("0.{}{}", "0".repeat((-point) as usize), digits)
    } else if point as usize >= digits.len() {
        format!("{}{}", digits, "0".repeat(point as usize - digits.len()))
    } else {
        format!("{}.{}", &digits[..point as usize], &digits[point as usize..])
    };
    let signed = if neg { format!("-{plain}") } else { plain };
    decimal::canonical(&signed).map_err(|_| out())
}

fn date_ok(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() != 10 || b[4] != b'-' || b[7] != b'-' {
        return false;
    }
    if !b.iter().enumerate().all(|(k, c)| k == 4 || k == 7 || c.is_ascii_digit()) {
        return false;
    }
    let y: u32 = s[0..4].parse().unwrap_or(0);
    let m: u32 = s[5..7].parse().unwrap_or(0);
    let d: u32 = s[8..10].parse().unwrap_or(0);
    if y < 1 || !(1..=12).contains(&m) {
        return false;
    }
    let leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    let days = [31, if leap { 29 } else { 28 }, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][(m - 1) as usize];
    (1..=days).contains(&d)
}

#[derive(Debug, Clone, PartialEq)]
pub enum Typed {
    /// Canonical decimal text.
    Num(String),
    Str(String),
    Bool(bool),
    /// ISO `YYYY-MM-DD`.
    Date(String),
}

#[derive(Debug, Clone, PartialEq)]
pub struct Checked {
    pub vtype: String,
    pub value: Typed,
    pub unit: Option<String>,
}

/// Check a selected JSON value (strictly, no coercion) or, with `from_text`, a text body (a `Json::Str`)
/// against a declared type.
pub fn value_type_check(v: &Json, ty: &str, unit: Option<&str>, from_text: bool) -> Res<Checked> {
    if let Some(u) = unit {
        if ty != "number" {
            return Err(bad("unit is only allowed for type number"));
        }
        if u.trim().is_empty() {
            return Err(bad("unit must be a non-empty string"));
        }
    }
    let value = if from_text {
        let Json::Str(t) = v else {
            return Err(bad("a text body must be a string"));
        };
        if ty == "string" {
            let o = t.strip_suffix("\r\n").or_else(|| t.strip_suffix('\n')).unwrap_or(t);
            Typed::Str(o.to_string())
        } else {
            let t = t.trim_matches(|c| matches!(c, ' ' | '\t' | '\r' | '\n'));
            match ty {
                "number" => Typed::Num(num_canonical(t)?),
                "boolean" => match t {
                    "true" => Typed::Bool(true),
                    "false" => Typed::Bool(false),
                    _ => return Err(type_err("expected a boolean (true or false)")),
                },
                "date" => {
                    if !date_ok(t) {
                        return Err(type_err("expected a date (YYYY-MM-DD)"));
                    }
                    Typed::Date(t.to_string())
                }
                _ => return Err(bad("unknown type")),
            }
        }
    } else {
        let want = match ty {
            "number" => "a number",
            "boolean" => "a boolean",
            "string" | "date" => "a string",
            _ => return Err(bad("unknown type")),
        };
        if kind_of(v) != want {
            return Err(type_err(format!("expected {ty}, got {}", kind_of(v))));
        }
        match (ty, v) {
            ("number", Json::Num(n)) => Typed::Num(num_canonical(n)?),
            ("boolean", Json::Bool(b)) => Typed::Bool(*b),
            ("date", Json::Str(t)) => {
                if !date_ok(t) {
                    return Err(type_err("expected a date (YYYY-MM-DD)"));
                }
                Typed::Date(t.clone())
            }
            (_, Json::Str(t)) => Typed::Str(t.clone()),
            _ => return Err(bad("unknown type")),
        }
    };
    Ok(Checked { vtype: ty.to_string(), value, unit: unit.map(|u| u.to_string()) })
}
