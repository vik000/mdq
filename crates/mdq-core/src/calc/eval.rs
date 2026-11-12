//! Evaluator for `calc` blocks (spec: extended-markdown.md sections 8.6-8.7,
//! calculation-units.md section 6).
//!
//! Values are a plain decimal, a quantity (decimal + catalogue unit) or money
//! (decimal + currency code). Every arithmetic step rounds once to 28
//! significant digits (half-even); unit work is exact (see `units::combine`).
//!
//! Decisions where the spec is silent (see docs/TEST_PLAN.md):
//! * G8: a percentage is the factor `n/100`, so `100 + 10%` is `100.1`;
//! * G12: unary `+`/`-` is allowed on every type (so `-5 degC` works), and
//!   negates the written value;
//! * G14: `deltaK / deltaK` is a plain ratio; `deltaK` in any other product,
//!   and absolute temperatures in any product or sum, are errors;
//! * G2: derived result units follow calculation-units.md section 6;
//!   `number / quantity` is always an error (no catalogue unit has an inverse dimension);
//! * G16: any well-formed currency code is accepted; `money to OTHER` uses
//!   the host's observations (`fx.rs`, E1.6): `amount * rate`, never inverted;
//! * adding quantities converts the right operand to the left unit first
//!   (spec 8.7), so that step and the addition each round once.

use std::cell::RefCell;
use std::collections::HashMap;

use crate::calc::parser::{self, Assignment, BinOp, Expr, ExprKind, UnOp};
use crate::decimal::Decimal;
use crate::diag::{Code, Diagnostic};
use crate::dim::Dim;
use crate::fx::{FxContext, Observation};
use crate::units::{self, Kind, Unit};

type Res<T> = Result<T, Diagnostic>;

#[derive(Debug, Clone)]
pub enum Value {
    Decimal(Decimal),
    Quantity { value: Decimal, unit: &'static Unit },
    Money { value: Decimal, currency: String },
}

impl Value {
    pub fn type_name(&self) -> &'static str {
        match self {
            Value::Decimal(_) => "decimal",
            Value::Quantity { .. } => "quantity",
            Value::Money { .. } => "money",
        }
    }

    pub fn magnitude(&self) -> &Decimal {
        match self {
            Value::Decimal(v) => v,
            Value::Quantity { value, .. } => value,
            Value::Money { value, .. } => value,
        }
    }

    /// Short description for error messages.
    fn describe(&self) -> String {
        match self {
            Value::Decimal(_) => "a plain number".to_string(),
            Value::Quantity { unit, .. } => format!("a quantity in {}", unit.symbol),
            Value::Money { currency, .. } => format!("money ({currency})"),
        }
    }
}

#[derive(Debug, Clone)]
pub struct EvalResult {
    pub name: String,
    pub line: u32,
    pub col: u32,
    pub value: Value,
    /// Observations applied by `to` conversions on THIS line (provenance).
    pub observations: Vec<Observation>,
}

fn cerr(message: impl Into<String>, line: u32, col: u32) -> Diagnostic {
    Diagnostic::new(Code::CalcError, message).at_position(line, col)
}

/// Attach a position to a diagnostic that has none yet.
fn at(d: Diagnostic, line: u32, col: u32) -> Diagnostic {
    if d.line.is_none() {
        d.at_position(line, col)
    } else {
        d
    }
}

fn dim_of(u: &Unit) -> Res<Dim> {
    Dim::parse(&u.dimension).map_err(|m| Diagnostic::new(Code::CalcError, m))
}

struct Scope<'a> {
    locals: HashMap<String, Value>,
    host: &'a HashMap<String, Value>,
    fx: &'a FxContext,
    used: RefCell<Vec<Observation>>,
    /// Every name assigned in the block, with its line (for "used before assigned").
    later: HashMap<String, u32>,
}

