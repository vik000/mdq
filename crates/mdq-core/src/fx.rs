//! Currency observation selection (spec: extended-markdown.md section 8.8).
//!
//! The engine never fetches rates. The host supplies observations and, for
//! each conversion, a valuation timestamp or an explicit observation id.
//! Selection is deterministic and never inverts, triangulates, falls back to
//! an older rate than the rule allows, or reads the clock.
//!
//! Decisions where the spec is silent (docs/TEST_PLAN.md G6, G20):
//! * timestamps must be `YYYY-MM-DDTHH:MM:SS[.f]` (1-9 fraction digits) plus
//!   `Z` or an explicit `+HH:MM`/`-HH:MM` offset; a timestamp without an
//!   offset is invalid, never assumed to be UTC;
//! * malformed host input (bad rate, timestamp, currency code, duplicate id,
//!   unknown field) is `MDQ_SOURCE_INVALID`; a missing or ambiguous
//!   observation is `MDQ_SOURCE_UNRESOLVED`;
//! * an observation id is a string, unique across the observations;
//! * the optional market is a REQUIREMENT on the conversion request: when set,
//!   only observations with exactly that market qualify;
//! * when a request has both an id and a valuation, the id wins;
//! * `EUR to EUR` is the identity and needs no observation.

use std::collections::{HashMap, HashSet};

use crate::decimal::Decimal;
use crate::diag::{Code, Diagnostic};

type Res<T> = Result<T, Diagnostic>;

fn invalid(message: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::SourceInvalid, message)
}

fn unresolved(message: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::SourceUnresolved, message)
}

/// A point in time, normalised to UTC. Orders by seconds, then nanoseconds.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct Instant {
    pub secs: i64,
    pub nanos: u32,
}

fn digits(b: &[u8], from: usize, len: usize) -> Option<i64> {
    let part = b.get(from..from + len)?;
    if !part.iter().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let mut n: i64 = 0;
    for c in part {
        n = n * 10 + i64::from(c - b'0');
    }
    Some(n)
}

fn is_leap(y: i64) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ => {
            if is_leap(y) {
                29
            } else {
                28
            }
        }
    }
}

/// Days since 1970-01-01 for a proleptic Gregorian date (year >= 1).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12; // March = 0
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

impl Instant {
    pub fn parse(s: &str) -> Res<Instant> {
        let bad = || {
            invalid(format!(
                "invalid timestamp {s:?}: expected YYYY-MM-DDTHH:MM:SS[.fraction] followed by Z or an explicit +HH:MM offset"
            ))
        };
        if !s.is_ascii() {
            return Err(bad());
        }
        let b = s.as_bytes();
        if b.len() < 20 {
            return Err(bad());
        }
        if b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[13] != b':' || b[16] != b':' {
            return Err(bad());
        }
        let y = digits(b, 0, 4).ok_or_else(bad)?;
        let mo = digits(b, 5, 2).ok_or_else(bad)?;
        let d = digits(b, 8, 2).ok_or_else(bad)?;
        let h = digits(b, 11, 2).ok_or_else(bad)?;
        let mi = digits(b, 14, 2).ok_or_else(bad)?;
        let sec = digits(b, 17, 2).ok_or_else(bad)?;
        if y < 1 || !(1..=12).contains(&mo) || d < 1 || d > days_in_month(y, mo) {
            return Err(bad());
        }
        if h > 23 || mi > 59 || sec > 59 {
            return Err(bad());
        }
        let mut i = 19;
        let mut nanos: u32 = 0;
        if i < b.len() && b[i] == b'.' {
            let start = i + 1;
            let mut j = start;
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
            let n = j - start;
            if n == 0 || n > 9 {
                return Err(bad());
            }
            let frac: u32 = s[start..j].parse().map_err(|_| bad())?;
            nanos = frac * 10u32.pow(9 - n as u32);
            i = j;
        }
        let offset: i64 = if i + 1 == b.len() && b[i] == b'Z' {
            0
        } else if i + 6 == b.len() && (b[i] == b'+' || b[i] == b'-') && b[i + 3] == b':' {
            let oh = digits(b, i + 1, 2).ok_or_else(bad)?;
            let om = digits(b, i + 4, 2).ok_or_else(bad)?;
            if oh > 23 || om > 59 {
                return Err(bad());
            }
            let magnitude = oh * 3600 + om * 60;
            if b[i] == b'-' {
                -magnitude
            } else {
                magnitude
            }
        } else {
            return Err(bad());
        };
        let secs = days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60 + sec - offset;
        Ok(Instant { secs, nanos })
    }
}

