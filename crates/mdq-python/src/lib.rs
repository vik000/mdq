//! PyO3 wrappers. RULE: no logic here. Convert arguments, call `mdq-core`,
//! convert the result or error. Everything testable lives in `mdq-core`.

use mdq_core::calc::eval::{self, Value};
use mdq_core::calc::lexer;
use mdq_core::calc::parser::{self, Expr, ExprKind};
use mdq_core::decimal;
use mdq_core::dim;
use mdq_core::calc::render;
use mdq_core::fx;
use mdq_core::diag::{Code, Diagnostic};
use mdq_core::units;
use mdq_core::version;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyDict;

// Python-visible exception `mdq.MdqError` (attributes are set in `to_py_err`).
pyo3::create_exception!(mdq, MdqError, PyException);

/// Turn a core `Diagnostic` into a Python `MdqError` carrying
/// `.code .path .line .column` attributes.
fn to_py_err(py: Python<'_>, d: Diagnostic) -> PyErr {
    let err = MdqError::new_err(d.message.clone());
    let value = err.value(py);
    // Setting plain attributes on a fresh exception object cannot reasonably fail.
    let _ = value.setattr("code", d.code.as_str());
    let _ = value.setattr("path", d.path);
    let _ = value.setattr("line", d.line);
    let _ = value.setattr("column", d.column);
    err
}


/// Convert a core result into a Python result (raises `MdqError` on `Err`).
fn wrap<T>(py: Python<'_>, r: Result<T, Diagnostic>) -> PyResult<T> {
    r.map_err(|d| to_py_err(py, d))
}

// ---- decimal test hooks (E1.1): strings in, canonical strings out ----

#[pyfunction]
fn dec_canonical(py: Python<'_>, s: &str) -> PyResult<String> {
    wrap(py, decimal::canonical(s))
}

#[pyfunction]
fn dec_add(py: Python<'_>, a: &str, b: &str) -> PyResult<String> {
    wrap(py, decimal::add_str(a, b))
}

#[pyfunction]
fn dec_sub(py: Python<'_>, a: &str, b: &str) -> PyResult<String> {
    wrap(py, decimal::sub_str(a, b))
}

#[pyfunction]
fn dec_mul(py: Python<'_>, a: &str, b: &str) -> PyResult<String> {
    wrap(py, decimal::mul_str(a, b))
}

#[pyfunction]
fn dec_div(py: Python<'_>, a: &str, b: &str) -> PyResult<String> {
    wrap(py, decimal::div_str(a, b))
}

#[pyfunction]
fn dec_neg(py: Python<'_>, a: &str) -> PyResult<String> {
    wrap(py, decimal::neg_str(a))
}

#[pyfunction]
fn dec_round_sig(py: Python<'_>, a: &str, prec: u32) -> PyResult<String> {
    wrap(py, decimal::round_sig_str(a, prec))
}

#[pyfunction]
fn dec_precision() -> u32 {
    decimal::PRECISION
}


// ---- unit catalogue test hooks (E1.2) ----

fn unit_dict<'py>(py: Python<'py>, u: &units::Unit) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("symbol", u.symbol.as_str())?;
    d.set_item("name", u.name.as_str())?;
    d.set_item("category", u.category.as_str())?;
    d.set_item("dimension", u.dimension.as_str())?;
    d.set_item("scale", u.scale_text.as_str())?;
    d.set_item("offset", u.offset_text.as_str())?;
    d.set_item("base_unit", u.base_unit.as_str())?;
    d.set_item("kind", u.kind.as_str())?;
    Ok(d)
}

#[pyfunction]
fn unit_table(py: Python<'_>) -> PyResult<Vec<Bound<'_, PyDict>>> {
    units::catalogue().units().iter().map(|u| unit_dict(py, u)).collect()
}

#[pyfunction]
fn unit_lookup<'py>(py: Python<'py>, symbol: &str) -> PyResult<Bound<'py, PyDict>> {
    let u = wrap(py, units::lookup(symbol))?;
    unit_dict(py, u)
}

