//! Network policy (sources-and-permissions.md section 4; G45 in docs/TEST_PLAN.md).
//! The effective policy is the intersection of two TRUSTED layers (reader/user, library) and the
//! document's REQUESTS; the most restrictive wins and a document can only narrow.

use crate::diag::{Code, Diagnostic};
use crate::net::{self, denied, ends_in_number, parse_ip, v4_text, v6_text, Ip};
use std::collections::BTreeSet;

type Res<T> = Result<T, Diagnostic>;

const KEYS: [&str; 8] = ["network", "hosts", "ports", "private_endpoints", "max_bytes", "timeout", "max_redirects", "max_concurrency"];
const LIMITS: [&str; 4] = ["max_bytes", "timeout", "max_redirects", "max_concurrency"];
const LIM_DEFAULT: [u64; 4] = [1 << 20, 10, 0, 4];
const LIM_CAP: [u64; 4] = [16 << 20, 60, 5, 16];
const LIM_MIN: [i64; 4] = [1, 1, 0, 1];
const MAX_LIST: usize = 256;

/// A value of a policy mapping, converted from the host language without interpretation.
#[derive(Debug, Clone, PartialEq)]
pub enum PVal {
    None,
    Bool(bool),
    Int(i64),
    Str(String),
    List(Vec<PVal>),
    Map(Vec<(String, PVal)>),
    Other,
}

pub type Map = [(String, PVal)];

fn err(msg: impl AsRef<str>) -> Diagnostic {
    Diagnostic::new(Code::SourceInvalid, format!("invalid policy: {}", msg.as_ref()))
}

fn opt<'a>(d: &'a Map, k: &str) -> Option<&'a PVal> {
    d.iter().find(|(n, _)| n == k).map(|x| &x.1)
}

fn int(v: &PVal, what: &str, lo: i64, hi: i64) -> Res<i64> {
    match v {
        PVal::Int(n) => {
            if *n < lo || *n > hi {
                Err(err(format!("{what} must be between {lo} and {hi}")))
            } else {
                Ok(*n)
            }
        }
        _ => Err(err(format!("{what} must be an integer"))),
    }
}

fn pat_norm(v: &PVal, what: &str) -> Res<String> {
    let PVal::Str(p) = v else {
        return Err(err(format!("{what} entries must be strings")));
    };
    let p = p.to_lowercase();
    let ipish = p.contains(':') || (!p.replace('.', "").is_empty() && p.replace('.', "").bytes().all(|c| c.is_ascii_digit()) && p.matches('.').count() == 3);
    if ipish {
        return match parse_ip(&p) {
            Some(Ip::V4(n)) => Ok(v4_text(n)),
            Some(Ip::V6(h)) => Ok(v6_text(&h)),
            None => Err(err(format!("{what}: invalid IP address pattern"))),
        };
    }
    let wild = p.starts_with("*.");
    let base = if wild { &p[2..] } else { p.as_str() };
    let labels: Vec<&str> = base.split('.').collect();
    let label_ok = |l: &&str| {
        (1..=63).contains(&l.len())
            && !l.starts_with('-')
            && !l.ends_with('-')
            && l.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-' || c == b'_')
    };
    if base.len() > 253 || !labels.iter().all(label_ok) {
        return Err(err(format!("{what}: invalid host pattern")));
    }
    if wild && labels.len() < 2 {
        return Err(err(format!("{what}: a wildcard needs at least two labels after '*.'")));
    }
    if base.bytes().last().is_some_and(|c| c.is_ascii_digit()) && ends_in_number(labels[labels.len() - 1]) {
        return Err(err(format!("{what}: invalid host pattern")));
    }
    Ok(p)
}