impl<'a> Scope<'a> {
    fn lookup(&self, name: &str, line: u32, col: u32) -> Res<Value> {
        if let Some(v) = self.locals.get(name) {
            return Ok(v.clone());
        }
        if let Some(v) = self.host.get(name) {
            return Ok(v.clone());
        }
        match self.later.get(name) {
            Some(l) => Err(cerr(format!("`{name}` is used before its assignment on line {l}"), line, col)),
            None => Err(cerr(format!("unknown name `{name}`"), line, col)),
        }
    }

    fn eval(&self, e: &Expr) -> Res<Value> {
        match &e.kind {
            ExprKind::Number(text) => Ok(Value::Decimal(number(text, e)?)),
            ExprKind::Quantity { number: text, unit, currency } => {
                let value = number(text, e)?;
                if *currency {
                    Ok(Value::Money { value, currency: unit.clone() })
                } else {
                    let unit = units::lookup(unit).map_err(|d| at(d, e.line, e.col))?;
                    Ok(Value::Quantity { value, unit })
                }
            }
            ExprKind::Ident(name) => self.lookup(name, e.line, e.col),
            ExprKind::Unary { op, operand } => {
                let v = self.eval(operand)?;
                Ok(unary(*op, v))
            }
            ExprKind::Percent(operand) => match self.eval(operand)? {
                Value::Decimal(d) => {
                    let hundred = Decimal::parse("100").map_err(|x| at(x, e.line, e.col))?;
                    Ok(Value::Decimal(d.div(&hundred).map_err(|x| at(x, e.line, e.col))?))
                }
                other => Err(cerr(
                    format!("`%` needs a dimensionless number, found {}", other.describe()),
                    e.line,
                    e.col,
                )),
            },
            ExprKind::Binary { op, lhs, rhs } => {
                let l = self.eval(lhs)?;
                let r = self.eval(rhs)?;
                match op {
                    BinOp::Add => add_sub(false, &l, &r, e.line, e.col),
                    BinOp::Sub => add_sub(true, &l, &r, e.line, e.col),
                    BinOp::Mul => mul_div(false, &l, &r, e.line, e.col),
                    BinOp::Div => mul_div(true, &l, &r, e.line, e.col),
                }
            }
            ExprKind::Convert { operand, unit, currency } => {
                let v = self.eval(operand)?;
                convert(&v, unit, *currency, e.line, e.col, self.fx, &self.used)
            }
        }
    }
}

fn number(text: &str, e: &Expr) -> Res<Decimal> {
    Decimal::parse(text).map_err(|d| at(d, e.line, e.col))
}

fn unary(op: UnOp, v: Value) -> Value {
    if op == UnOp::Plus {
        return v;
    }
    match v {
        Value::Decimal(d) => Value::Decimal(d.neg()),
        Value::Quantity { value, unit } => Value::Quantity { value: value.neg(), unit },
        Value::Money { value, currency } => Value::Money { value: value.neg(), currency },
    }
}

fn add_sub(subtract: bool, l: &Value, r: &Value, line: u32, col: u32) -> Res<Value> {
    let verb = if subtract { "subtract" } else { "add" };
    let calc = |a: &Decimal, b: &Decimal| -> Res<Decimal> {
        let res = if subtract { a.sub(b) } else { a.add(b) };
        res.map_err(|d| at(d, line, col))
    };
    match (l, r) {
        (Value::Decimal(a), Value::Decimal(b)) => Ok(Value::Decimal(calc(a, b)?)),
        (Value::Money { value: a, currency: ca }, Value::Money { value: b, currency: cb }) => {
            if ca != cb {
                return Err(cerr(
                    format!("cannot {verb} {ca} and {cb} amounts: convert one with `to` first"),
                    line,
                    col,
                ));
            }
            Ok(Value::Money { value: calc(a, b)?, currency: ca.clone() })
        }
        (Value::Quantity { value: a, unit: ua }, Value::Quantity { value: b, unit: ub }) => {
            quantity_add_sub(subtract, a, *ua, b, *ub, line, col)
        }
        _ => Err(cerr(format!("cannot {verb} {} and {}", l.describe(), r.describe()), line, col)),
    }
}

