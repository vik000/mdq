//! URL and IP classification (sources-and-permissions.md section 4; G44 in docs/TEST_PLAN.md).
//! Offline: no DNS. The transport re-checks every resolved address with `ip_is_blocked`.

use crate::diag::{Code, Diagnostic};
use std::net::{Ipv4Addr, Ipv6Addr};

type Res<T> = Result<T, Diagnostic>;

const MAX_URL: usize = 8192;
const V4_BLOCKED: [(u32, u32); 16] = [
    (0x0000_0000, 8),
    (0x0A00_0000, 8),
    (0x6440_0000, 10),
    (0x7F00_0000, 8),
    (0xA9FE_0000, 16),
    (0xAC10_0000, 12),
    (0xC000_0000, 24),
    (0xC000_0200, 24),
    (0xC058_6300, 24),
    (0xC0A8_0000, 16),
    (0xC612_0000, 15),
    (0xC633_6400, 24),
    (0xCB00_7100, 24),
    (0xE000_0000, 4),
    (0xF000_0000, 4),
    (0xA83F_8110, 32),
];
const BLOCKED_SUFFIXES: [&str; 8] = ["localhost", "local", "internal", "localdomain", "home.arpa", "lan", "corp", "home"];

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Ip {
    V4(u32),
    V6([u16; 8]),
}

/// A canonical IPv4 or IPv6 literal (no zone, no brackets, no legacy IPv4 forms).
pub fn parse_ip(s: &str) -> Option<Ip> {
    if !s.is_ascii() || s.contains(|c| matches!(c, '%' | '/' | ' ')) {
        return None;
    }
    if s.contains(':') {
        s.parse::<Ipv6Addr>().ok().map(|a| Ip::V6(a.segments()))
    } else {
        s.parse::<Ipv4Addr>().ok().map(|a| Ip::V4(u32::from(a)))
    }
}

fn v4_blocked(n: u32) -> bool {
    V4_BLOCKED.iter().any(|&(base, bits)| (n >> (32 - bits)) == (base >> (32 - bits)))
}

fn v6_blocked(h: &[u16; 8]) -> bool {
    let v4 = |a: u16, b: u16| v4_blocked(((a as u32) << 16) | b as u32);
    if h[..5].iter().all(|&x| x == 0) && h[5] == 0xFFFF {
        return v4(h[6], h[7]);
    }
    if h[0] == 0x64 && h[1] == 0xFF9B {
        if h[2..6].iter().all(|&x| x == 0) {
            return v4(h[6], h[7]);
        }
        return true;
    }
    if h[..6].iter().all(|&x| x == 0) {
        return true;
    }
    if h[0] & 0xE000 != 0x2000 {
        return true;
    }
    if h[0] == 0x2002 {
        return v4(h[1], h[2]);
    }
    if h[0] == 0x2001 && (h[1] < 0x0200 || h[1] == 0x0DB8) {
        return true;
    }
    h[0] == 0x3FFF && h[1] < 0x1000
}

/// Loopback, private, link-local, CGNAT, multicast, reserved, documentation and metadata-service
/// addresses; IPv4-mapped, NAT64 and 6to4 addresses follow their embedded IPv4 address.
pub fn ip_is_blocked(ip: &Ip) -> bool {
    match ip {
        Ip::V4(n) => v4_blocked(*n),
        Ip::V6(h) => v6_blocked(h),
    }
}

fn v4_text(n: u32) -> String {
    format!("{}.{}.{}.{}", n >> 24, (n >> 16) & 255, (n >> 8) & 255, n & 255)
}

fn v6_text(h: &[u16; 8]) -> String {
    let (mut best_start, mut best_len) = (0usize, 0usize);
    let mut i = 0;
    while i < 8 {
        if h[i] == 0 {
            let mut j = i;
            while j < 8 && h[j] == 0 {
                j += 1;
            }
            if j - i > best_len && j - i >= 2 {
                best_start = i;
                best_len = j - i;
            }
            i = j;
        } else {
            i += 1;
        }
    }
    let hex = |s: &[u16]| s.iter().map(|x| format!("{x:x}")).collect::<Vec<_>>().join(":");
    if best_len == 0 {
        return hex(h);
    }
    format!("{}::{}", hex(&h[..best_start]), hex(&h[best_start + best_len..]))
}

fn v4_part(p: &str) -> Option<u64> {
    if p.is_empty() {
        return None;
    }
    let (base, s) = if p.len() >= 2 && (p.starts_with("0x") || p.starts_with("0X")) {
        (16, &p[2..])
    } else if p.len() >= 2 && p.starts_with('0') {
        (8, &p[1..])
    } else {
        (10, p)
    };
    if s.is_empty() {
        return Some(0);
    }
    let ok = match base {
        16 => s.bytes().all(|c| c.is_ascii_hexdigit()),
        8 => s.bytes().all(|c| (b'0'..=b'7').contains(&c)),
        _ => s.bytes().all(|c| c.is_ascii_digit()),
    };
    if !ok {
        return None;
    }
    u64::from_str_radix(s, base).ok()
}

fn ends_in_number(label: &str) -> bool {
    if !label.is_empty() && label.bytes().all(|c| c.is_ascii_digit()) {
        return true;
    }
    (label.starts_with("0x") || label.starts_with("0X")) && label[2..].bytes().all(|c| c.is_ascii_hexdigit())
}

