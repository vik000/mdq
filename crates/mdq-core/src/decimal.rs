//! Exact decimal arithmetic (spec: extended-markdown.md sections 8.5-8.7).
//!
//! Rules implemented here:
//! * literals are exact (never rounded when parsed);
//! * every arithmetic RESULT is rounded once to `PRECISION` (28) significant
//!   digits, round-half-even;
//! * division by zero is an error;
//! * the canonical text form has no exponent, no trailing fractional zeros,
//!   and `-0` prints as `0`.
//!
//! A `Decimal` is `mant * 10^(-scale)`: for example 12.50 is `mant = 1250,
//! scale = 2`, and 300 may be stored as `mant = 3, scale = -2`.
//! `BigInt` is an arbitrary-size integer (like Python's `int`).

use std::cmp::Ordering;

use num_bigint::{BigInt, Sign};
use num_traits::{Signed, Zero};

use crate::diag::{Code, Diagnostic};

/// Significant digits kept in every arithmetic result (spec: at least 28).
pub const PRECISION: u32 = 28;
/// Policy (spec gap G4): the exponent of the leading digit must stay within +-9999.
pub const MAX_ADJUSTED_EXPONENT: i64 = 9_999;
/// Policy (spec gap G4): longest accepted numeric literal, in bytes.
pub const MAX_LITERAL_LEN: usize = 10_000;
/// Largest precision `round_sig` accepts.
pub const MAX_ROUND_PRECISION: u32 = 1_000;

// A shorthand: `Res<T>` is "a T, or a Diagnostic describing what went wrong".
// Rust has no exceptions; fallible functions return this and callers use `?`
// (which means "if this is an Err, return it from the current function").
type Res<T> = Result<T, Diagnostic>;

fn calc_err(message: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::CalcError, message)
}

/// 10^n as a BigInt. `n` must be non-negative and fit in a u32.
fn pow10(n: i64) -> Res<BigInt> {
    let n = u32::try_from(n)
        .map_err(|_| Diagnostic::new(Code::ResourceLimit, "decimal exponent out of range"))?;
    Ok(BigInt::from(10u32).pow(n))
}

fn digit_count(magnitude: &BigInt) -> i64 {
    magnitude.to_string().len() as i64
}

/// Round `(mag + fraction) * 10^-scale` to `prec` significant digits, half-even.
///
/// `mag` must be non-negative. `sticky` means "there is a non-zero fraction
/// below the last digit of `mag`" (only division produces one). It decides
/// whether an exact-looking tie is really a tie.
/// Returns the rounded magnitude and its new scale.
fn round_magnitude(mag: BigInt, scale: i64, sticky: bool, prec: u32) -> Res<(BigInt, i64)> {
    let prec = prec as i64;
    let digits = digit_count(&mag);
    if digits <= prec {
        return Ok((mag, scale)); // nothing to round
    }
    let drop = digits - prec; // how many trailing digits we discard
    let divisor = pow10(drop)?;
    let mut q = &mag / &divisor; // the digits we keep
    let rem = &mag % &divisor; // the digits we discard
    let half = pow10(drop - 1)? * BigInt::from(5u32);

    let round_up = match rem.cmp(&half) {
        Ordering::Greater => true,
        Ordering::Less => false,
        // Exactly half: a real tie goes to the even neighbour, but if a hidden
        // non-zero fraction exists, the value is really above half.
        Ordering::Equal => sticky || !(&q % BigInt::from(2u32)).is_zero(),
    };

    let mut new_scale = scale - drop;
    if round_up {
        q = q + BigInt::from(1u32);
        if digit_count(&q) > prec {
            // 999..9 rounded up became 1000..0 (one digit too many): shrink.
            q = q / BigInt::from(10u32);
            new_scale -= 1;
        }
    }
    Ok((q, new_scale))
}

#[derive(Debug, Clone)]
pub struct Decimal {
    mant: BigInt,
    scale: i64,
}

impl Decimal {
    pub fn zero() -> Decimal {
        Decimal { mant: BigInt::zero(), scale: 0 }
    }