/// One exchange-rate observation supplied by the host.
#[derive(Debug, Clone)]
pub struct Observation {
    pub id: Option<String>,
    pub from: String,
    pub to: String,
    pub rate: Decimal,
    /// The timestamp exactly as supplied (for provenance).
    pub observed_at: String,
    pub instant: Instant,
    pub provider: String,
    pub market: Option<String>,
}

#[derive(Debug, Clone)]
enum Pick {
    Id(String),
    /// Valuation instant and its original text.
    Latest(Instant, String),
}

#[derive(Debug, Clone)]
struct Request {
    pick: Pick,
    market: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub struct FxContext {
    observations: Vec<Observation>,
    default: Option<Request>,
    pins: HashMap<(String, String), Request>,
}

fn is_currency(s: &str) -> bool {
    s.len() == 3 && s.chars().all(|c| c.is_ascii_uppercase())
}

fn sorted_keys(map: &HashMap<String, String>) -> Vec<&String> {
    let mut keys: Vec<&String> = map.keys().collect();
    keys.sort();
    keys
}

fn check_keys(map: &HashMap<String, String>, allowed: &[&str], what: &str) -> Res<()> {
    for key in sorted_keys(map) {
        if !allowed.contains(&key.as_str()) {
            return Err(invalid(format!("unknown {what} field {key:?}")));
        }
    }
    Ok(())
}

fn non_empty(map: &HashMap<String, String>, key: &str, what: &str) -> Res<Option<String>> {
    match map.get(key) {
        None => Ok(None),
        Some(v) if v.is_empty() => Err(invalid(format!("{what} field `{key}` must not be empty"))),
        Some(v) => Ok(Some(v.clone())),
    }
}

fn observation_from(map: &HashMap<String, String>) -> Res<Observation> {
    check_keys(map, &["id", "from", "to", "rate", "observed_at", "provider", "market"], "observation")?;
    let required = |key: &str| -> Res<String> {
        non_empty(map, key, "observation")?
            .ok_or_else(|| invalid(format!("observation is missing the required field `{key}`")))
    };
    let from = required("from")?;
    let to = required("to")?;
    if !is_currency(&from) || !is_currency(&to) {
        return Err(invalid(format!("invalid currency pair {from:?} to {to:?}: codes are three capital letters")));
    }
    if from == to {
        return Err(invalid(format!("observation {from} to {to} has the same currency on both sides")));
    }
    let rate_text = required("rate")?;
    if rate_text.starts_with('+') || rate_text.starts_with('-') {
        return Err(invalid(format!("invalid rate {rate_text:?}: must be a positive decimal")));
    }
    let rate = Decimal::parse(&rate_text)
        .map_err(|d| invalid(format!("invalid rate {rate_text:?}: {}", d.message)))?;
    if rate.is_zero() {
        return Err(invalid("invalid rate \"0\": must be a positive decimal"));
    }
    let observed_at = required("observed_at")?;
    let instant = Instant::parse(&observed_at)?;
    Ok(Observation {
        id: non_empty(map, "id", "observation")?,
        from,
        to,
        rate,
        observed_at,
        instant,
        provider: required("provider")?,
        market: non_empty(map, "market", "observation")?,
    })
}

fn latest_request(valuation: &str, market: Option<String>) -> Res<Request> {
    let instant = Instant::parse(valuation)?;
    Ok(Request { pick: Pick::Latest(instant, valuation.to_string()), market })
}

impl FxContext {
    /// No observations and no valuation: every currency conversion is unresolved.
    pub fn empty() -> FxContext {
        FxContext::default()
    }

