//! Unit catalogue and exact conversion (spec: calculation-units.md).
//!
//! The table lives in `spec/units.tsv` and is embedded into the binary at
//! compile time, so the spec file is the single source of truth.
//!
//! Conversion rule: the EXACT rational value of
//!     ((value + offset_from) * scale_from) / scale_to - offset_to
//! is computed with big integers, then rounded ONCE to 28 significant digits.

use std::collections::HashMap;
use std::sync::OnceLock;

use num_bigint::{BigInt, Sign};

use crate::decimal::Decimal;
use crate::diag::{Code, Diagnostic};

type Res<T> = Result<T, Diagnostic>;

const TABLE: &str = include_str!("../../../spec/units.tsv");

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Linear,
    AbsoluteTemperature,
    /// Result-only unit (a temperature difference); never a source token.
    Difference,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Linear => "linear",
            Kind::AbsoluteTemperature => "absolute_temperature",
            Kind::Difference => "difference",
        }
    }

    fn parse(s: &str) -> Option<Kind> {
        match s {
            "linear" => Some(Kind::Linear),
            "absolute_temperature" => Some(Kind::AbsoluteTemperature),
            "difference" => Some(Kind::Difference),
            _ => None,
        }
    }
}

/// An exact fraction `n / d` of big integers, with `d > 0`. Not reduced.
#[derive(Debug, Clone)]
struct Ratio {
    n: BigInt,
    d: BigInt,
}

impl Ratio {
    fn from_decimal(x: &Decimal) -> Ratio {
        let (n, d) = x.to_ratio();
        Ratio { n, d }
    }

    /// Parse `a` or `a/b` where `a` and `b` are decimal literals.
    fn parse(s: &str) -> Result<Ratio, String> {
        let part = |t: &str| {
            Decimal::parse(t)
                .map(|x| Ratio::from_decimal(&x))
                .map_err(|diag| diag.message)
        };
        match s.split_once('/') {
            None => part(s),
            Some((a, b)) => {
                let a = part(a)?;
                let b = part(b)?;
                if b.n.sign() != Sign::Plus {
                    return Err(format!("denominator must be positive in {s:?}"));
                }
                Ok(a.div(&b))
            }
        }
    }

    fn add(&self, o: &Ratio) -> Ratio {
        Ratio { n: &self.n * &o.d + &o.n * &self.d, d: &self.d * &o.d }
    }

    fn sub(&self, o: &Ratio) -> Ratio {
        Ratio { n: &self.n * &o.d - &o.n * &self.d, d: &self.d * &o.d }
    }

    fn mul(&self, o: &Ratio) -> Ratio {
        Ratio { n: &self.n * &o.n, d: &self.d * &o.d }
    }

    /// Divide by `o`; the caller guarantees `o.n > 0`.
    fn div(&self, o: &Ratio) -> Ratio {
        Ratio { n: &self.n * &o.d, d: &self.d * &o.n }
    }
}

#[derive(Debug)]
pub struct Unit {
    pub symbol: String,
    pub name: String,
    pub category: String,
    pub dimension: String,
    /// Scale and offset exactly as written in the table.
    pub scale_text: String,
    pub offset_text: String,
    pub base_unit: String,
    pub kind: Kind,
    scale: Ratio,
    offset: Ratio,
}

pub struct Catalogue {
    units: Vec<Unit>,
    by_symbol: HashMap<String, usize>,
    /// Indexes into `units`, longest symbol first (ties keep table order).
    longest_first: Vec<usize>,
}

impl Catalogue {
    /// All units in table order.
    pub fn units(&self) -> &[Unit] {
        &self.units
    }

    /// Exact, case-sensitive lookup.
    pub fn get(&self, symbol: &str) -> Option<&Unit> {
        self.by_symbol.get(symbol).map(|&i| &self.units[i])
    }
}

/// Parse the embedded table. It is a compile-time constant checked by tests,
/// so a malformed table is a programming error and panics with a clear message.
fn load() -> Catalogue {
    let mut units: Vec<Unit> = Vec::new();
    let mut header_seen = false;
    for line in TABLE.lines() {
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if !header_seen {
            header_seen = true; // first non-comment line is the column header
            continue;
        }
        let c: Vec<&str> = line.split('\t').collect();
        assert!(c.len() == 9, "units.tsv: expected 9 columns in {line:?}");
        let kind = Kind::parse(c[7]).unwrap_or_else(|| panic!("units.tsv: bad kind {:?}", c[7]));
        let scale = Ratio::parse(c[4]).unwrap_or_else(|e| panic!("units.tsv: scale of {}: {e}", c[0]));
        let offset = Ratio::parse(c[5]).unwrap_or_else(|e| panic!("units.tsv: offset of {}: {e}", c[0]));
        assert!(scale.n.sign() == Sign::Plus, "units.tsv: scale of {} must be positive", c[0]);
        units.push(Unit {
            symbol: c[0].to_string(),
            name: c[1].to_string(),
            category: c[2].to_string(),
            dimension: c[3].to_string(),
            scale_text: c[4].to_string(),
            offset_text: c[5].to_string(),
            base_unit: c[6].to_string(),
            kind,
            scale,
            offset,
        });
    }
    let by_symbol: HashMap<String, usize> =
        units.iter().enumerate().map(|(i, u)| (u.symbol.clone(), i)).collect();
    assert_eq!(by_symbol.len(), units.len(), "units.tsv: duplicate symbols");
    let mut longest_first: Vec<usize> = (0..units.len()).collect();
    longest_first.sort_by(|&a, &b| {
        units[b].symbol.len().cmp(&units[a].symbol.len()).then(a.cmp(&b))
    });
    Catalogue { units, by_symbol, longest_first }
}

