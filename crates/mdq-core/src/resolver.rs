//! Source resolver (sources-and-permissions.md sections 2-4; G46 in docs/TEST_PLAN.md).
//!
//! The core never opens a socket, reads the clock or the environment. It drives a `Transport`
//! supplied by the host (a Python callable in tests, a real HTTP client later) and checks every
//! answer: policy, DNS re-check, pinned address, size and time limits, redirects, UTF-8, types.
//!
//! Diagnostic messages never contain URLs, hosts, header values, response bodies or transport
//! error text: they are fixed sentences (plus declaration names and HTTP status numbers).

use crate::diag::{Code, Diagnostic};
use crate::json::Json;
use crate::net::{self, parse_ip, Ip};
use crate::policy::{self, Map};
use crate::source::{self, Checked, Declared, Source};

type Res<T> = Result<T, Diagnostic>;

const REDIRECTS: [i64; 5] = [301, 302, 303, 307, 308];

// ---------------------------------------------------------------- transport protocol

/// One HTTP GET the transport must perform. `address` is the IP the connection MUST use
/// (the host name is only for TLS/SNI and the Host header): this pins the validated address.
#[derive(Debug, Clone, PartialEq)]
pub struct GetRequest {
    pub url: String,
    pub address: String,
    pub headers: Vec<(String, String)>,
    pub timeout_ms: u64,
    pub max_bytes: u64,
}

/// The transport's answer, converted from the host language but NOT yet checked (the core checks it).
/// `status` and `elapsed_ms` are signed so that a negative value reaches the core and is rejected there.
#[derive(Debug, Clone, PartialEq)]
pub struct Response {
    pub status: i64,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
    /// The IP address the connection actually used.
    pub peer: String,
    pub elapsed_ms: i64,
}

/// Why a transport call produced no usable answer.
#[derive(Debug, Clone, PartialEq)]
pub enum TransportError {
    /// The transport gave up because of time (Python: `TimeoutError`).
    Timeout,
    /// Any other failure; carries only the error's class name (never its text).
    Failed(String),
    /// The answer had the wrong shape (not a dict, wrong types, missing keys).
    Malformed,
    /// The host must stop now (Python: `KeyboardInterrupt`, a panic, ...). The wrapper
    /// re-raises the original error; the core only stops.
    Abort,
}

/// What the core needs from the outside world. `&mut self` lets a transport record calls.
pub trait Transport {
    fn resolve(&mut self, host: &str) -> Result<Vec<String>, TransportError>;
    fn get(&mut self, req: &GetRequest) -> Result<Response, TransportError>;
}

// ---------------------------------------------------------------- results and errors

/// Where a value came from. Identity is `origin` + `key`, never the URL (which may carry secrets).
#[derive(Debug, Clone, PartialEq)]
pub struct SourceRef {
    pub kind: String,
    pub named: Option<String>,
    pub origin: Option<String>,
    /// SHA-256 (hex) of the canonical JSON of the declaration, see `decl_key`.
    pub key: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Fetched {
    pub name: String,
    pub checked: Checked,
    pub retrieved_at: String,
    pub source: SourceRef,
}

/// `source_fetch` can fail in two ways: a bad argument from the caller (the wrapper raises
/// `ValueError`) or a diagnostic about the document/source (the wrapper raises `MdqError`).
#[derive(Debug, Clone, PartialEq)]
pub enum FetchError {
    Arg(&'static str),
    Diag(Diagnostic),
}

// `impl From` lets the `?` operator turn a `Diagnostic` into a `FetchError` automatically.
impl From<Diagnostic> for FetchError {
    fn from(d: Diagnostic) -> Self {
        FetchError::Diag(d)
    }
}

fn failed(msg: &str) -> Diagnostic {
    Diagnostic::new(Code::SourceFetchFailed, msg)
}

fn invalid(msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::SourceInvalid, msg.into())
}

fn unresolved(msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::SourceUnresolved, msg.into())
}

// ---------------------------------------------------------------- argument checks

