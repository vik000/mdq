//! Dimension algebra: products of base dimensions with integer exponents
//! (spec: calculation-units.md section 1). `L1*T-1` is a speed, `M1*L2*T-2` energy.

use crate::diag::{Code, Diagnostic};

/// Base dimensions in canonical order.
const ORDER: [&str; 7] = ["M", "L", "T", "Th", "dTh", "A", "I"];
/// Index of the temperature bases `Th` and `dTh` in `ORDER`.
const TEMPERATURE: [usize; 2] = [3, 4];

/// A dimension: (index into `ORDER`, exponent) pairs, sorted, no zero exponents.
/// The empty list is the dimensionless dimension.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dim(Vec<(usize, i32)>);

impl Dim {
    /// Parse `L1*T-1`. `""` and `"1"` are dimensionless.
    pub fn parse(s: &str) -> Result<Dim, String> {
        let mut terms: Vec<(usize, i32)> = Vec::new();
        let s = s.trim();
        if s.is_empty() || s == "1" {
            return Ok(Dim(terms));
        }
        for part in s.split('*') {
            let cut = part
                .find(|c: char| c.is_ascii_digit() || c == '-')
                .ok_or_else(|| format!("bad dimension term {part:?}"))?;
            let (name, exp) = part.split_at(cut);
            let idx = ORDER
                .iter()
                .position(|n| *n == name)
                .ok_or_else(|| format!("unknown base dimension {name:?}"))?;
            let e: i32 = exp.parse().map_err(|_| format!("bad exponent in {part:?}"))?;
            terms.push((idx, e));
        }
        Ok(Dim::normalise(terms))
    }

    fn normalise(terms: Vec<(usize, i32)>) -> Dim {
        let mut merged: Vec<(usize, i32)> = Vec::new();
        for (idx, e) in terms {
            match merged.iter_mut().find(|(i, _)| *i == idx) {
                Some(slot) => slot.1 += e,
                None => merged.push((idx, e)),
            }
        }
        merged.retain(|(_, e)| *e != 0);
        merged.sort();
        Dim(merged)
    }

    fn combine(&self, other: &Dim, sign: i32) -> Dim {
        let mut terms = self.0.clone();
        terms.extend(other.0.iter().map(|(i, e)| (*i, e * sign)));
        Dim::normalise(terms)
    }

    pub fn mul(&self, other: &Dim) -> Dim {
        self.combine(other, 1)
    }

    pub fn div(&self, other: &Dim) -> Dim {
        self.combine(other, -1)
    }

    pub fn is_zero(&self) -> bool {
        self.0.is_empty()
    }

    pub fn involves_temperature(&self) -> bool {
        self.0.iter().any(|(i, _)| TEMPERATURE.contains(i))
    }

    /// `M1*L2*T-2`, or `1` for dimensionless.
    pub fn to_canonical(&self) -> String {
        if self.0.is_empty() {
            return "1".to_string();
        }
        let parts: Vec<String> = self.0.iter().map(|(i, e)| format!("{}{}", ORDER[*i], e)).collect();
        parts.join("*")
    }
}

fn bad(msg: String) -> Diagnostic {
    Diagnostic::new(Code::CalcError, msg)
}

/// String-in / string-out hooks for the Python tests.
pub fn canonical(s: &str) -> Result<String, Diagnostic> {
    Ok(Dim::parse(s).map_err(bad)?.to_canonical())
}

pub fn combine_str(a: &str, b: &str, divide: bool) -> Result<String, Diagnostic> {
    let da = Dim::parse(a).map_err(bad)?;
    let db = Dim::parse(b).map_err(bad)?;
    let result = if divide { da.div(&db) } else { da.mul(&db) };
    Ok(result.to_canonical())
}