/// Symbol of the unit token at character index `pos`, or None.
#[pyfunction]
fn unit_match_at(text: &str, pos: usize) -> Option<String> {
    units::match_at(text, pos).map(|u| u.symbol.clone())
}

#[pyfunction]
fn unit_convert(py: Python<'_>, value: &str, from_unit: &str, to_unit: &str) -> PyResult<String> {
    wrap(py, units::convert(value, from_unit, to_unit))
}


// ---- calc lexer test hook (E1.3) ----

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn calc_tokenize<'py>(py: Python<'py>, text: &str, first_line: u32) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let tokens = wrap(py, lexer::tokenize(text, first_line))?;
    tokens
        .iter()
        .map(|t| {
            let d = PyDict::new(py);
            d.set_item("kind", t.kind.as_str())?;
            d.set_item("text", t.text.as_str())?;
            d.set_item("line", t.line)?;
            d.set_item("col", t.col)?;
            Ok(d)
        })
        .collect()
}


// ---- calc parser test hook (E1.4) ----

/// Expression tree as nested dicts: kind, line, col, plus per-kind fields.
fn expr_dict<'py>(py: Python<'py>, e: &Expr) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("line", e.line)?;
    d.set_item("col", e.col)?;
    match &e.kind {
        ExprKind::Number(text) => {
            d.set_item("kind", "number")?;
            d.set_item("text", text.as_str())?;
        }
        ExprKind::Quantity { number, unit, currency } => {
            d.set_item("kind", "quantity")?;
            d.set_item("text", number.as_str())?;
            d.set_item("unit", unit.as_str())?;
            d.set_item("currency", *currency)?;
        }
        ExprKind::Ident(name) => {
            d.set_item("kind", "ident")?;
            d.set_item("name", name.as_str())?;
        }
        ExprKind::Unary { op, operand } => {
            d.set_item("kind", "unary")?;
            d.set_item("op", op.as_str())?;
            d.set_item("operand", expr_dict(py, operand)?)?;
        }
        ExprKind::Percent(operand) => {
            d.set_item("kind", "percent")?;
            d.set_item("operand", expr_dict(py, operand)?)?;
        }
        ExprKind::Binary { op, lhs, rhs } => {
            d.set_item("kind", "binary")?;
            d.set_item("op", op.as_str())?;
            d.set_item("lhs", expr_dict(py, lhs)?)?;
            d.set_item("rhs", expr_dict(py, rhs)?)?;
        }
        ExprKind::Convert { operand, unit, currency } => {
            d.set_item("kind", "convert")?;
            d.set_item("operand", expr_dict(py, operand)?)?;
            d.set_item("unit", unit.as_str())?;
            d.set_item("currency", *currency)?;
        }
    }
    Ok(d)
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn calc_parse<'py>(py: Python<'py>, text: &str, first_line: u32) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let block = wrap(py, parser::parse_source(text, first_line))?;
    block
        .iter()
        .map(|a| {
            let d = PyDict::new(py);
            d.set_item("name", a.name.as_str())?;
            d.set_item("line", a.line)?;
            d.set_item("col", a.col)?;
            d.set_item("expr", expr_dict(py, &a.expr)?)?;
            Ok(d)
        })
        .collect()
}


// ---- calc evaluator and dimension test hooks (E1.5) ----

#[pyfunction]
fn dim_canonical(py: Python<'_>, s: &str) -> PyResult<String> {
    wrap(py, dim::canonical(s))
}

#[pyfunction]
fn dim_combine(py: Python<'_>, a: &str, b: &str, divide: bool) -> PyResult<String> {
    wrap(py, dim::combine_str(a, b, divide))
}