/// `YYYY-MM-DDTHH:MM:SSZ`, a real calendar date, hour < 24, minute < 60, second < 60.
pub fn timestamp_ok(s: &str) -> bool {
    // Check ASCII first so the byte slicing below can never cut a multi-byte character.
    if !s.is_ascii() || s.len() != 20 {
        return false;
    }
    let b = s.as_bytes();
    let shape = b[10] == b'T' && b[13] == b':' && b[16] == b':' && b[19] == b'Z';
    let digits = [11, 12, 14, 15, 17, 18].iter().all(|&i| b[i].is_ascii_digit());
    if !shape || !digits || !source::date_ok(&s[..10]) {
        return false;
    }
    let n = |i: usize| (b[i] - b'0') as u32 * 10 + (b[i + 1] - b'0') as u32;
    n(11) < 24 && n(14) < 60 && n(17) < 60
}

/// `None` if the caller's extra request headers are acceptable, else the reason.
/// Names: printable ASCII without space or ':'; values: printable ASCII (no control characters).
/// Two names that differ only in case are rejected (HTTP names are case-insensitive).
pub fn headers_check(headers: &[(String, String)]) -> Option<&'static str> {
    let mut seen: Vec<String> = Vec::new();
    for (k, v) in headers {
        if k.is_empty() || !k.bytes().all(|c| (0x21..=0x7E).contains(&c) && c != b':') {
            return Some("a header name must be non-empty printable ASCII without spaces or ':'");
        }
        if !v.bytes().all(|c| (0x20..=0x7E).contains(&c)) {
            return Some("a header value must be printable ASCII without control characters");
        }
        let low = k.to_ascii_lowercase();
        if seen.contains(&low) {
            return Some("a header name is given twice");
        }
        seen.push(low);
    }
    None
}

// ---------------------------------------------------------------- SHA-256 (FIPS 180-4)

const K: [u32; 64] = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
    0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
    0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
    0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
    0xc67178f2,
];