    /// Parse the spec number grammar, plus an optional leading `+` or `-`
    /// (host-supplied values may be signed; the calc lexer will not allow it
    /// in literals). Accepts `12`, `12.5`, `.5`; rejects `12.`, exponents,
    /// separators, spaces, non-ASCII digits, NaN, infinity.
    pub fn parse(s: &str) -> Res<Decimal> {
        if s.len() > MAX_LITERAL_LEN {
            return Err(Diagnostic::new(Code::ResourceLimit, "decimal literal too long"));
        }
        let bad = || calc_err(format!("invalid decimal literal {s:?}"));
        let bytes = s.as_bytes();
        let mut i = 0;
        let mut negative = false;
        if i < bytes.len() && (bytes[i] == b'+' || bytes[i] == b'-') {
            negative = bytes[i] == b'-';
            i += 1;
        }
        let int_start = i;
        while i < bytes.len() && bytes[i].is_ascii_digit() {
            i += 1;
        }
        let int_digits = &s[int_start..i];
        let mut frac_digits = "";
        if i < bytes.len() && bytes[i] == b'.' {
            i += 1;
            let frac_start = i;
            while i < bytes.len() && bytes[i].is_ascii_digit() {
                i += 1;
            }
            frac_digits = &s[frac_start..i];
            if frac_digits.is_empty() {
                return Err(bad()); // "12." is not valid
            }
        }
        if i != bytes.len() || (int_digits.is_empty() && frac_digits.is_empty()) {
            return Err(bad());
        }
        let all_digits = format!("{int_digits}{frac_digits}");
        let mut mant: BigInt = all_digits.parse().map_err(|_| bad())?;
        if negative {
            mant = -mant; // BigInt has no negative zero, so "-0" becomes 0
        }
        Decimal { mant, scale: frac_digits.len() as i64 }.checked()
    }

    /// An integer as a Decimal (used to divide two exact integers with one rounding).
    pub(crate) fn from_integer(n: BigInt) -> Decimal {
        Decimal { mant: n, scale: 0 }
    }

    /// The exact value as `numerator / denominator` (denominator > 0, not reduced).
    pub(crate) fn to_ratio(&self) -> (BigInt, BigInt) {
        if self.scale >= 0 {
            (self.mant.clone(), BigInt::from(10u32).pow(self.scale as u32))
        } else {
            (&self.mant * BigInt::from(10u32).pow((-self.scale) as u32), BigInt::from(1u32))
        }
    }

    fn is_negative(&self) -> bool {
        self.mant.sign() == Sign::Minus
    }

    /// Normalise zero and enforce the exponent range.
    fn checked(mut self) -> Res<Decimal> {
        if self.mant.is_zero() {
            self.scale = 0;
            return Ok(self);
        }
        let adjusted = digit_count(&self.mant.abs()) - 1 - self.scale;
        if adjusted > MAX_ADJUSTED_EXPONENT {
            return Err(calc_err("numeric overflow"));
        }
        if adjusted < -MAX_ADJUSTED_EXPONENT {
            return Err(calc_err("numeric underflow"));
        }
        Ok(self)
    }

    /// Round to `prec` significant digits and build the result.
    fn finish(mag: BigInt, scale: i64, negative: bool, sticky: bool, prec: u32) -> Res<Decimal> {
        let (q, s) = round_magnitude(mag, scale, sticky, prec)?;
        let mant = if negative { -q } else { q };
        Decimal { mant, scale: s }.checked()
    }

    pub fn is_zero(&self) -> bool {
        self.mant.is_zero()
    }

    pub fn neg(&self) -> Decimal {
        Decimal { mant: -self.mant.clone(), scale: self.scale }
    }

    pub fn add(&self, other: &Decimal) -> Res<Decimal> {
        // Bring both to the larger scale, add the integers exactly, round once.
        let scale = self.scale.max(other.scale);
        let a = &self.mant * pow10(scale - self.scale)?;
        let b = &other.mant * pow10(scale - other.scale)?;
        let sum = a + b;
        let negative = sum.sign() == Sign::Minus;
        Decimal::finish(sum.abs(), scale, negative, false, PRECISION)
    }