fn quantity_add_sub(
    subtract: bool,
    a: &Decimal,
    ua: &'static Unit,
    b: &Decimal,
    ub: &'static Unit,
    line: u32,
    col: u32,
) -> Res<Value> {
    let abs_a = ua.kind == Kind::AbsoluteTemperature;
    let abs_b = ub.kind == Kind::AbsoluteTemperature;
    let diff_a = ua.kind == Kind::Difference;
    let diff_b = ub.kind == Kind::Difference;
    if abs_a && abs_b {
        if !subtract {
            return Err(cerr("absolute temperatures cannot be added", line, col));
        }
        let value = units::difference(a, ua, b, ub).map_err(|d| at(d, line, col))?;
        let unit = units::lookup("deltaK").map_err(|d| at(d, line, col))?;
        return Ok(Value::Quantity { value, unit });
    }
    if (abs_a && diff_b) || (diff_a && abs_b) {
        return Err(cerr(
            "adding or subtracting a temperature difference and an absolute temperature is not supported in 0.1",
            line,
            col,
        ));
    }
    if ua.dimension != ub.dimension {
        return Err(cerr(
            format!(
                "incompatible dimensions: {} ({}) and {} ({})",
                ua.symbol, ua.dimension, ub.symbol, ub.dimension
            ),
            line,
            col,
        ));
    }
    let b_in_a = if ua.symbol == ub.symbol {
        b.clone()
    } else {
        units::convert_decimal(b, ub, ua).map_err(|d| at(d, line, col))?
    };
    let res = if subtract { a.sub(&b_in_a) } else { a.add(&b_in_a) };
    let value = res.map_err(|d| at(d, line, col))?;
    Ok(Value::Quantity { value, unit: ua })
}

fn reject_absolute_temperature(u: &Unit, line: u32, col: u32) -> Res<()> {
    if u.kind == Kind::AbsoluteTemperature {
        return Err(cerr("absolute temperatures cannot be multiplied or divided", line, col));
    }
    Ok(())
}

fn mul_div(divide: bool, l: &Value, r: &Value, line: u32, col: u32) -> Res<Value> {
    let verb = if divide { "divide" } else { "multiply" };
    let calc = |a: &Decimal, b: &Decimal| -> Res<Decimal> {
        let res = if divide { a.div(b) } else { a.mul(b) };
        res.map_err(|d| at(d, line, col))
    };
    match (l, r) {
        (Value::Decimal(a), Value::Decimal(b)) => Ok(Value::Decimal(calc(a, b)?)),
        (Value::Quantity { value: a, unit }, Value::Decimal(b)) => {
            reject_absolute_temperature(unit, line, col)?;
            Ok(Value::Quantity { value: calc(a, b)?, unit: *unit })
        }
        (Value::Decimal(a), Value::Quantity { value: b, unit }) => {
            if divide {
                return Err(cerr(
                    "cannot divide a plain number by a quantity: no catalogue unit has the inverse dimension",
                    line,
                    col,
                ));
            }
            reject_absolute_temperature(unit, line, col)?;
            Ok(Value::Quantity { value: calc(a, b)?, unit: *unit })
        }
        (Value::Quantity { value: a, unit: ua }, Value::Quantity { value: b, unit: ub }) => {
            quantity_mul_div(divide, a, *ua, b, *ub, line, col)
        }
        (Value::Money { value: a, currency }, Value::Decimal(b)) => {
            Ok(Value::Money { value: calc(a, b)?, currency: currency.clone() })
        }
        (Value::Decimal(a), Value::Money { value: b, currency }) => {
            if divide {
                return Err(cerr("cannot divide a plain number by money", line, col));
            }
            Ok(Value::Money { value: calc(a, b)?, currency: currency.clone() })
        }
        (Value::Money { value: a, currency: ca }, Value::Money { value: b, currency: cb }) => {
            if !divide {
                return Err(cerr("cannot multiply two money amounts", line, col));
            }
            if ca != cb {
                return Err(cerr(
                    format!("cannot divide {ca} by {cb}: convert one with `to` first"),
                    line,
                    col,
                ));
            }
            Ok(Value::Decimal(calc(a, b)?))
        }
        _ => Err(cerr(format!("cannot {verb} {} and {}", l.describe(), r.describe()), line, col)),
    }
}