fn invalid(msg: &str) -> Diagnostic {
    Diagnostic::new(Code::SourceInvalid, msg)
}

fn denied(msg: &str) -> Diagnostic {
    Diagnostic::new(Code::SecurityDenied, msg)
}

#[derive(Debug, Clone, PartialEq)]
pub struct Classified {
    pub host: String,
    /// `name`, `ipv4` or `ipv6`.
    pub kind: &'static str,
    pub port: u16,
    pub origin: String,
}

/// Classify an absolute URL. Messages never contain parts of the URL.
pub fn url_classify(url: &str) -> Res<Classified> {
    if url.chars().count() > MAX_URL {
        return Err(Diagnostic::new(Code::ResourceLimit, format!("URL is longer than {MAX_URL} characters")));
    }
    if url.chars().any(|c| (c as u32) <= 0x20 || (c as u32) >= 0x7F || c == '\\') {
        return Err(invalid("URL contains whitespace, a control, a backslash or a non-ASCII character"));
    }
    let scheme = match url.find(':') {
        Some(i) if i > 0 => &url[..i],
        _ => "",
    };
    let scheme_ok = scheme.chars().next().is_some_and(|c| c.is_ascii_alphabetic())
        && scheme.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '+' | '-' | '.'));
    if !scheme_ok {
        return Err(invalid("not an absolute URL"));
    }
    if !scheme.eq_ignore_ascii_case("https") {
        return Err(denied("only https URLs are allowed"));
    }
    let Some(rest) = url[scheme.len() + 1..].strip_prefix("//") else {
        return Err(invalid("an https URL needs '//' after the scheme"));
    };
    let cut = rest.find(|c| matches!(c, '/' | '?' | '#')).unwrap_or(rest.len());
    let auth = &rest[..cut];
    if auth.contains('@') {
        return Err(denied("credentials in URLs are not allowed"));
    }
    let bracket = auth.starts_with('[');
    let (hostpart, port_s): (&str, Option<&str>) = if bracket {
        let Some(end) = auth.find(']') else {
            return Err(invalid("unterminated IPv6 host"));
        };
        let after = &auth[end + 1..];
        if !after.is_empty() && !after.starts_with(':') {
            return Err(invalid("malformed host"));
        }
        (&auth[1..end], if after.is_empty() { None } else { Some(&after[1..]) })
    } else {
        match auth.rfind(':') {
            Some(i) => (&auth[..i], Some(&auth[i + 1..])),
            None => (auth, None),
        }
    };
    let port: u16 = match port_s {
        None => 443,
        Some(p) => {
            let n = if p.is_empty() || !p.bytes().all(|c| c.is_ascii_digit()) { None } else { p.parse::<u32>().ok() };
            match n {
                Some(n) if (1..=65535).contains(&n) => n as u16,
                _ => return Err(invalid("invalid port")),
            }
        }
    };
    if bracket {
        let ip = if hostpart.contains(':') { parse_ip(hostpart) } else { None };
        let Some(Ip::V6(h)) = ip else {
            return Err(invalid("invalid IPv6 address"));
        };
        if v6_blocked(&h) {
            return Err(denied("the host is in a blocked address range"));
        }
        let host = v6_text(&h);
        let origin = format!("https://[{host}]:{port}");
        return Ok(Classified { host, kind: "ipv6", port, origin });
    }
    if hostpart.contains(|c| matches!(c, '%' | '[' | ']' | ':')) {
        return Err(invalid("invalid host"));
    }
    let h = hostpart.strip_suffix('.').unwrap_or(hostpart);
    if h.is_empty() {
        return Err(invalid("the URL has no host"));
    }
    let labels: Vec<&str> = h.split('.').collect();
    let (host, kind) = if ends_in_number(labels[labels.len() - 1]) {
        if labels.len() > 4 {
            return Err(invalid("invalid IPv4 address"));
        }
        let nums: Vec<Option<u64>> = labels.iter().map(|p| v4_part(p)).collect();
        if nums.iter().any(|n| n.is_none()) {
            return Err(invalid("invalid IPv4 address"));
        }
        let nums: Vec<u64> = nums.into_iter().flatten().collect();
        let last = nums[nums.len() - 1];
        if nums[..nums.len() - 1].iter().any(|&n| n > 255) || last >= 256u64.pow(5 - nums.len() as u32) {
            return Err(invalid("invalid IPv4 address"));
        }
        let mut n = last;
        for (k, x) in nums[..nums.len() - 1].iter().enumerate() {
            n += x << (8 * (3 - k));
        }
        let n = n as u32;
        if v4_blocked(n) {
            return Err(denied("the host is in a blocked address range"));
        }
        (v4_text(n), "ipv4")
    } else {
        let h = h.to_ascii_lowercase();
        let label_ok = |l: &str| {
            (1..=63).contains(&l.len())
                && !l.starts_with('-')
                && !l.ends_with('-')
                && l.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'-' || c == b'_')
        };
        if h.len() > 253 || !h.split('.').all(label_ok) {
            return Err(invalid("invalid host name"));
        }
        if !h.contains('.') || BLOCKED_SUFFIXES.iter().any(|s| h == *s || h.ends_with(&format!(".{s}"))) {
            return Err(denied("the host name is not allowed"));
        }
        (h, "name")
    };
    let origin = format!("https://{host}:{port}");
    Ok(Classified { host, kind, port, origin })
}