    pub fn sub(&self, other: &Decimal) -> Res<Decimal> {
        self.add(&other.neg())
    }

    pub fn mul(&self, other: &Decimal) -> Res<Decimal> {
        let product = &self.mant * &other.mant;
        let negative = product.sign() == Sign::Minus;
        Decimal::finish(product.abs(), self.scale + other.scale, negative, false, PRECISION)
    }

    pub fn div(&self, other: &Decimal) -> Res<Decimal> {
        if other.mant.is_zero() {
            return Err(calc_err("division by zero"));
        }
        if self.mant.is_zero() {
            return Ok(Decimal::zero());
        }
        let negative = self.is_negative() != other.is_negative();
        let ma = self.mant.abs();
        let mb = other.mant.abs();
        // Scale the numerator up so the integer quotient has at least
        // PRECISION + 1 digits; the remainder then tells us if the true
        // quotient has more digits ("sticky"), so we round exactly once.
        let k = (PRECISION as i64 + 1 + digit_count(&mb) - digit_count(&ma)).max(0);
        let numerator = ma * pow10(k)?;
        let quotient = &numerator / &mb;
        let remainder = &numerator % &mb;
        let sticky = !remainder.is_zero();
        Decimal::finish(quotient, self.scale - other.scale + k, negative, sticky, PRECISION)
    }

    /// Round to `prec` significant digits (half-even). For testing the
    /// rounding rule directly with small, hand-checkable precisions.
    pub fn round_sig(&self, prec: u32) -> Res<Decimal> {
        if prec == 0 || prec > MAX_ROUND_PRECISION {
            return Err(calc_err(format!("precision must be 1..={MAX_ROUND_PRECISION}")));
        }
        Decimal::finish(self.mant.abs(), self.scale, self.is_negative(), false, prec)
    }

    /// Canonical text: no exponent, no trailing fractional zeros, `-0` -> `0`.
    pub fn to_canonical(&self) -> String {
        if self.mant.is_zero() {
            return "0".to_string();
        }
        let digits = self.mant.abs().to_string();
        let sign = if self.is_negative() { "-" } else { "" };
        let body = if self.scale <= 0 {
            // value = digits followed by `-scale` zeros
            format!("{}{}", digits, "0".repeat((-self.scale) as usize))
        } else {
            let scale = self.scale as usize;
            let (int_part, frac_part) = if digits.len() > scale {
                let (i, f) = digits.split_at(digits.len() - scale);
                (i.to_string(), f.to_string())
            } else {
                ("0".to_string(), format!("{}{}", "0".repeat(scale - digits.len()), digits))
            };
            let frac = frac_part.trim_end_matches('0');
            if frac.is_empty() {
                int_part
            } else {
                format!("{int_part}.{frac}")
            }
        };
        format!("{sign}{body}")
    }
}

// ---- string-in / string-out helpers (used by the Python test hooks) ----

pub fn canonical(s: &str) -> Res<String> {
    Ok(Decimal::parse(s)?.to_canonical())
}

pub fn add_str(a: &str, b: &str) -> Res<String> {
    Ok(Decimal::parse(a)?.add(&Decimal::parse(b)?)?.to_canonical())
}

pub fn sub_str(a: &str, b: &str) -> Res<String> {
    Ok(Decimal::parse(a)?.sub(&Decimal::parse(b)?)?.to_canonical())
}

pub fn mul_str(a: &str, b: &str) -> Res<String> {
    Ok(Decimal::parse(a)?.mul(&Decimal::parse(b)?)?.to_canonical())
}

pub fn div_str(a: &str, b: &str) -> Res<String> {
    Ok(Decimal::parse(a)?.div(&Decimal::parse(b)?)?.to_canonical())
}

pub fn neg_str(a: &str) -> Res<String> {
    Ok(Decimal::parse(a)?.neg().to_canonical())
}

pub fn round_sig_str(a: &str, prec: u32) -> Res<String> {
    Ok(Decimal::parse(a)?.round_sig(prec)?.to_canonical())
}