/// SHA-256 of `data` as 64 lower-case hex digits. Written out here so the core has no dependency.
/// All additions are `wrapping_add`: SHA-256 is defined modulo 2^32 (plain `+` would panic on overflow
/// in debug builds).
pub fn sha256_hex(data: &[u8]) -> String {
    let mut h: [u32; 8] =
        [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    // Padding: a 1 bit, zeros up to 56 mod 64 bytes, then the length in bits as a big-endian u64.
    let mut msg = data.to_vec();
    let bits = (data.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bits.to_be_bytes());
    for chunk in msg.chunks(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([chunk[4 * i], chunk[4 * i + 1], chunk[4 * i + 2], chunk[4 * i + 3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh] = h;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ (!e & g);
            let t1 = hh.wrapping_add(s1).wrapping_add(ch).wrapping_add(K[i]).wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        for (x, y) in h.iter_mut().zip([a, b, c, d, e, f, g, hh]) {
            *x = x.wrapping_add(y);
        }
    }
    h.iter().map(|x| format!("{x:08x}")).collect()
}

// ---------------------------------------------------------------- declaration key

/// A JSON string exactly as Python's `json.dumps(..., ensure_ascii=True)` writes it:
/// `\"` `\\` `\b` `\f` `\n` `\r` `\t`, every other character outside ' '..'~' as `\uxxxx`
/// (lower-case hex), characters above U+FFFF as a UTF-16 surrogate pair.
fn json_str(s: &str) -> String {
    let mut out = String::from("\"");
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            ' '..='~' => out.push(ch),
            _ => {
                // `encode_utf16` gives one unit for the BMP and two (a surrogate pair) above it.
                let mut buf = [0u16; 2];
                for unit in ch.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}

fn json_opt(s: &Option<String>) -> String {
    s.as_ref().map(|x| json_str(x)).unwrap_or_else(|| "null".to_string())
}

/// The identity of a declaration: SHA-256 of
/// `json.dumps({"source": {arguments, format, kind, method, reader, select, url}, "type": ty, "unit": unit},
///             sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
/// Keys are written in sorted order by hand; `url` is the TEMPLATE, not the expanded URL.
pub fn decl_key(spec: &Source, ty: &str, unit: Option<&str>) -> String {
    let mut args = spec.arguments.clone();
    // Rust compares `String`s byte-wise; for UTF-8 that is the same as Python's code-point order.
    args.sort();
    let args_json: Vec<String> = args.iter().map(|(k, v)| format!("{}:{}", json_str(k), json_str(v))).collect();
    let doc = format!(
        "{{\"source\":{{\"arguments\":{{{}}},\"format\":{},\"kind\":{},\"method\":{},\"reader\":{},\"select\":{},\"url\":{}}},\"type\":{},\"unit\":{}}}",
        args_json.join(","),
        json_opt(&spec.format),
        json_str(&spec.kind),
        json_opt(&spec.method),
        json_opt(&spec.reader),
        json_opt(&spec.select),
        json_opt(&spec.url),
        json_str(ty),
        json_opt(&unit.map(|u| u.to_string())),
    );
    sha256_hex(doc.as_bytes())
}

// ---------------------------------------------------------------- the fetch loop

fn transport_diag(e: TransportError, malformed: &'static str) -> Diagnostic {
    match e {
        TransportError::Timeout => failed("the request timed out"),
        TransportError::Failed(class) => {
            // Only a plain identifier is echoed; anything odd becomes "Exception".
            let ok = !class.is_empty() && class.len() <= 64 && class.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_');
            let class = if ok { class } else { "Exception".to_string() };
            failed(&format!("the transport failed ({class})"))
        }
        TransportError::Malformed => failed(malformed),
        TransportError::Abort => failed("the transport was interrupted"),
    }
}

fn ip_text(ip: Ip) -> String {
    match ip {
        Ip::V4(n) => net::v4_text(n),
        Ip::V6(h) => net::v6_text(&h),
    }
}

/// Resolve a host name and re-check EVERY answer against the policy (one bad address in the answer
/// means the name is hostile). Returns the first address, which the connection is then pinned to.
fn resolve_pinned(t: &mut dyn Transport, policy: &Map, host: &str) -> Res<Ip> {
    const DNS: &str = "DNS resolution failed";
    let addrs = t.resolve(host).map_err(|e| transport_diag(e, DNS))?;
    if addrs.is_empty() {
        return Err(failed(DNS));
    }
    let mut ips = Vec::new();
    for a in &addrs {
        // `ok_or_else` turns `None` into an error value; `?` returns it.
        ips.push(parse_ip(a).ok_or_else(|| failed(DNS))?);
    }
    for ip in &ips {
        if policy::policy_ip_allowed(policy, host, &ip_text(*ip))? != Some(true) {
            return Err(net::denied("the host resolved to a blocked address"));
        }
    }
    Ok(ips[0])
}

/// Header names lower-cased; two names equal up to case make the response malformed.
fn lower_headers(h: &[(String, String)]) -> Option<Vec<(String, String)>> {
    let mut out: Vec<(String, String)> = Vec::new();
    for (k, v) in h {
        let low = k.to_ascii_lowercase();
        if out.iter().any(|(n, _)| *n == low) {
            return None;
        }
        out.push((low, v.clone()));
    }
    Some(out)
}

fn header<'a>(h: &'a [(String, String)], name: &str) -> Option<&'a str> {
    h.iter().find(|(k, _)| k == name).map(|(_, v)| v.as_str())
}

/// Turn the body into a checked value: strict UTF-8, then JSON selection or text, then the type.
fn body_value(body: Vec<u8>, spec: &Source, d: &Declared) -> Res<Checked> {
    let Ok(doc) = String::from_utf8(body) else {
        return Err(invalid("the response is not valid UTF-8"));
    };
    if spec.format.as_deref() == Some("json") {
        let v = match source::json_pointer_select(&doc, spec.select.as_deref().unwrap_or("")) {
            Ok(v) => v,
            // The JSON parser may quote parts of the document (a duplicate key); keep only the position.
            Err(e) if e.code == Code::SourceInvalid => {
                let mut fixed = invalid("the response is not valid JSON");
                fixed.line = e.line;
                fixed.column = e.column;
                return Err(fixed);
            }
            Err(e) => return Err(e),
        };
        source::value_type_check(&v, &d.vtype, d.unit.as_deref(), false)
    } else {
        source::value_type_check(&Json::Str(doc), &d.vtype, d.unit.as_deref(), true)
    }
}

/// Fetch and check one declared variable. `retrieved_at` is supplied by the caller (the core never
/// reads the clock); `allow_network` is the caller's explicit consent for THIS call.
/// A failure is always an error: nothing stale or cached is ever substituted.
pub fn source_fetch(
    text: &str,
    name: &str,
    policy: &Map,
    t: &mut dyn Transport,
    retrieved_at: &str,
    allow_network: bool,
    headers: &[(String, String)],
) -> Result<Fetched, FetchError> {
    if !timestamp_ok(retrieved_at) {
        return Err(FetchError::Arg("retrieved_at must be YYYY-MM-DDTHH:MM:SSZ (a real UTC date and time)"));
    }
    if let Some(why) = headers_check(headers) {
        return Err(FetchError::Arg(why));
    }
    let decls = source::source_validate(text, 1)?;
    let Some(d) = decls.into_iter().find(|x| x.name == name) else {
        return Err(unresolved("no declared variable has that name").into());
    };
    let Some(spec) = d.source.clone() else {
        return Err(unresolved("the variable declares no source").into());
    };
    if spec.kind != "http" {
        return Err(Diagnostic::new(Code::UnsupportedFeature, "reader sources need a reader adapter").into());
    }
    if spec.format.as_deref() == Some("csv") {
        return Err(Diagnostic::new(Code::UnsupportedFeature, "CSV sources need a reader adapter").into());
    }
    if !allow_network {
        return Err(net::denied("network access was not authorised for this call").into());
    }

    let mut url = d.url.clone().unwrap_or_default();
    let mut visited = vec![url.clone()];
    let mut origin0: Option<String> = None;
    let mut total_ms: u64 = 0; // time spent so far, as reported by the transport
    let mut n: u64 = 0; // redirects followed so far

    let body = loop {
        let dec = match policy::policy_decide(policy, &url, n) {
            Ok(x) => x,
            // After a redirect, a malformed URL can only come from the server's Location header.
            Err(e) if n > 0 && e.code == Code::SourceInvalid => {
                return Err(failed("the redirect location is not a valid URL").into())
            }
            Err(e) => return Err(e.into()),
        };
        // `get_or_insert_with` stores the first origin once and returns it on every later turn.
        let first_origin = origin0.get_or_insert_with(|| dec.origin.clone()).clone();
        let (max_bytes, timeout_ms) = (dec.limits[0], dec.limits[1].saturating_mul(1000));
        if total_ms >= timeout_ms {
            return Err(failed("the request timed out").into());
        }
        let pinned = if dec.kind == "name" {
            resolve_pinned(t, policy, &dec.host)?
        } else {
            // An IP literal host needs no DNS; `policy_decide` already checked its range.
            parse_ip(&dec.host).ok_or_else(|| failed("malformed transport response"))?
        };
        // Caller headers (e.g. an API key) go only to the first origin, never across a redirect.
        let send = if dec.origin == first_origin { headers.to_vec() } else { Vec::new() };
        let req = GetRequest {
            url: url.clone(),
            address: ip_text(pinned),
            headers: send,
            timeout_ms: timeout_ms - total_ms,
            max_bytes,
        };
        const BAD: &str = "malformed transport response";
        let resp = t.get(&req).map_err(|e| transport_diag(e, BAD))?;

        // --- check the answer before believing any of it
        if !(100..=599).contains(&resp.status) {
            return Err(failed(BAD).into());
        }
        let Ok(elapsed) = u64::try_from(resp.elapsed_ms) else {
            return Err(failed(BAD).into()); // negative
        };
        let Some(rh) = lower_headers(&resp.headers) else {
            return Err(failed(BAD).into());
        };
        let Some(peer) = parse_ip(&resp.peer) else {
            return Err(failed(BAD).into());
        };
        if peer != pinned {
            // DNS rebinding or a misbehaving transport: the connection went somewhere unchecked.
            return Err(net::denied("the connection did not use the validated address").into());
        }
        total_ms = total_ms.saturating_add(elapsed);
        if total_ms > timeout_ms {
            return Err(failed("the request timed out").into()); // slow-loris: many slow steps
        }
        if let Some(cl) = header(&rh, "content-length") {
            if cl.is_empty() || !cl.bytes().all(|c| c.is_ascii_digit()) {
                return Err(failed(BAD).into());
            }
            // Too many digits for u64 is certainly too large.
            if cl.parse::<u64>().map_or(true, |x| x > max_bytes) {
                return Err(failed("the response is too large").into());
            }
        }
        if resp.body.len() as u64 > max_bytes {
            return Err(failed("the response is too large").into());
        }
        if REDIRECTS.contains(&resp.status) {
            let loc = header(&rh, "location").unwrap_or("");
            if loc.is_empty() {
                return Err(failed("redirect without a location").into());
            }
            let Some(next) = net::url_join(&url, loc) else {
                return Err(failed("the redirect location is not a valid URL").into());
            };
            if visited.contains(&next) {
                return Err(failed("redirect loop").into());
            }
            visited.push(next.clone());
            url = next;
            n += 1; // `policy_decide` enforces max_redirects on the next turn
            continue;
        }
        if !(200..=299).contains(&resp.status) {
            return Err(failed(&format!("HTTP status {}", resp.status)).into());
        }
        break resp.body; // `break value` makes the loop an expression
    };

    let checked = body_value(body, &spec, &d)?;
    let key = decl_key(&spec, &d.vtype, d.unit.as_deref());
    Ok(Fetched {
        name: d.name,
        checked,
        retrieved_at: retrieved_at.to_string(),
        source: SourceRef { kind: "http".to_string(), named: spec.named, origin: origin0, key },
    })
}

// ---------------------------------------------------------------- snapshots (offline replay)

/// A snapshot value converted from the host language without interpretation.
#[derive(Debug, Clone, PartialEq)]
pub enum SVal {
    Null,
    Bool(bool),
    /// An integer or a decimal, as its text.
    Num(String),
    /// A binary float: never accepted for values (inexact).
    Float,
    Str(String),
    List,
    Map(Vec<(String, SVal)>),
    Other,
}

struct Entry {
    name: String,
    vtype: String,
    value: SVal,
    unit: Option<String>,
    retrieved_at: String,
    source: SourceRef,
}

const ENTRY_KEYS: [&str; 6] = ["name", "type", "value", "unit", "retrieved_at", "source"];
const SOURCE_KEYS: [&str; 4] = ["kind", "named", "origin", "key"];

fn field<'a>(m: &'a [(String, SVal)], k: &str) -> Option<&'a SVal> {
    m.iter().find(|(n, _)| n == k).map(|x| &x.1)
}