    /// Build a context from host data.
    /// * `observations`: records with keys id, from, to, rate, observed_at, provider, market;
    /// * `valuation`/`market`: the default request for every conversion;
    /// * `pins`: per-pair requests keyed `"EUR>USD"` with keys id, valuation, market.
    pub fn from_parts(
        observations: &[HashMap<String, String>],
        valuation: Option<&str>,
        market: Option<&str>,
        pins: &HashMap<String, HashMap<String, String>>,
    ) -> Res<FxContext> {
        let mut parsed = Vec::new();
        let mut ids: HashSet<String> = HashSet::new();
        for record in observations {
            let obs = observation_from(record)?;
            if let Some(id) = &obs.id {
                if !ids.insert(id.clone()) {
                    return Err(invalid(format!("duplicate observation id {id:?}")));
                }
            }
            parsed.push(obs);
        }
        let default = match (valuation, market) {
            (Some(v), m) => Some(latest_request(v, m.map(|x| x.to_string()))?),
            (None, Some(_)) => return Err(invalid("`market` needs a `valuation`")),
            (None, None) => None,
        };
        let mut pin_map = HashMap::new();
        let mut keys: Vec<&String> = pins.keys().collect();
        keys.sort();
        for key in keys {
            let (from, to) = key
                .split_once('>')
                .filter(|(f, t)| is_currency(f) && is_currency(t))
                .ok_or_else(|| invalid(format!("invalid pin key {key:?}: expected \"EUR>USD\"")))?;
            let spec = &pins[key];
            check_keys(spec, &["id", "valuation", "market"], "pin")?;
            let pin_market = non_empty(spec, "market", "pin")?;
            let id = non_empty(spec, "id", "pin")?;
            let val = non_empty(spec, "valuation", "pin")?;
            let request = match (id, val) {
                (Some(id), v) => {
                    if let Some(v) = v {
                        Instant::parse(&v)?; // still validated; the id wins
                    }
                    Request { pick: Pick::Id(id), market: pin_market }
                }
                (None, Some(v)) => latest_request(&v, pin_market)?,
                (None, None) => {
                    return Err(invalid(format!("pin {key:?} needs an `id` or a `valuation`")));
                }
            };
            pin_map.insert((from.to_string(), to.to_string()), request);
        }
        Ok(FxContext { observations: parsed, default, pins: pin_map })
    }

    /// Pick the observation for converting `from` to `to` (never inverted).
    pub fn select(&self, from: &str, to: &str) -> Res<&Observation> {
        let request = self
            .pins
            .get(&(from.to_string(), to.to_string()))
            .or(self.default.as_ref())
            .ok_or_else(|| {
                unresolved(format!(
                    "no valuation timestamp or observation id was supplied to convert {from} to {to}"
                ))
            })?;
        let candidates: Vec<&Observation> = self
            .observations
            .iter()
            .filter(|o| o.from == from && o.to == to)
            .filter(|o| match &request.market {
                Some(m) => o.market.as_deref() == Some(m.as_str()),
                None => true,
            })
            .collect();
        match &request.pick {
            Pick::Id(id) => candidates
                .into_iter()
                .find(|o| o.id.as_deref() == Some(id.as_str()))
                .ok_or_else(|| {
                    unresolved(format!("no exchange-rate observation with id {id:?} for {from} to {to}"))
                }),
            Pick::Latest(at, text) => {
                let eligible: Vec<&Observation> = candidates.into_iter().filter(|o| o.instant <= *at).collect();
                let best = match eligible.iter().map(|o| o.instant).max() {
                    Some(b) => b,
                    None => {
                        return Err(unresolved(format!(
                            "no exchange-rate observation for {from} to {to} at or before {text}"
                        )));
                    }
                };
                let tied: Vec<&Observation> = eligible.into_iter().filter(|o| o.instant == best).collect();
                if tied.len() > 1 {
                    return Err(unresolved(format!(
                        "{} exchange-rate observations for {from} to {to} share the latest timestamp {}; specify an observation id",
                        tied.len(),
                        tied[0].observed_at
                    )));
                }
                Ok(tied[0])
            }
        }
    }
}

/// Test hook: select one observation from raw host records.
pub fn select_from_parts(
    observations: &[HashMap<String, String>],
    from: &str,
    to: &str,
    valuation: Option<&str>,
    id: Option<&str>,
    market: Option<&str>,
) -> Res<Observation> {
    let mut spec: HashMap<String, String> = HashMap::new();
    if let Some(v) = valuation {
        spec.insert("valuation".to_string(), v.to_string());
    }
    if let Some(i) = id {
        spec.insert("id".to_string(), i.to_string());
    }
    if let Some(m) = market {
        spec.insert("market".to_string(), m.to_string());
    }
    let mut pins = HashMap::new();
    if !spec.is_empty() {
        pins.insert(format!("{from}>{to}"), spec);
    }
    let ctx = FxContext::from_parts(observations, None, None, &pins)?;
    ctx.select(from, to).cloned()
}
