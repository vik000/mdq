//! Result rendering (spec 8.6 / E1.7): the display text of a calc value.
//!
//! * dimensionless: the canonical decimal (`25.2`, `0`, never an exponent, no `-0`)
//! * quantity: `<canonical decimal> <unit symbol>` (`19.312128 km`)
//! * money: `<canonical decimal> <CUR>` (`580.8 EUR`)
//!
//! No thousands separators, no locale, no rounding (the value was already
//! rounded by the evaluator). The typed value is kept separately by callers.

use crate::calc::eval::Value;
use crate::decimal::Decimal;
use crate::diag::{Code, Diagnostic};
use crate::units;

type Res<T> = Result<T, Diagnostic>;

fn err(message: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::CalcError, message)
}

pub fn render_value(v: &Value) -> String {
    match v {
        Value::Decimal(d) => d.to_canonical(),
        Value::Quantity { value, unit } => format!("{} {}", value.to_canonical(), unit.symbol),
        Value::Money { value, currency } => format!("{} {}", value.to_canonical(), currency),
    }
}

/// Render from loose parts (the shape of a `calc_eval` result), validating them.
pub fn render_parts(
    kind: &str,
    value: &str,
    unit: Option<&str>,
    currency: Option<&str>,
) -> Res<String> {
    let amount = Decimal::parse(value)?.to_canonical();
    match (kind, unit, currency) {
        ("decimal", None, None) => Ok(amount),
        ("decimal", _, _) => Err(err("a decimal result has neither a unit nor a currency")),
        ("quantity", Some(u), None) => Ok(format!("{amount} {}", units::lookup(u)?.symbol)),
        ("quantity", _, _) => Err(err("a quantity result needs a unit and no currency")),
        ("money", None, Some(c))
            if c.len() == 3 && c.chars().all(|ch| ch.is_ascii_uppercase()) =>
        {
            Ok(format!("{amount} {c}"))
        }
        ("money", None, Some(c)) => Err(err(format!("invalid currency code {c:?}"))),
        ("money", _, _) => Err(err("a money result needs a currency and no unit")),
        (other, _, _) => Err(err(format!("unknown result type {other:?}"))),
    }
}