/// Exactly the keys `allowed`, each once. Map keys that were not strings arrive as "" (never allowed).
fn exact_keys(m: &[(String, SVal)], allowed: &[&str], what: &str) -> Res<()> {
    for (k, _) in m {
        if !allowed.contains(&k.as_str()) {
            return Err(invalid(format!("{what}: unknown key")));
        }
    }
    for k in allowed {
        if field(m, k).is_none() {
            return Err(invalid(format!("{what}: missing key '{k}'")));
        }
    }
    Ok(())
}

fn opt_str(v: Option<&SVal>, what: &str) -> Res<Option<String>> {
    match v {
        Some(SVal::Null) => Ok(None),
        Some(SVal::Str(s)) => Ok(Some(s.clone())),
        _ => Err(invalid(format!("{what} must be a string or None"))),
    }
}

fn req_str(v: Option<&SVal>, what: &str) -> Res<String> {
    match v {
        Some(SVal::Str(s)) => Ok(s.clone()),
        _ => Err(invalid(format!("{what} must be a string"))),
    }
}

fn parse_entry(i: usize, e: &SVal) -> Res<Entry> {
    let what = format!("snapshot entry {i}");
    let SVal::Map(m) = e else {
        return Err(invalid(format!("{what} must be a mapping")));
    };
    exact_keys(m, &ENTRY_KEYS, &what)?;
    let name = req_str(field(m, "name"), &format!("{what}.name"))?;
    let vtype = req_str(field(m, "type"), &format!("{what}.type"))?;
    if !matches!(vtype.as_str(), "number" | "string" | "boolean" | "date") {
        return Err(invalid(format!("{what}.type must be number, string, boolean or date")));
    }
    let unit = opt_str(field(m, "unit"), &format!("{what}.unit"))?;
    if unit.as_ref().is_some_and(|u| u.trim().is_empty()) {
        return Err(invalid(format!("{what}.unit must be a non-empty string")));
    }
    let retrieved_at = req_str(field(m, "retrieved_at"), &format!("{what}.retrieved_at"))?;
    if !timestamp_ok(&retrieved_at) {
        return Err(invalid(format!("{what}.retrieved_at must be YYYY-MM-DDTHH:MM:SSZ")));
    }
    let Some(SVal::Map(s)) = field(m, "source") else {
        return Err(invalid(format!("{what}.source must be a mapping")));
    };
    let sw = format!("{what}.source");
    exact_keys(s, &SOURCE_KEYS, &sw)?;
    let key = req_str(field(s, "key"), &format!("{sw}.key"))?;
    if key.len() != 64 || !key.bytes().all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c)) {
        return Err(invalid(format!("{sw}.key must be 64 lower-case hex digits")));
    }
    let source = SourceRef {
        kind: req_str(field(s, "kind"), &format!("{sw}.kind"))?,
        named: opt_str(field(s, "named"), &format!("{sw}.named"))?,
        origin: opt_str(field(s, "origin"), &format!("{sw}.origin"))?,
        key,
    };
    let value = field(m, "value").cloned().unwrap_or(SVal::Null);
    Ok(Entry { name, vtype, value, unit, retrieved_at, source })
}