fn quantity_mul_div(
    divide: bool,
    a: &Decimal,
    ua: &'static Unit,
    b: &Decimal,
    ub: &'static Unit,
    line: u32,
    col: u32,
) -> Res<Value> {
    reject_absolute_temperature(ua, line, col)?;
    reject_absolute_temperature(ub, line, col)?;
    let diff_a = ua.kind == Kind::Difference;
    let diff_b = ub.kind == Kind::Difference;
    if (diff_a || diff_b) && !(divide && diff_a && diff_b) {
        return Err(cerr(
            "a temperature difference can only be multiplied or divided by a plain number",
            line,
            col,
        ));
    }
    if divide && b.is_zero() {
        return Err(cerr("division by zero", line, col));
    }
    let da = dim_of(ua).map_err(|d| at(d, line, col))?;
    let db = dim_of(ub).map_err(|d| at(d, line, col))?;
    let dim = if divide { da.div(&db) } else { da.mul(&db) };
    if dim.is_zero() {
        let value = units::combine(a, ua, b, ub, divide, None).map_err(|d| at(d, line, col))?;
        return Ok(Value::Decimal(value));
    }
    let unit = derived_unit(ua, ub, divide, &dim).map_err(|d| at(d, line, col))?;
    let value = units::combine(a, ua, b, ub, divide, Some(unit)).map_err(|d| at(d, line, col))?;
    Ok(Value::Quantity { value, unit })
}

/// Result unit of a product or ratio (calculation-units.md section 6).
fn derived_unit(ua: &Unit, ub: &Unit, divide: bool, dim: &Dim) -> Res<&'static Unit> {
    let cat = units::catalogue();
    let matches = |u: &Unit| dim_of(u).map(|d| d == *dim);
    // Rule 3: u * u -> u2 / u3 ; a / b -> "a/b"
    if !divide && ua.symbol == ub.symbol {
        for suffix in ["2", "3"] {
            if let Some(u) = cat.get(&format!("{}{}", ua.symbol, suffix)) {
                if matches(u)? {
                    return Ok(u);
                }
            }
        }
    }
    if divide {
        if let Some(u) = cat.get(&format!("{}/{}", ua.symbol, ub.symbol)) {
            if matches(u)? {
                return Ok(u);
            }
        }
    }
    // Rule 4: the coherent unit of the dimension (scale 1, offset 0).
    for u in cat.units() {
        if u.kind == Kind::Linear && u.scale_text == "1" && u.offset_text == "0" && matches(u)? {
            return Ok(u);
        }
    }
    // Rule 2: not a catalogue dimension.
    Err(Diagnostic::new(
        Code::CalcError,
        format!("the result dimension {} has no catalogue unit", dim.to_canonical()),
    ))
}

