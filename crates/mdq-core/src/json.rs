//! Strict RFC 8259 JSON parser (used for CSL JSON; G39 in docs/TEST_PLAN.md).
//!
//! Numbers keep their source text. Duplicate object keys, unpaired surrogates, control characters
//! in strings and nesting deeper than 64 are errors.

use crate::diag::{Code, Diagnostic};
use std::collections::HashSet;

type Res<T> = Result<T, Diagnostic>;

const MAX_INPUT: usize = 16 * 1024 * 1024;
const MAX_DEPTH: usize = 64;

#[derive(Debug, Clone, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Num(String),
    Str(String),
    Arr(Vec<Json>),
    Obj(Vec<(String, Json)>),
}

impl Json {
    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Obj(m) => m.iter().find(|(k, _)| k == key).map(|x| &x.1),
            _ => None,
        }
    }
}

struct P<'a> {
    c: Vec<char>,
    pos: usize,
    what: &'a str,
}

impl<'a> P<'a> {
    fn bad_code(&self, code: Code, msg: &str, i: usize) -> Diagnostic {
        let line = 1 + self.c[..i].iter().filter(|&&x| x == '\n').count();
        let ls = self.c[..i].iter().rposition(|&x| x == '\n').map(|k| k + 1).unwrap_or(0);
        Diagnostic::new(code, format!("{}: {}", self.what, msg)).at_position(line as u32, (i - ls + 1) as u32)
    }

    fn bad(&self, msg: &str, i: usize) -> Diagnostic {
        self.bad_code(Code::SourceInvalid, msg, i)
    }

    fn ws(&mut self) {
        while self.pos < self.c.len() && matches!(self.c[self.pos], ' ' | '\t' | '\n' | '\r') {
            self.pos += 1;
        }
    }

    fn hex4(&self, k: usize) -> Res<u32> {
        if k + 4 > self.c.len() || !self.c[k..k + 4].iter().all(|x| x.is_ascii_hexdigit()) {
            return Err(self.bad("bad \\u escape", k - 2));
        }
        let h: String = self.c[k..k + 4].iter().collect();
        Ok(u32::from_str_radix(&h, 16).unwrap_or(0))
    }

    fn string(&mut self) -> Res<String> {
        let n = self.c.len();
        let mut i = self.pos + 1;
        let mut out = String::new();
        loop {
            if i >= n {
                return Err(self.bad("unterminated string", self.pos));
            }
            let ch = self.c[i];
            if ch == '"' {
                self.pos = i + 1;
                return Ok(out);
            }
            if (ch as u32) < 0x20 {
                return Err(self.bad("control character in string", i));
            }
            if ch == '\\' {
                i += 1;
                if i >= n {
                    return Err(self.bad("unterminated string", self.pos));
                }
                match self.c[i] {
                    '"' => out.push('"'),
                    '\\' => out.push('\\'),
                    '/' => out.push('/'),
                    'b' => out.push('\u{8}'),
                    'f' => out.push('\u{c}'),
                    'n' => out.push('\n'),
                    'r' => out.push('\r'),
                    't' => out.push('\t'),
                    'u' => {
                        let mut v = self.hex4(i + 1)?;
                        i += 4;
                        if (0xD800..0xDC00).contains(&v) {
                            if !(i + 2 < n && self.c[i + 1] == '\\' && self.c[i + 2] == 'u') {
                                return Err(self.bad("unpaired surrogate", i - 5));
                            }
                            let w = self.hex4(i + 3)?;
                            if !(0xDC00..0xE000).contains(&w) {
                                return Err(self.bad("unpaired surrogate", i - 5));
                            }
                            v = 0x10000 + ((v - 0xD800) << 10) + (w - 0xDC00);
                            i += 6;
                        } else if (0xDC00..0xE000).contains(&v) {
                            return Err(self.bad("unpaired surrogate", i - 5));
                        }
                        out.push(char::from_u32(v).unwrap_or('\u{fffd}'));
                    }
                    _ => return Err(self.bad("bad escape", i - 1)),
                }
            } else {
                out.push(ch);
            }
            i += 1;
        }
    }