fn pat_list(v: Option<&PVal>, what: &str) -> Res<Option<Vec<String>>> {
    match v {
        None | Some(PVal::None) => Ok(None),
        Some(PVal::List(items)) => {
            if items.len() > MAX_LIST {
                return Err(Diagnostic::new(Code::ResourceLimit, format!("{what} has more than {MAX_LIST} entries")));
            }
            let mut set = BTreeSet::new();
            for i in items {
                set.insert(pat_norm(i, what)?);
            }
            Ok(Some(set.into_iter().collect()))
        }
        Some(_) => Err(err(format!("{what} must be a list"))),
    }
}

fn pat_match(host: &str, p: &str) -> bool {
    if let Some(suffix) = p.strip_prefix('*') {
        parse_ip(host).is_none() && host.ends_with(suffix) && host.len() > suffix.len()
    } else {
        host == p
    }
}

fn pat_and(a: &str, b: &str) -> Option<String> {
    if a == b {
        return Some(a.to_string());
    }
    match (a.starts_with("*."), b.starts_with("*.")) {
        (false, false) => None,
        (true, false) => pat_match(b, a).then(|| b.to_string()),
        (false, true) => pat_match(a, b).then(|| a.to_string()),
        (true, true) => {
            let (sa, sb) = (&a[2..], &b[2..]);
            if sa.ends_with(&format!(".{sb}")) {
                Some(a.to_string())
            } else if sb.ends_with(&format!(".{sa}")) {
                Some(b.to_string())
            } else {
                None
            }
        }
    }
}

fn pat_inter(a: &[String], b: &[String]) -> Vec<String> {
    let mut set = BTreeSet::new();
    for x in a {
        for y in b {
            if let Some(r) = pat_and(x, y) {
                set.insert(r);
            }
        }
    }
    set.into_iter().collect()
}

struct Layer {
    network: Option<String>,
    hosts: Option<Vec<String>>,
    ports: Option<Vec<u16>>,
    private: Option<Vec<String>>,
    limits: [Option<u64>; 4],
    required: bool,
}

fn layer(d: Option<&Map>, effective: bool) -> Res<Layer> {
    let d = d.unwrap_or(&[]);
    for (k, _) in d {
        if !KEYS.contains(&k.as_str()) && !(effective && k == "network_required") {
            return Err(err(format!("unknown key {k:?}")));
        }
    }
    let network = match opt(d, "network") {
        None | Some(PVal::None) => None,
        Some(PVal::Str(s)) if s == "allow" || s == "deny" => Some(s.clone()),
        Some(_) => return Err(err("network must be 'allow' or 'deny'")),
    };
    let ports = match opt(d, "ports") {
        None | Some(PVal::None) => None,
        Some(PVal::List(items)) => {
            if items.len() > MAX_LIST {
                return Err(Diagnostic::new(Code::ResourceLimit, "ports has too many entries"));
            }
            let mut set = BTreeSet::new();
            for i in items {
                set.insert(int(i, "ports entry", 1, 65535)? as u16);
            }
            Some(set.into_iter().collect())
        }
        Some(_) => return Err(err("ports must be a list")),
    };
    let mut limits = [None; 4];
    for (k, name) in LIMITS.iter().enumerate() {
        limits[k] = match opt(d, name) {
            None | Some(PVal::None) => None,
            Some(v) => Some((int(v, name, LIM_MIN[k], i64::MAX)? as u64).min(LIM_CAP[k])),
        };
    }
    let required = match opt(d, "network_required") {
        None | Some(PVal::None) => false,
        Some(PVal::Bool(b)) => *b,
        Some(_) => return Err(err("network_required must be a boolean")),
    };
    Ok(Layer {
        network,
        hosts: pat_list(opt(d, "hosts"), "hosts")?,
        ports,
        private: pat_list(opt(d, "private_endpoints"), "private_endpoints")?,
        limits,
        required,
    })
}

struct Doc {
    deny: bool,
    hosts: Option<Vec<String>>,
    required: bool,
}

fn submap<'a>(v: Option<&'a PVal>, what: &str) -> Res<&'a Map> {
    match v {
        None | Some(PVal::None) => Ok(&[]),
        Some(PVal::Map(m)) => Ok(m),
        Some(_) => Err(err(format!("{what} must be a mapping"))),
    }
}