fn convert(
    v: &Value,
    target: &str,
    currency: bool,
    line: u32,
    col: u32,
    fx: &FxContext,
    used: &RefCell<Vec<Observation>>,
) -> Res<Value> {
    if currency {
        return match v {
            Value::Money { value, currency: from } => {
                if from == target {
                    Ok(Value::Money { value: value.clone(), currency: from.clone() })
                } else {
                    let obs = fx.select(from, target).map_err(|d| at(d, line, col))?;
                    let converted = value.mul(&obs.rate).map_err(|d| at(d, line, col))?;
                    used.borrow_mut().push(obs.clone());
                    Ok(Value::Money { value: converted, currency: target.to_string() })
                }
            }
            other => Err(cerr(format!("cannot convert {} to {target}", other.describe()), line, col)),
        };
    }
    match v {
        Value::Quantity { value, unit: from } => {
            let to = units::lookup(target).map_err(|d| at(d, line, col))?;
            if from.symbol == to.symbol {
                return Ok(Value::Quantity { value: value.clone(), unit: to });
            }
            let converted = units::convert_decimal(value, from, to).map_err(|d| at(d, line, col))?;
            Ok(Value::Quantity { value: converted, unit: to })
        }
        Value::Decimal(_) => Err(cerr(
            format!("cannot convert a plain number to {target}: only quantities have units"),
            line,
            col,
        )),
        Value::Money { .. } => Err(cerr(format!("cannot convert money to {target}"), line, col)),
    }
}

/// Evaluate parsed assignments in order. `host` holds typed inputs that are
/// visible as simple identifiers; an earlier local assignment shadows them.
pub fn evaluate(block: &[Assignment], host: &HashMap<String, Value>, fx: &FxContext) -> Res<Vec<EvalResult>> {
    let mut later: HashMap<String, u32> = HashMap::new();
    for a in block {
        later.insert(a.name.clone(), a.line);
    }
    let mut scope = Scope { locals: HashMap::new(), host, fx, used: RefCell::new(Vec::new()), later };
    let mut out = Vec::new();
    for a in block {
        scope.used.borrow_mut().clear();
        let value = scope.eval(&a.expr)?;
        scope.locals.insert(a.name.clone(), value.clone());
        let observations = scope.used.borrow().clone();
        out.push(EvalResult { name: a.name.clone(), line: a.line, col: a.col, value, observations });
    }
    Ok(out)
}

/// Lex, parse and evaluate a whole `calc` block.
pub fn evaluate_source(
    text: &str,
    first_line: u32,
    host: &HashMap<String, Value>,
    fx: &FxContext,
) -> Res<Vec<EvalResult>> {
    let block = parser::parse_source(text, first_line)?;
    evaluate(&block, host, fx)
}

fn is_literal(e: &Expr) -> bool {
    match &e.kind {
        ExprKind::Number(_) | ExprKind::Quantity { .. } => true,
        ExprKind::Unary { operand, .. } => is_literal(operand),
        _ => false,
    }
}

/// Build host-supplied typed inputs from `(name, literal)` pairs such as
/// `("price", "120 EUR")`. Values use the calc literal syntax: a number,
/// `number unit` or `number CUR`, optionally signed.
pub fn parse_host_values(pairs: &[(String, String)]) -> Res<HashMap<String, Value>> {
    let mut sorted: Vec<&(String, String)> = pairs.iter().collect();
    sorted.sort(); // deterministic: the first bad variable (by name) is reported
    let mut out = HashMap::new();
    let empty: HashMap<String, Value> = HashMap::new();
    let no_fx = FxContext::empty();
    for (name, text) in sorted {
        let valid_name = name.chars().next().map_or(false, |c| c.is_ascii_alphabetic())
            && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            && name != "to";
        if !valid_name {
            return Err(Diagnostic::new(Code::CalcError, format!("invalid variable name {name:?}")));
        }
        let wrap = |d: Diagnostic| {
            Diagnostic::new(d.code, format!("invalid value for variable `{name}`: {}", d.message))
        };
        let block = parser::parse_source(&format!("v = {text}"), 1).map_err(wrap)?;
        if block.len() != 1 || !is_literal(&block[0].expr) {
            return Err(Diagnostic::new(
                Code::CalcError,
                format!("invalid value for variable `{name}`: expected a number, quantity or money literal"),
            ));
        }
        let scope = Scope {
            locals: HashMap::new(),
            host: &empty,
            fx: &no_fx,
            used: RefCell::new(Vec::new()),
            later: HashMap::new(),
        };
        let value = scope.eval(&block[0].expr).map_err(wrap)?;
        out.insert(name.clone(), value);
    }
    Ok(out)
}