    fn number(&mut self) -> Res<Json> {
        let n = self.c.len();
        let i = self.pos;
        let mut j = i;
        if j < n && self.c[j] == '-' {
            j += 1;
        }
        if j < n && self.c[j] == '0' {
            j += 1;
        } else if j < n && ('1'..='9').contains(&self.c[j]) {
            while j < n && self.c[j].is_ascii_digit() {
                j += 1;
            }
        } else {
            return Err(self.bad("bad number", i));
        }
        if j < n && self.c[j] == '.' {
            let mut k = j + 1;
            while k < n && self.c[k].is_ascii_digit() {
                k += 1;
            }
            if k == j + 1 {
                return Err(self.bad("bad number", i));
            }
            j = k;
        }
        if j < n && (self.c[j] == 'e' || self.c[j] == 'E') {
            let mut k = j + 1;
            if k < n && (self.c[k] == '+' || self.c[k] == '-') {
                k += 1;
            }
            let mut m = k;
            while m < n && self.c[m].is_ascii_digit() {
                m += 1;
            }
            if m == k {
                return Err(self.bad("bad number", i));
            }
            j = m;
        }
        self.pos = j;
        Ok(Json::Num(self.c[i..j].iter().collect()))
    }

    fn value(&mut self, depth: usize) -> Res<Json> {
        self.ws();
        let n = self.c.len();
        if self.pos >= n {
            return Err(self.bad("unexpected end of input", self.pos));
        }
        let ch = self.c[self.pos];
        if depth > MAX_DEPTH {
            return Err(self.bad_code(Code::ResourceLimit, "nested deeper than 64 levels", self.pos));
        }
        match ch {
            '{' => {
                self.pos += 1;
                let mut out: Vec<(String, Json)> = Vec::new();
                let mut seen: HashSet<String> = HashSet::new();
                self.ws();
                if self.pos < n && self.c[self.pos] == '}' {
                    self.pos += 1;
                    return Ok(Json::Obj(out));
                }
                loop {
                    self.ws();
                    if self.pos >= n || self.c[self.pos] != '"' {
                        return Err(self.bad("expected a string key", self.pos));
                    }
                    let at = self.pos;
                    let k = self.string()?;
                    self.ws();
                    if self.pos >= n || self.c[self.pos] != ':' {
                        return Err(self.bad("expected ':'", self.pos));
                    }
                    self.pos += 1;
                    if !seen.insert(k.clone()) {
                        return Err(self.bad(&format!("duplicate key {:?}", k), at));
                    }
                    let v = self.value(depth + 1)?;
                    out.push((k, v));
                    self.ws();
                    if self.pos < n && self.c[self.pos] == ',' {
                        self.pos += 1;
                        continue;
                    }
                    if self.pos < n && self.c[self.pos] == '}' {
                        self.pos += 1;
                        return Ok(Json::Obj(out));
                    }
                    return Err(self.bad("expected ',' or '}'", self.pos));
                }
            }
            '[' => {
                self.pos += 1;
                let mut out = Vec::new();
                self.ws();
                if self.pos < n && self.c[self.pos] == ']' {
                    self.pos += 1;
                    return Ok(Json::Arr(out));
                }
                loop {
                    out.push(self.value(depth + 1)?);
                    self.ws();
                    if self.pos < n && self.c[self.pos] == ',' {
                        self.pos += 1;
                        continue;
                    }
                    if self.pos < n && self.c[self.pos] == ']' {
                        self.pos += 1;
                        return Ok(Json::Arr(out));
                    }
                    return Err(self.bad("expected ',' or ']'", self.pos));
                }
            }
            '"' => Ok(Json::Str(self.string()?)),
            _ => {
                for (word, v) in [("true", Json::Bool(true)), ("false", Json::Bool(false)), ("null", Json::Null)] {
                    let w: Vec<char> = word.chars().collect();
                    if self.pos + w.len() <= n && self.c[self.pos..self.pos + w.len()] == w[..] {
                        self.pos += w.len();
                        return Ok(v);
                    }
                }
                if ch == '-' || ch.is_ascii_digit() {
                    return self.number();
                }
                Err(self.bad("unexpected character", self.pos))
            }
        }
    }
}

/// Parse one JSON value; `what` names the document in error messages.
pub fn parse(text: &str, what: &str) -> Res<Json> {
    if text.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let t = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut p = P { c: t.chars().collect(), pos: 0, what };
    let v = p.value(0)?;
    p.ws();
    if p.pos < p.c.len() {
        return Err(p.bad("unexpected text after the JSON value", p.pos));
    }
    Ok(v)
}