fn obs_dict<'py>(py: Python<'py>, o: &fx::Observation) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("id", o.id.as_deref())?;
    d.set_item("from", o.from.as_str())?;
    d.set_item("to", o.to.as_str())?;
    d.set_item("rate", o.rate.to_canonical())?;
    d.set_item("observed_at", o.observed_at.as_str())?;
    d.set_item("provider", o.provider.as_str())?;
    d.set_item("market", o.market.as_deref())?;
    Ok(d)
}

/// `variables` maps names to calc literals such as "120 EUR". The FX
/// arguments describe host-supplied exchange-rate observations (E1.6).
#[pyfunction]
#[pyo3(signature = (text, variables=None, first_line=1, observations=None, valuation=None, market=None, pins=None))]
#[allow(clippy::too_many_arguments)]
fn calc_eval<'py>(
    py: Python<'py>,
    text: &str,
    variables: Option<std::collections::HashMap<String, String>>,
    first_line: u32,
    observations: Option<Vec<std::collections::HashMap<String, String>>>,
    valuation: Option<String>,
    market: Option<String>,
    pins: Option<std::collections::HashMap<String, std::collections::HashMap<String, String>>>,
) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let pairs: Vec<(String, String)> = variables.unwrap_or_default().into_iter().collect();
    let host = wrap(py, eval::parse_host_values(&pairs))?;
    let ctx = wrap(
        py,
        fx::FxContext::from_parts(
            &observations.unwrap_or_default(),
            valuation.as_deref(),
            market.as_deref(),
            &pins.unwrap_or_default(),
        ),
    )?;
    let results = wrap(py, eval::evaluate_source(text, first_line, &host, &ctx))?;
    results
        .iter()
        .map(|r| {
            let d = PyDict::new(py);
            d.set_item("name", r.name.as_str())?;
            d.set_item("line", r.line)?;
            d.set_item("col", r.col)?;
            d.set_item("type", r.value.type_name())?;
            d.set_item("value", r.value.magnitude().to_canonical())?;
            match &r.value {
                Value::Decimal(_) => {
                    d.set_item("unit", py.None())?;
                    d.set_item("currency", py.None())?;
                }
                Value::Quantity { unit, .. } => {
                    d.set_item("unit", unit.symbol.as_str())?;
                    d.set_item("currency", py.None())?;
                }
                Value::Money { currency, .. } => {
                    d.set_item("unit", py.None())?;
                    d.set_item("currency", currency.as_str())?;
                }
            }
            d.set_item("display", render::render_value(&r.value))?;
            let used = r
                .observations
                .iter()
                .map(|o| obs_dict(py, o))
                .collect::<PyResult<Vec<_>>>()?;
            d.set_item("observations", used)?;
            Ok(d)
        })
        .collect()
}

// ---- render test hook (E1.7) ----

/// `calc_render(result)`: display text of a result dict (`type`, `value`,
/// `unit`, `currency`; other keys are ignored, so a `calc_eval` result works).
#[pyfunction]
fn calc_render(py: Python<'_>, result: &Bound<'_, PyDict>) -> PyResult<String> {
    fn text(d: &Bound<'_, PyDict>, key: &str, required: bool) -> PyResult<Option<String>> {
        match d.get_item(key)? {
            Some(v) if v.is_none() => Ok(None),
            Some(v) => Ok(Some(v.extract::<String>()?)),
            None if required => Err(pyo3::exceptions::PyKeyError::new_err(key.to_string())),
            None => Ok(None),
        }
    }
    let kind = text(result, "type", true)?.unwrap_or_default();
    let value = text(result, "value", true)?.unwrap_or_default();
    let unit = text(result, "unit", false)?;
    let currency = text(result, "currency", false)?;
    wrap(py, render::render_parts(&kind, &value, unit.as_deref(), currency.as_deref()))
}

// ---- FX test hooks (E1.6) ----

#[pyfunction]
fn fx_parse_instant(py: Python<'_>, s: &str) -> PyResult<(i64, u32)> {
    let i = wrap(py, fx::Instant::parse(s))?;
    Ok((i.secs, i.nanos))
}