/// The catalogue, parsed once on first use.
pub fn catalogue() -> &'static Catalogue {
    static CATALOGUE: OnceLock<Catalogue> = OnceLock::new();
    CATALOGUE.get_or_init(load)
}

/// Look a symbol up, or fail with MDQ_CALC_ERROR (unknown units are never guessed).
pub fn lookup(symbol: &str) -> Res<&'static Unit> {
    catalogue()
        .get(symbol)
        .ok_or_else(|| Diagnostic::new(Code::CalcError, format!("unknown unit {symbol:?}")))
}

fn is_identifier_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// Recognise a unit token at character index `pos` of `text` (spec section 3):
/// the LONGEST catalogue symbol that matches there and is not immediately
/// followed by an identifier character. Result-only units never match.
/// `pos` counts characters (like a Python string index), not bytes.
pub fn match_at(text: &str, pos: usize) -> Option<&'static Unit> {
    let byte_pos = text.char_indices().nth(pos).map(|(b, _)| b)?;
    let rest = &text[byte_pos..];
    let cat = catalogue();
    for &i in &cat.longest_first {
        let unit = &cat.units[i];
        if unit.kind == Kind::Difference {
            continue;
        }
        if let Some(after) = rest.strip_prefix(unit.symbol.as_str()) {
            let boundary_ok = match after.chars().next() {
                Some(c) => !is_identifier_char(c),
                None => true,
            };
            if boundary_ok {
                return Some(unit);
            }
        }
    }
    None
}

/// Convert between two units of the same dimension with a single rounding.
pub fn convert_decimal(x: &Decimal, from: &Unit, to: &Unit) -> Res<Decimal> {
    if from.dimension != to.dimension {
        return Err(Diagnostic::new(
            Code::CalcError,
            format!(
                "cannot convert {} ({}) to {} ({}): incompatible dimensions",
                from.symbol, from.dimension, to.symbol, to.dimension
            ),
        ));
    }
    let base = Ratio::from_decimal(x).add(&from.offset).mul(&from.scale);
    let y = base.div(&to.scale).sub(&to.offset);
    // One exact division of two integers: a single rounding to 28 digits.
    Decimal::from_integer(y.n).div(&Decimal::from_integer(y.d))
}

/// Value of `x` expressed in the base unit of its dimension, exactly:
/// `(x + offset) * scale`.
fn base_value(x: &Decimal, u: &Unit) -> Ratio {
    Ratio::from_decimal(x).add(&u.offset).mul(&u.scale)
}

/// One exact division of two integers: a single rounding to 28 digits.
fn ratio_to_decimal(r: Ratio) -> Res<Decimal> {
    Decimal::from_integer(r.n).div(&Decimal::from_integer(r.d))
}

/// Multiply (or divide) two linear quantities exactly, rounding once.
/// The result is expressed in `to`, or in base units when `to` is `None`
/// (used for dimensionless ratios). The caller rejects a zero divisor.
pub(crate) fn combine(
    a: &Decimal,
    ua: &Unit,
    b: &Decimal,
    ub: &Unit,
    divide: bool,
    to: Option<&Unit>,
) -> Res<Decimal> {
    let ra = base_value(a, ua);
    let rb = base_value(b, ub);
    let mut r = if divide { ra.div(&rb) } else { ra.mul(&rb) };
    if let Some(u) = to {
        r = r.div(&u.scale).sub(&u.offset);
    }
    ratio_to_decimal(r)
}

/// `a - b` for two absolute temperatures, in kelvin increments (`deltaK`).
/// Exact, rounded once.
pub(crate) fn difference(a: &Decimal, ua: &Unit, b: &Decimal, ub: &Unit) -> Res<Decimal> {
    ratio_to_decimal(base_value(a, ua).sub(&base_value(b, ub)))
}

/// String-in / string-out conversion (used by the Python test hooks).
pub fn convert(value: &str, from: &str, to: &str) -> Res<String> {
    let uf = lookup(from)?;
    let ut = lookup(to)?;
    if uf.dimension != ut.dimension {
        return Err(Diagnostic::new(
            Code::CalcError,
            format!(
                "cannot convert {} ({}) to {} ({}): incompatible dimensions",
                uf.symbol, uf.dimension, ut.symbol, ut.dimension
            ),
        ));
    }
    let x = Decimal::parse(value)?;
    if from == to {
        return Ok(x.to_canonical()); // identity: canonicalised, never rounded
    }
    Ok(convert_decimal(&x, uf, ut)?.to_canonical())
}