/// Replay recorded values instead of fetching: every declared variable WITH a source needs exactly
/// one matching entry (same kind, origin and declaration key; same type and unit). Never touches a
/// transport. Returns the values in declaration order.
pub fn snapshot_apply(text: &str, snapshot: &[SVal]) -> Res<Vec<Fetched>> {
    let decls = source::source_validate(text, 1)?;
    snapshot_resolve(&decls, snapshot)
}

/// `snapshot_apply` for declarations that are already validated (used by `compile`).
pub fn snapshot_resolve(decls: &[source::Declared], snapshot: &[SVal]) -> Res<Vec<Fetched>> {
    let mut entries: Vec<Entry> = Vec::new();
    for (i, e) in snapshot.iter().enumerate() {
        let en = parse_entry(i, e)?;
        if !decls.iter().any(|d| d.name == en.name && d.source.is_some()) {
            return Err(invalid(format!("snapshot entry {i} names no declared source variable")));
        }
        if entries.iter().any(|x| x.name == en.name) {
            return Err(invalid(format!("snapshot entry {i} repeats a variable")));
        }
        entries.push(en);
    }
    let mut out = Vec::new();
    for d in decls {
        let Some(spec) = &d.source else { continue }; // host-supplied: not the snapshot's business
        let Some(en) = entries.iter().find(|x| x.name == d.name) else {
            return Err(unresolved(format!("no snapshot entry for '{}'", d.name)));
        };
        // The origin a live fetch would report (only http sources have one).
        let origin = d.url.as_deref().and_then(|u| net::url_parts(u).ok()).map(|p| p.origin);
        let same = en.source.kind == spec.kind
            && en.source.key == decl_key(spec, &d.vtype, d.unit.as_deref())
            && (spec.kind != "http" || en.source.origin == origin)
            && en.vtype == d.vtype
            && en.unit == d.unit;
        if !same {
            return Err(unresolved(format!("the snapshot entry for '{}' does not match its declaration", d.name)));
        }
        let j = match &en.value {
            SVal::Null => Json::Null,
            SVal::Bool(b) => Json::Bool(*b),
            SVal::Num(s) => Json::Num(s.clone()),
            SVal::Str(s) => Json::Str(s.clone()),
            SVal::List => Json::Arr(Vec::new()),
            SVal::Map(_) => Json::Obj(Vec::new()),
            SVal::Float => {
                return Err(Diagnostic::new(Code::SourceTypeError, "a float is not accepted; use a decimal"))
            }
            SVal::Other => return Err(Diagnostic::new(Code::SourceTypeError, "unsupported value type")),
        };
        let checked = source::value_type_check(&j, &d.vtype, d.unit.as_deref(), false)?;
        out.push(Fetched {
            name: d.name.clone(),
            checked,
            retrieved_at: en.retrieved_at.clone(),
            // `named` is not part of the identity: the entry's value was type-checked above and is
            // then ignored in favour of the declaration's.
            source: SourceRef { named: spec.named.clone(), ..en.source.clone() },
        });
    }
    Ok(out)
}