#[pyfunction]
#[pyo3(signature = (observations, from_currency, to_currency, valuation=None, id=None, market=None))]
fn fx_select<'py>(
    py: Python<'py>,
    observations: Vec<std::collections::HashMap<String, String>>,
    from_currency: &str,
    to_currency: &str,
    valuation: Option<&str>,
    id: Option<&str>,
    market: Option<&str>,
) -> PyResult<Bound<'py, PyDict>> {
    let obs = wrap(
        py,
        fx::select_from_parts(&observations, from_currency, to_currency, valuation, id, market),
    )?;
    obs_dict(py, &obs)
}

/// Public: `mdq.version_info()`.
#[pyfunction]
fn version_info(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("package", env!("CARGO_PKG_VERSION"))?;
    d.set_item("spec", version::SPEC_VERSION)?;
    d.set_item("syntax", version::SYNTAX_VERSION)?;
    d.set_item("manifest", version::MANIFEST_VERSION)?;
    d.set_item("unit_catalogue", version::UNIT_CATALOGUE_VERSION)?;
    Ok(d)
}

/// Internal: the stable diagnostic codes, in spec order.
#[pyfunction]
fn diagnostic_codes() -> Vec<&'static str> {
    Code::ALL.iter().map(|c| c.as_str()).collect()
}

/// Internal: profiles this build implements.
#[pyfunction]
fn supported_profiles() -> Vec<&'static str> {
    version::supported_profiles().to_vec()
}

/// Internal: raises `MdqError` (MDQ_UNSUPPORTED_FEATURE) if unsupported.
#[pyfunction]
fn check_required(py: Python<'_>, profiles: Vec<String>, version: &str) -> PyResult<()> {
    version::check_required(&profiles, version).map_err(|d| to_py_err(py, d))
}

/// Internal test hook: panics on purpose, to prove a Rust panic reaches Python
/// as a loud `PanicException` (a BaseException) instead of crashing or hiding.
#[pyfunction]
#[pyo3(name = "_panic_probe")]
fn panic_probe() {
    panic!("deliberate panic from _panic_probe");
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let py = m.py();
    m.add("MdqError", py.get_type::<MdqError>())?;
    m.add_function(wrap_pyfunction!(version_info, m)?)?;

    // `mdq._internal` re-exports everything registered here (unstable, for tests).
    let internal = PyModule::new(py, "_internal")?;
    internal.add_function(wrap_pyfunction!(diagnostic_codes, &internal)?)?;
    internal.add_function(wrap_pyfunction!(supported_profiles, &internal)?)?;
    internal.add_function(wrap_pyfunction!(check_required, &internal)?)?;
    internal.add_function(wrap_pyfunction!(panic_probe, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dec_canonical, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dec_add, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dec_sub, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dec_mul, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dec_div, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dec_neg, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dec_round_sig, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dec_precision, &internal)?)?;
    internal.add_function(wrap_pyfunction!(unit_table, &internal)?)?;
    internal.add_function(wrap_pyfunction!(unit_lookup, &internal)?)?;
    internal.add_function(wrap_pyfunction!(unit_match_at, &internal)?)?;
    internal.add_function(wrap_pyfunction!(unit_convert, &internal)?)?;
    internal.add_function(wrap_pyfunction!(calc_tokenize, &internal)?)?;
    internal.add_function(wrap_pyfunction!(calc_parse, &internal)?)?;
    internal.add_function(wrap_pyfunction!(calc_eval, &internal)?)?;
    internal.add_function(wrap_pyfunction!(fx_parse_instant, &internal)?)?;
    internal.add_function(wrap_pyfunction!(fx_select, &internal)?)?;
    internal.add_function(wrap_pyfunction!(calc_render, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dim_canonical, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dim_combine, &internal)?)?;
    m.add_submodule(&internal)?;
    Ok(())
}