fn doc_layer(d: Option<&Map>) -> Res<Doc> {
    let Some(d) = d else {
        return Ok(Doc { deny: false, hosts: None, required: false });
    };
    for (k, _) in d {
        if k != "requirements" && k != "security" {
            return Err(err(format!("a document can only carry 'requirements' and 'security', not {k:?}")));
        }
    }
    let req = submap(opt(d, "requirements"), "requirements")?;
    let sec = submap(opt(d, "security"), "security")?;
    for (k, _) in req {
        if k != "network" && k != "hosts" {
            return Err(err(format!("a document can only request 'network' and 'hosts', not {k:?}")));
        }
    }
    for (k, _) in sec {
        if k != "network" {
            return Err(err(format!("a document can only request 'network', not {k:?}")));
        }
    }
    let word = |v: Option<&PVal>, default: &'static str, ok: &[&str], what: &str| -> Res<String> {
        match v {
            None => Ok(default.to_string()),
            Some(PVal::Str(s)) if ok.contains(&s.as_str()) => Ok(s.clone()),
            Some(_) => Err(err(format!("{what} must be one of {}", ok.join(", ")))),
        }
    };
    let rn = word(opt(req, "network"), "optional", &["none", "optional", "required"], "requirements.network")?;
    let sn = word(opt(sec, "network"), "request", &["request", "none"], "security.network")?;
    Ok(Doc { deny: rn == "none" || sn == "none", hosts: pat_list(opt(req, "hosts"), "hosts")?, required: rn == "required" })
}

#[derive(Debug, Clone, PartialEq)]
pub struct Effective {
    pub network_allow: bool,
    pub hosts: Option<Vec<String>>,
    pub ports: Vec<u16>,
    pub private_endpoints: Vec<String>,
    /// max_bytes, timeout, max_redirects, max_concurrency
    pub limits: [u64; 4],
    pub network_required: bool,
}

pub fn policy_intersect(host: Option<&Map>, library: Option<&Map>, document: Option<&Map>) -> Res<Effective> {
    let docs: Vec<&Map> = document.into_iter().collect();
    policy_intersect_docs(host, library, &docs)
}

/// `policy_intersect` with several document request layers (manifest, `.mdq.yaml` files,
/// frontmatter, sidecar). They compose by the most restrictive rule: any `none` denies, any
/// `required` sets `network_required`, host lists are intersected. A request never grants.
pub fn policy_intersect_docs(host: Option<&Map>, library: Option<&Map>, documents: &[&Map]) -> Res<Effective> {
    let h = layer(host, false)?;
    let l = layer(library, false)?;
    let ds: Vec<Doc> = documents.iter().map(|d| doc_layer(Some(d))).collect::<Res<_>>()?;
    let deny = ds.iter().any(|d| d.deny);
    let required = ds.iter().any(|d| d.required);
    let allow = h.network.as_deref() == Some("allow") && l.network.as_deref() == Some("allow") && !deny;
    let mut hosts: Option<Vec<String>> = None;
    let doc_hosts: Vec<&Option<Vec<String>>> = ds.iter().map(|d| &d.hosts).collect();
    // `flatten` skips the layers without a host list (None = unrestricted).
    for x in [&h.hosts, &l.hosts].into_iter().chain(doc_hosts).flatten() {
        hosts = Some(match hosts {
            None => x.clone(),
            Some(cur) => pat_inter(&cur, x),
        });
    }
    let hp: BTreeSet<u16> = h.ports.clone().unwrap_or_else(|| vec![443]).into_iter().collect();
    let lp: BTreeSet<u16> = l.ports.clone().unwrap_or_else(|| vec![443]).into_iter().collect();
    let ports = hp.intersection(&lp).copied().collect();
    let private_endpoints = pat_inter(&h.private.unwrap_or_default(), &l.private.unwrap_or_default());
    let mut limits = [0u64; 4];
    for k in 0..4 {
        limits[k] = h.limits[k].unwrap_or(LIM_DEFAULT[k]).min(l.limits[k].unwrap_or(LIM_DEFAULT[k]));
    }
    Ok(Effective { network_allow: allow, hosts, ports, private_endpoints, limits, network_required: required })
}

/// An effective policy as the mapping `policy_decide` and `source_fetch` take (the same keys the
/// Python `policy_intersect` returns).
pub fn effective_map(e: &Effective) -> Vec<(String, PVal)> {
    let strs = |v: &[String]| PVal::List(v.iter().map(|x| PVal::Str(x.clone())).collect());
    let mut m = vec![
        ("network".to_string(), PVal::Str(if e.network_allow { "allow" } else { "deny" }.to_string())),
        ("hosts".to_string(), e.hosts.as_ref().map(|h| strs(h)).unwrap_or(PVal::None)),
        ("ports".to_string(), PVal::List(e.ports.iter().map(|p| PVal::Int(*p as i64)).collect())),
        ("private_endpoints".to_string(), strs(&e.private_endpoints)),
    ];
    for (k, v) in ["max_bytes", "timeout", "max_redirects", "max_concurrency"].iter().zip(e.limits) {
        m.push((k.to_string(), PVal::Int(v as i64)));
    }
    m.push(("network_required".to_string(), PVal::Bool(e.network_required)));
    m
}

fn eff(policy: &Map) -> Res<Effective> {
    let p = layer(Some(policy), true)?;
    let mut limits = [0u64; 4];
    for k in 0..4 {
        limits[k] = p.limits[k].unwrap_or(LIM_DEFAULT[k]);
    }
    Ok(Effective {
        network_allow: p.network.as_deref() == Some("allow"),
        hosts: p.hosts,
        ports: p.ports.unwrap_or_else(|| vec![443]),
        private_endpoints: p.private.unwrap_or_default(),
        limits,
        network_required: p.required,
    })
}

#[derive(Debug, Clone, PartialEq)]
pub struct Decision {
    pub host: String,
    pub kind: &'static str,
    pub port: u16,
    pub origin: String,
    pub private: bool,
    pub limits: [u64; 4],
}

/// Apply an effective policy to one URL. `redirect_count` is the number of redirects already followed.
pub fn policy_decide(policy: &Map, url: &str, redirect_count: u64) -> Res<Decision> {
    let e = eff(policy)?;
    if !e.network_allow {
        let more = if e.network_required { "; the document requires it" } else { "" };
        return Err(denied(&format!("network access is denied by policy{more}")));
    }
    let r = net::url_parts(url)?;
    if redirect_count > e.limits[2] {
        return Err(denied("redirect limit exceeded"));
    }
    if !e.ports.contains(&r.port) {
        return Err(denied("the port is not allowed by policy"));
    }
    if let Some(hosts) = &e.hosts {
        if !hosts.iter().any(|p| pat_match(&r.host, p)) {
            return Err(denied("the host is not allowed by policy"));
        }
    }
    let mut private = false;
    if let Some(why) = r.blocked {
        if !e.private_endpoints.iter().any(|p| pat_match(&r.host, p)) {
            return Err(denied(why));
        }
        private = true;
    }
    Ok(Decision { host: r.host, kind: r.kind, port: r.port, origin: r.origin, private, limits: e.limits })
}

/// Re-check a resolved address (DNS rebinding): `None` if `address` is not an IP literal.
pub fn policy_ip_allowed(policy: &Map, host: &str, address: &str) -> Res<Option<bool>> {
    let e = eff(policy)?;
    let Some(ip) = parse_ip(address) else {
        return Ok(None);
    };
    if !e.network_allow {
        return Ok(Some(false));
    }
    if !net::ip_is_blocked(&ip) {
        return Ok(Some(true));
    }
    let h = host.to_lowercase();
    let h = h.strip_suffix('.').unwrap_or(&h);
    Ok(Some(e.private_endpoints.iter().any(|p| pat_match(h, p))))
}
