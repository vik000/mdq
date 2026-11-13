//! PyO3 wrappers. RULE: no logic here. Convert arguments, call `mdq-core`,
//! convert the result or error. Everything testable lives in `mdq-core`.

use mdq_core::calc::eval::{self, Value};
use mdq_core::containers;
use mdq_core::calc::lexer;
use mdq_core::calc::parser::{self, Expr, ExprKind};
use mdq_core::decimal;
use mdq_core::dim;
use mdq_core::export;
use mdq_core::calc::render;
use mdq_core::frontmatter;
use mdq_core::fx;
use mdq_core::headings;
use mdq_core::imports;
use mdq_core::links;
use mdq_core::cite;
use mdq_core::notes;
use mdq_core::refs;
use mdq_core::rich;
use mdq_core::source;
use mdq_core::meta;
use mdq_core::net;
use mdq_core::paths;
use mdq_core::policy;
use mdq_core::diag::{Code, Diagnostic};
use mdq_core::units;
use mdq_core::utf8;
use mdq_core::vars;
use mdq_core::version;
use mdq_core::yaml;
use mdq_core::inflate;
use mdq_core::zip;
use mdq_core::manifest;
use mdq_core::age;
use mdq_core::compile;
use pyo3::exceptions::{PyException, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyInt, PyList, PyString, PyTuple};

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

// ---- metadata merge test hooks (E2.3) ----

/// A layer is `(name, text)` or `(name, text, first_line)`.
fn layer_from(obj: &Bound<'_, PyAny>) -> PyResult<meta::Layer> {
    let t = obj.downcast::<PyTuple>()?;
    let n = t.len();
    if n != 2 && n != 3 {
        return Err(PyTypeError::new_err("a layer is (name, text) or (name, text, first_line)"));
    }
    let name: String = t.get_item(0)?.extract()?;
    let text: String = t.get_item(1)?.extract()?;
    let first_line: u32 = if n == 3 { t.get_item(2)?.extract()? } else { 1 };
    Ok(meta::Layer { name, text, first_line })
}

fn merged_to_py<'py>(py: Python<'py>, m: &meta::Merged) -> PyResult<Bound<'py, PyDict>> {
    let decimal_cls = py.import("decimal")?.getattr("Decimal")?;
    let d = PyDict::new(py);
    d.set_item("value", yaml_to_py(py, &m.value, &decimal_cls)?)?;
    let prov = PyDict::new(py);
    for (pointer, layer) in &m.provenance {
        prov.set_item(pointer.as_str(), layer.as_str())?;
    }
    d.set_item("provenance", prov)?;
    Ok(d)
}

/// `meta_merge(layers)`: layers lowest precedence first.
#[pyfunction]
fn meta_merge<'py>(py: Python<'py>, layers: Vec<Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyDict>> {
    let parsed = layers.iter().map(|l| layer_from(l)).collect::<PyResult<Vec<_>>>()?;
    let merged = wrap(py, meta::merge(&parsed))?;
    merged_to_py(py, &merged)
}

/// `meta_cascade(defaults, project, directories, frontmatter, sidecar, overrides)`.
#[pyfunction]
#[pyo3(signature = (defaults=None, project=None, directories=None, frontmatter=None, sidecar=None, overrides=None))]
fn meta_cascade<'py>(
    py: Python<'py>,
    defaults: Option<Bound<'py, PyAny>>,
    project: Option<Bound<'py, PyAny>>,
    directories: Option<Vec<Bound<'py, PyAny>>>,
    frontmatter: Option<Bound<'py, PyAny>>,
    sidecar: Option<Bound<'py, PyAny>>,
    overrides: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyDict>> {
    let one = |o: &Option<Bound<'py, PyAny>>| -> PyResult<Option<meta::Layer>> {
        match o {
            Some(obj) => Ok(Some(layer_from(obj)?)),
            None => Ok(None),
        }
    };
    let c = meta::Cascade {
        defaults: one(&defaults)?,
        project: one(&project)?,
        directories: directories
            .unwrap_or_default()
            .iter()
            .map(|l| layer_from(l))
            .collect::<PyResult<Vec<_>>>()?,
        frontmatter: one(&frontmatter)?,
        sidecar: one(&sidecar)?,
        overrides: one(&overrides)?,
    };
    let merged = wrap(py, meta::cascade(&c))?;
    merged_to_py(py, &merged)
}

// ---- YAML loader test hook (E2.2) ----

fn yaml_to_py<'py>(
    py: Python<'py>,
    v: &yaml::Yaml,
    decimal_cls: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    match v {
        yaml::Yaml::Null => Ok(py.None().into_bound(py)),
        yaml::Yaml::Bool(b) => Ok(PyBool::new(py, *b).to_owned().into_any()),
        yaml::Yaml::Number(n) => decimal_cls.call1((n.as_str(),)),
        yaml::Yaml::Str(s) => Ok(PyString::new(py, s).into_any()),
        yaml::Yaml::Seq(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(yaml_to_py(py, item, decimal_cls)?)?;
            }
            Ok(list.into_any())
        }
        yaml::Yaml::Map(entries) => {
            let dict = PyDict::new(py);
            for (k, item) in entries {
                dict.set_item(k.as_str(), yaml_to_py(py, item, decimal_cls)?)?;
            }
            Ok(dict.into_any())
        }
    }
}

/// `yaml_load_strict(text, mapping_only=True, first_line=1)`: null/bool/str/
/// `decimal.Decimal`/list/dict (insertion-ordered).
#[pyfunction]
#[pyo3(signature = (text, mapping_only=true, first_line=1))]
fn yaml_load_strict<'py>(
    py: Python<'py>,
    text: &str,
    mapping_only: bool,
    first_line: u32,
) -> PyResult<Bound<'py, PyAny>> {
    let v = wrap(py, yaml::load(text, mapping_only, first_line))?;
    let decimal_cls = py.import("decimal")?.getattr("Decimal")?;
    yaml_to_py(py, &v, &decimal_cls)
}

// ---- frontmatter test hook (E2.1) ----

#[pyfunction]
fn frontmatter_split<'py>(py: Python<'py>, text: &str) -> PyResult<Bound<'py, PyDict>> {
    let s = wrap(py, frontmatter::split(text))?;
    let d = PyDict::new(py);
    d.set_item("has_frontmatter", s.frontmatter.is_some())?;
    d.set_item("frontmatter", s.frontmatter)?;
    d.set_item("frontmatter_line", s.frontmatter_line)?;
    d.set_item("body", s.body)?;
    d.set_item("body_line", s.body_line)?;
    Ok(d)
}

// ---- UTF-8 decode test hook (E3.1) ----

#[pyfunction]
fn utf8_decode(py: Python<'_>, data: &[u8]) -> PyResult<String> {
    wrap(py, utf8::decode(data))
}

// ---- variables test hooks (E2.4) ----

fn py_to_yaml(py: Python<'_>, obj: &Bound<'_, PyAny>, depth: usize) -> PyResult<yaml::Yaml> {
    if depth > 64 {
        return Err(PyTypeError::new_err("variables are nested too deeply"));
    }
    if obj.is_none() {
        return Ok(yaml::Yaml::Null);
    }
    if let Ok(b) = obj.downcast::<PyBool>() {
        return Ok(yaml::Yaml::Bool(b.is_true()));
    }
    if obj.downcast::<PyString>().is_ok() {
        return Ok(yaml::Yaml::Str(obj.extract::<String>()?));
    }
    if obj.downcast::<PyInt>().is_ok() {
        return Ok(yaml::Yaml::Number(obj.str()?.extract::<String>()?));
    }
    let decimal_cls = py.import("decimal")?.getattr("Decimal")?;
    if obj.is_instance(&decimal_cls)? {
        return Ok(yaml::Yaml::Number(obj.str()?.extract::<String>()?));
    }
    if let Ok(list) = obj.downcast::<PyList>() {
        let mut items = Vec::new();
        for item in list.iter() {
            items.push(py_to_yaml(py, &item, depth + 1)?);
        }
        return Ok(yaml::Yaml::Seq(items));
    }
    if let Ok(tuple) = obj.downcast::<PyTuple>() {
        let mut items = Vec::new();
        for item in tuple.iter() {
            items.push(py_to_yaml(py, &item, depth + 1)?);
        }
        return Ok(yaml::Yaml::Seq(items));
    }
    if let Ok(dict) = obj.downcast::<PyDict>() {
        let mut entries = Vec::new();
        for (k, v) in dict.iter() {
            let key: String = k
                .extract()
                .map_err(|_| PyTypeError::new_err("variable names must be str"))?;
            entries.push((key, py_to_yaml(py, &v, depth + 1)?));
        }
        return Ok(yaml::Yaml::Map(entries));
    }
    Err(PyTypeError::new_err(
        "variables must be None, bool, int, decimal.Decimal, str, list or dict (float is not accepted)",
    ))
}

#[pyfunction]
fn var_format_scalar(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<String> {
    let y = py_to_yaml(py, value, 0)?;
    wrap(py, vars::format_scalar(&y))
}

#[pyfunction]
#[pyo3(signature = (text, at_line_start=true))]
fn md_escape_inline(py: Python<'_>, text: &str, at_line_start: bool) -> PyResult<String> {
    wrap(py, vars::escape_inline(text, at_line_start))
}

#[pyfunction]
#[pyo3(signature = (text, variables, first_line=1))]
fn interpolate(py: Python<'_>, text: &str, variables: &Bound<'_, PyAny>, first_line: u32) -> PyResult<String> {
    if !variables.is_instance_of::<PyDict>() {
        return Err(PyTypeError::new_err("variables must be a dict"));
    }
    let y = py_to_yaml(py, variables, 0)?;
    wrap(py, vars::interpolate(text, &y, first_line))
}

#[pyfunction]
fn md_regions<'py>(py: Python<'py>, text: &str) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let leaves = wrap(py, mdq_core::blocks::regions(text))?;
    let mut out = Vec::with_capacity(leaves.len());
    for leaf in leaves {
        let d = PyDict::new(py);
        d.set_item("kind", leaf.kind.as_str())?;
        d.set_item("start_line", leaf.first_line)?;
        d.set_item("end_line", leaf.end_line)?;
        let mut segs = Vec::with_capacity(leaf.segments.len());
        for (a, b) in leaf.segments {
            segs.push(PyTuple::new(py, [a, b])?);
        }
        d.set_item("segments", segs)?;
        out.push(d);
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (text, variables, first_line=1, max_iterations=10000))]
fn container_eval(
    py: Python<'_>,
    text: &str,
    variables: &Bound<'_, PyAny>,
    first_line: u32,
    max_iterations: usize,
) -> PyResult<String> {
    if !variables.is_instance_of::<PyDict>() {
        return Err(PyTypeError::new_err("variables must be a dict"));
    }
    let y = py_to_yaml(py, variables, 0)?;
    wrap(py, containers::container_eval(text, &y, first_line, max_iterations))
}

#[pyfunction]
fn path_normalise(py: Python<'_>, base: &str, rel: &str) -> PyResult<String> {
    wrap(py, paths::path_normalise(base, rel))
}

#[pyfunction]
fn path_identity(path: &str) -> String {
    paths::path_identity(path)
}

#[pyfunction]
fn path_confine(py: Python<'_>, root: &str, rel: &str) -> PyResult<String> {
    wrap(py, paths::path_confine(root, rel))
}

#[pyfunction]
#[pyo3(signature = (inline, atx=false))]
fn heading_plain_text(py: Python<'_>, inline: &str, atx: bool) -> PyResult<String> {
    wrap(py, headings::heading_plain_text(inline, atx))
}

#[pyfunction]
#[pyo3(signature = (headings, target, atx=false))]
fn heading_match(py: Python<'_>, headings: Vec<String>, target: &str, atx: bool) -> PyResult<Option<usize>> {
    wrap(py, headings::heading_match(&headings, target, atx))
}

#[pyfunction]
fn slugify(text: &str) -> String {
    headings::slugify(text)
}

#[pyfunction]
#[pyo3(signature = (slugs, reserved=None))]
fn slug_dedupe(slugs: Vec<String>, reserved: Option<Vec<String>>) -> Vec<String> {
    headings::slug_dedupe(&slugs, &reserved.unwrap_or_default())
}

#[pyfunction]
fn percent_encode_anchor(text: &str) -> String {
    headings::percent_encode_anchor(text)
}

fn heading_dict<'py>(py: Python<'py>, h: &refs::Heading) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("ordinal", h.ordinal)?;
    d.set_item("level", h.level)?;
    d.set_item("text", &h.text)?;
    d.set_item("id", h.id.as_deref())?;
    d.set_item("start_line", h.start_line)?;
    d.set_item("end_line", h.end_line)?;
    d.set_item("section_end", h.section_end)?;
    Ok(d)
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn doc_index<'py>(py: Python<'py>, text: &str, first_line: u32) -> PyResult<Bound<'py, PyDict>> {
    let (headings, blocks) = wrap(py, refs::doc_index(text, first_line))?;
    let hs = PyList::empty(py);
    for h in &headings {
        hs.append(heading_dict(py, h)?)?;
    }
    let bs = PyList::empty(py);
    for b in &blocks {
        let d = PyDict::new(py);
        d.set_item("id", &b.id)?;
        d.set_item("kind", b.kind)?;
        d.set_item("start_line", b.start_line)?;
        d.set_item("end_line", b.end_line)?;
        bs.append(d)?;
    }
    let out = PyDict::new(py);
    out.set_item("headings", hs)?;
    out.set_item("blocks", bs)?;
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (text, fragment, first_line=1))]
fn resolve_ref<'py>(py: Python<'py>, text: &str, fragment: &str, first_line: u32) -> PyResult<Bound<'py, PyDict>> {
    let r = wrap(py, refs::resolve_ref(text, fragment, first_line))?;
    let d = PyDict::new(py);
    d.set_item("kind", r.kind)?;
    d.set_item("start_line", r.start_line)?;
    d.set_item("end_line", r.end_line)?;
    d.set_item("id", r.id.as_deref())?;
    if let Some(h) = &r.heading {
        d.set_item("ordinal", h.ordinal)?;
        d.set_item("level", h.level)?;
        d.set_item("text", &h.text)?;
        d.set_item("section_end", h.section_end)?;
    }
    Ok(d)
}

#[pyfunction]
#[pyo3(signature = (text, path, read, max_depth=64, max_expansions=10000, max_bytes=16777216, rebase=true, footnotes=true))]
fn import_expand<'py>(
    py: Python<'py>,
    text: &str,
    path: &str,
    read: &Bound<'py, PyAny>,
    max_depth: usize,
    max_expansions: usize,
    max_bytes: usize,
    rebase: bool,
    footnotes: bool,
) -> PyResult<Bound<'py, PyDict>> {
    if !read.is_callable() {
        return Err(PyTypeError::new_err("read must be callable"));
    }
    let failure: std::cell::RefCell<Option<PyErr>> = std::cell::RefCell::new(None);
    let mut reader = |p: &str| -> Result<Option<String>, Diagnostic> {
        let sentinel = || Diagnostic::new(mdq_core::diag::Code::SourceFetchFailed, "read callback failed");
        match read.call1((p,)) {
            Err(e) => {
                *failure.borrow_mut() = Some(e);
                Err(sentinel())
            }
            Ok(v) if v.is_none() => Ok(None),
            Ok(v) => match v.extract::<String>() {
                Ok(s) => Ok(Some(s)),
                Err(_) => {
                    *failure.borrow_mut() = Some(PyTypeError::new_err("read() must return str or None"));
                    Err(sentinel())
                }
            },
        }
    };
    let limits = imports::Limits { max_depth, max_expansions, max_bytes, rebase_links: rebase, footnotes };
    let res = imports::import_expand(text, path, &mut reader, limits);
    if let Some(e) = failure.borrow_mut().take() {
        return Err(e);
    }
    let r = wrap(py, res)?;
    let map = PyList::empty(py);
    for m in &r.map {
        let d = PyDict::new(py);
        d.set_item("out_start", m.out_start)?;
        d.set_item("out_end", m.out_end)?;
        d.set_item("path", &m.path)?;
        d.set_item("src_start", m.src_start)?;
        map.append(d)?;
    }
    let out = PyDict::new(py);
    out.set_item("text", r.text)?;
    out.set_item("map", map)?;
    Ok(out)
}

#[pyfunction]
fn rebase_link(py: Python<'_>, dest: &str, from_dir: &str, to_dir: &str) -> PyResult<String> {
    wrap(py, links::rebase_link(dest, from_dir, to_dir))
}

#[pyfunction]
#[pyo3(signature = (text, from_dir, to_dir, first_line=1))]
fn rebase_links(py: Python<'_>, text: &str, from_dir: &str, to_dir: &str, first_line: u32) -> PyResult<String> {
    wrap(py, links::rebase_links(text, from_dir, to_dir, first_line))
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn highlight_fallback(py: Python<'_>, text: &str, first_line: u32) -> PyResult<String> {
    wrap(py, rich::highlight_fallback(text, first_line))
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn callout_fallback(py: Python<'_>, text: &str, first_line: u32) -> PyResult<String> {
    wrap(py, rich::callout_fallback(text, first_line))
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn details_fallback(py: Python<'_>, text: &str, first_line: u32) -> PyResult<String> {
    wrap(py, rich::details_fallback(text, first_line))
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn columns_fallback(py: Python<'_>, text: &str, first_line: u32) -> PyResult<String> {
    wrap(py, rich::columns_fallback(text, first_line))
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn math_fallback(py: Python<'_>, text: &str, first_line: u32) -> PyResult<String> {
    wrap(py, notes::math_fallback(text, first_line))
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn footnote_check(py: Python<'_>, text: &str, first_line: u32) -> PyResult<Vec<String>> {
    wrap(py, notes::footnote_check(text, first_line))
}

#[pyfunction]
fn footnote_rename(py: Python<'_>, text: &str, scope: &str) -> PyResult<String> {
    wrap(py, notes::footnote_rename(text, scope))
}

fn parse_format(s: &str) -> PyResult<cite::Format> {
    cite::Format::parse(s).ok_or_else(|| PyValueError::new_err("format must be 'bibtex' or 'csl-json'"))
}

#[pyfunction]
fn bib_parse<'py>(py: Python<'py>, text: &str, format: &str) -> PyResult<Bound<'py, PyList>> {
    let f = parse_format(format)?;
    let entries = wrap(py, cite::bib_parse(text, f))?;
    let list = PyList::empty(py);
    for e in &entries {
        let d = PyDict::new(py);
        d.set_item("key", &e.key)?;
        d.set_item("type", &e.typ)?;
        let authors = PyList::empty(py);
        for a in &e.authors {
            let n = PyDict::new(py);
            n.set_item("family", &a.family)?;
            n.set_item("given", &a.given)?;
            authors.append(n)?;
        }
        d.set_item("authors", authors)?;
        d.set_item("et_al", e.et_al)?;
        d.set_item("year", &e.year)?;
        d.set_item("title", &e.title)?;
        d.set_item("container", &e.container)?;
        list.append(d)?;
    }
    Ok(list)
}

#[pyfunction]
#[pyo3(signature = (text, bibs, first_line=1))]
fn citation_resolve(py: Python<'_>, text: &str, bibs: Vec<(String, String)>, first_line: u32) -> PyResult<String> {
    let mut v = Vec::new();
    for (f, t) in bibs {
        v.push((parse_format(&f)?, t));
    }
    wrap(py, cite::citation_resolve(text, &v, first_line))
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn toc_build(py: Python<'_>, text: &str, first_line: u32) -> PyResult<String> {
    wrap(py, rich::toc_build(text, first_line))
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn generic_fallback(py: Python<'_>, text: &str, first_line: u32) -> PyResult<String> {
    wrap(py, rich::generic_fallback(text, first_line))
}

#[pyfunction]
#[pyo3(signature = (text, profile="commonmark", bibs=None, first_line=1))]
fn export_markdown(
    py: Python<'_>,
    text: &str,
    profile: &str,
    bibs: Option<Vec<(String, String)>>,
    first_line: u32,
) -> PyResult<String> {
    let profile = export::Profile::parse(profile)
        .ok_or_else(|| PyValueError::new_err("profile must be 'commonmark' or 'gfm'"))?;
    let mut v = Vec::new();
    for (f, t) in bibs.unwrap_or_default() {
        v.push((parse_format(&f)?, t));
    }
    wrap(py, export::export_markdown(text, profile, &v, first_line))
}

fn source_dict<'py>(py: Python<'py>, s: source::Source) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("kind", s.kind)?;
    d.set_item("reader", s.reader)?;
    d.set_item("url", s.url)?;
    d.set_item("method", s.method)?;
    d.set_item("format", s.format)?;
    d.set_item("select", s.select)?;
    d.set_item("arguments", attrs_dict(py, s.arguments)?)?;
    d.set_item("named", s.named)?;
    Ok(d)
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn source_validate<'py>(py: Python<'py>, text: &str, first_line: u32) -> PyResult<Bound<'py, PyList>> {
    let decls = wrap(py, source::source_validate(text, first_line))?;
    let list = PyList::empty(py);
    for v in decls {
        let d = PyDict::new(py);
        d.set_item("name", v.name)?;
        d.set_item("type", v.vtype)?;
        d.set_item("unit", v.unit)?;
        match v.source {
            Some(s) => d.set_item("source", source_dict(py, s)?)?,
            None => d.set_item("source", py.None())?,
        }
        d.set_item("url", v.url)?;
        list.append(d)?;
    }
    Ok(list)
}

#[pyfunction]
fn url_template_expand(py: Python<'_>, template: &str, arguments: &Bound<'_, PyDict>) -> PyResult<String> {
    let mut args: Vec<(String, String)> = Vec::new();
    for (k, v) in arguments.iter() {
        let key = k
            .downcast::<PyString>()
            .map_err(|_| PyTypeError::new_err("argument names must be str"))?
            .to_string();
        let val = if v.downcast::<PyBool>().is_ok() {
            return Err(PyTypeError::new_err("argument values must be str or int"));
        } else if let Ok(s) = v.downcast::<PyString>() {
            s.to_string()
        } else if v.downcast::<PyInt>().is_ok() {
            v.str()?.to_string()
        } else {
            return Err(PyTypeError::new_err("argument values must be str or int"));
        };
        args.push((key, val));
    }
    wrap(py, source::url_template_expand(template, &args))
}

fn json_to_py<'py>(py: Python<'py>, v: &mdq_core::json::Json, dec: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    use mdq_core::json::Json;
    match v {
        Json::Null => Ok(py.None().into_bound(py)),
        Json::Bool(b) => Ok(PyBool::new(py, *b).to_owned().into_any()),
        Json::Num(n) => dec.call1((n.as_str(),)),
        Json::Str(s) => Ok(PyString::new(py, s).into_any()),
        Json::Arr(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(json_to_py(py, item, dec)?)?;
            }
            Ok(list.into_any())
        }
        Json::Obj(entries) => {
            let d = PyDict::new(py);
            for (k, item) in entries {
                d.set_item(k.as_str(), json_to_py(py, item, dec)?)?;
            }
            Ok(d.into_any())
        }
    }
}

#[pyfunction]
fn json_pointer_select<'py>(py: Python<'py>, text: &str, pointer: &str) -> PyResult<Bound<'py, PyAny>> {
    let v = wrap(py, source::json_pointer_select(text, pointer))?;
    let dec = py.import("decimal")?.getattr("Decimal")?;
    json_to_py(py, &v, &dec)
}

#[pyfunction]
#[pyo3(signature = (value, r#type, unit=None, from_text=false))]
fn value_type_check<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    r#type: &str,
    unit: Option<&str>,
    from_text: bool,
) -> PyResult<Bound<'py, PyDict>> {
    use mdq_core::json::Json;
    if !matches!(r#type, "number" | "string" | "boolean" | "date") {
        return Err(PyValueError::new_err("type must be 'number', 'string', 'boolean' or 'date'"));
    }
    let dec = py.import("decimal")?.getattr("Decimal")?;
    let j = if value.is_none() {
        Json::Null
    } else if value.downcast::<PyBool>().is_ok() {
        Json::Bool(value.is_truthy()?)
    } else if value.downcast::<PyInt>().is_ok() || value.is_instance(&dec)? {
        Json::Num(value.str()?.to_string())
    } else if let Ok(s) = value.downcast::<PyString>() {
        Json::Str(s.to_string())
    } else if value.downcast::<PyList>().is_ok() {
        Json::Arr(Vec::new())
    } else if value.downcast::<PyDict>().is_ok() {
        Json::Obj(Vec::new())
    } else if value.downcast::<pyo3::types::PyFloat>().is_ok() {
        return Err(PyTypeError::new_err("float is not accepted; use decimal.Decimal"));
    } else {
        return Err(PyTypeError::new_err("unsupported value type"));
    };
    if from_text && !matches!(j, Json::Str(_)) {
        return Err(PyTypeError::new_err("value must be str when from_text is true"));
    }
    let c = wrap(py, source::value_type_check(&j, r#type, unit, from_text))?;
    let d = PyDict::new(py);
    d.set_item("type", c.vtype)?;
    match c.value {
        source::Typed::Num(n) => d.set_item("value", dec.call1((n.as_str(),))?)?,
        source::Typed::Str(s) | source::Typed::Date(s) => d.set_item("value", s)?,
        source::Typed::Bool(b) => d.set_item("value", b)?,
    }
    d.set_item("unit", c.unit)?;
    Ok(d)
}

#[pyfunction]
fn ip_is_blocked(address: &str) -> PyResult<bool> {
    match net::parse_ip(address) {
        Some(ip) => Ok(net::ip_is_blocked(&ip)),
        None => Err(PyValueError::new_err("not an IP address literal")),
    }
}

#[pyfunction]
fn url_classify<'py>(py: Python<'py>, url: &str) -> PyResult<Bound<'py, PyDict>> {
    let c = wrap(py, net::url_classify(url))?;
    let d = PyDict::new(py);
    d.set_item("scheme", "https")?;
    d.set_item("host", c.host)?;
    d.set_item("kind", c.kind)?;
    d.set_item("port", c.port)?;
    d.set_item("origin", c.origin)?;
    Ok(d)
}

fn to_pval(v: &Bound<'_, PyAny>) -> PyResult<policy::PVal> {
    use policy::PVal;
    if v.is_none() {
        return Ok(PVal::None);
    }
    if v.downcast::<PyBool>().is_ok() {
        return Ok(PVal::Bool(v.is_truthy()?));
    }
    if v.downcast::<PyInt>().is_ok() {
        return Ok(PVal::Int(match v.extract::<i64>() {
            Ok(n) => n,
            Err(_) => {
                if v.lt(0)? {
                    i64::MIN
                } else {
                    i64::MAX
                }
            }
        }));
    }
    if let Ok(s) = v.downcast::<PyString>() {
        return Ok(PVal::Str(s.to_string()));
    }
    if let Ok(l) = v.downcast::<PyList>() {
        return Ok(PVal::List(l.iter().map(|x| to_pval(&x)).collect::<PyResult<_>>()?));
    }
    if let Ok(t) = v.downcast::<PyTuple>() {
        return Ok(PVal::List(t.iter().map(|x| to_pval(&x)).collect::<PyResult<_>>()?));
    }
    if let Ok(d) = v.downcast::<PyDict>() {
        return Ok(PVal::Map(to_map(d)?));
    }
    Ok(PVal::Other)
}

fn to_map(d: &Bound<'_, PyDict>) -> PyResult<Vec<(String, policy::PVal)>> {
    let mut out = Vec::new();
    for (k, v) in d.iter() {
        let key = k
            .downcast::<PyString>()
            .map_err(|_| PyTypeError::new_err("policy keys must be str"))?
            .to_string();
        out.push((key, to_pval(&v)?));
    }
    Ok(out)
}

fn layer_arg(v: Option<&Bound<'_, PyAny>>, what: &str) -> PyResult<Option<Vec<(String, policy::PVal)>>> {
    match v {
        None => Ok(None),
        Some(x) if x.is_none() => Ok(None),
        Some(x) => match x.downcast::<PyDict>() {
            Ok(d) => Ok(Some(to_map(d)?)),
            Err(_) => Err(PyTypeError::new_err(format!("{what} must be a dict"))),
        },
    }
}

fn limits_dict<'py>(py: Python<'py>, l: &[u64; 4]) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("max_bytes", l[0])?;
    d.set_item("timeout", l[1])?;
    d.set_item("max_redirects", l[2])?;
    d.set_item("max_concurrency", l[3])?;
    Ok(d)
}

#[pyfunction]
#[pyo3(signature = (host=None, library=None, document=None))]
fn policy_intersect<'py>(
    py: Python<'py>,
    host: Option<&Bound<'py, PyAny>>,
    library: Option<&Bound<'py, PyAny>>,
    document: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyDict>> {
    let h = layer_arg(host, "a policy layer")?;
    let l = layer_arg(library, "a policy layer")?;
    let d = layer_arg(document, "the document request")?;
    let e = wrap(py, policy::policy_intersect(h.as_deref(), l.as_deref(), d.as_deref()))?;
    let out = PyDict::new(py);
    out.set_item("network", if e.network_allow { "allow" } else { "deny" })?;
    out.set_item("hosts", e.hosts)?;
    out.set_item("ports", e.ports)?;
    out.set_item("private_endpoints", e.private_endpoints)?;
    for (k, v) in [("max_bytes", e.limits[0]), ("timeout", e.limits[1]), ("max_redirects", e.limits[2]), ("max_concurrency", e.limits[3])] {
        out.set_item(k, v)?;
    }
    out.set_item("network_required", e.network_required)?;
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (policy, url, redirect_count=0))]
fn policy_decide<'py>(
    py: Python<'py>,
    policy: &Bound<'py, PyDict>,
    url: &str,
    redirect_count: u64,
) -> PyResult<Bound<'py, PyDict>> {
    let p = to_map(policy)?;
    let r = wrap(py, policy::policy_decide(&p, url, redirect_count))?;
    let d = PyDict::new(py);
    d.set_item("allow", true)?;
    d.set_item("scheme", "https")?;
    d.set_item("host", r.host)?;
    d.set_item("kind", r.kind)?;
    d.set_item("port", r.port)?;
    d.set_item("origin", r.origin)?;
    d.set_item("private", r.private)?;
    d.set_item("limits", limits_dict(py, &r.limits)?)?;
    Ok(d)
}

#[pyfunction]
fn policy_ip_allowed(py: Python<'_>, policy: &Bound<'_, PyDict>, host: &str, address: &str) -> PyResult<bool> {
    let p = to_map(policy)?;
    match wrap(py, policy::policy_ip_allowed(&p, host, address))? {
        Some(b) => Ok(b),
        None => Err(PyValueError::new_err("not an IP address literal")),
    }
}

// ---- E6.5 resolver hooks (G46) ----

use mdq_core::resolver::{self, GetRequest, Response, TransportError};

/// Adapter: a Python callable `transport(request: dict) -> dict` seen from Rust as a `resolver::Transport`.
/// It only converts types; every check of the answer happens in the core.
/// `fatal` keeps an error that must reach Python unchanged (KeyboardInterrupt, a panic, ...).
struct PyTransport<'py> {
    f: Bound<'py, PyAny>,
    fatal: Option<PyErr>,
}

impl<'py> PyTransport<'py> {
    fn call(&mut self, req: Bound<'py, PyDict>) -> Result<Bound<'py, PyAny>, TransportError> {
        let py = self.f.py();
        match self.f.call1((req,)) {
            Ok(v) => Ok(v),
            Err(e) if e.is_instance_of::<pyo3::exceptions::PyTimeoutError>(py) => Err(TransportError::Timeout),
            // Only the class name crosses over: the exception text may contain URLs or secrets.
            Err(e) if e.is_instance_of::<PyException>(py) => {
                let name = e.get_type(py).name().map(|n| n.to_string()).unwrap_or_default();
                Err(TransportError::Failed(name))
            }
            Err(e) => {
                self.fatal = Some(e); // BaseException but not Exception: re-raised by the wrapper
                Err(TransportError::Abort)
            }
        }
    }
}

/// A dict of str -> str, or `None` if anything has another type.
fn str_pairs(v: &Bound<'_, PyAny>) -> Option<Vec<(String, String)>> {
    let d = v.downcast::<PyDict>().ok()?;
    let mut out = Vec::new();
    for (k, x) in d.iter() {
        out.push((k.downcast::<PyString>().ok()?.to_string(), x.downcast::<PyString>().ok()?.to_string()));
    }
    Some(out)
}

/// An `int` (not `bool`) that fits in i64, else `None`.
fn plain_int(v: &Bound<'_, PyAny>) -> Option<i64> {
    if v.downcast::<PyBool>().is_ok() || v.downcast::<PyInt>().is_err() {
        return None;
    }
    v.extract::<i64>().ok()
}

fn to_response(v: &Bound<'_, PyAny>) -> Option<Response> {
    let d = v.downcast::<PyDict>().ok()?;
    // `get_item` returns PyResult<Option<..>>: `.ok()?` drops errors, the second `?` a missing key.
    let status = plain_int(&d.get_item("status").ok()??)?;
    let headers = str_pairs(&d.get_item("headers").ok()??)?;
    let body = d.get_item("body").ok()??.downcast::<pyo3::types::PyBytes>().ok()?.as_bytes().to_vec();
    let peer = d.get_item("peer").ok()??.downcast::<PyString>().ok()?.to_string();
    let elapsed_ms = match d.get_item("elapsed_ms").ok()? {
        None => 0,
        Some(x) => plain_int(&x)?,
    };
    Some(Response { status, headers, body, peer, elapsed_ms })
}

impl<'py> resolver::Transport for PyTransport<'py> {
    fn resolve(&mut self, host: &str) -> Result<Vec<String>, TransportError> {
        let py = self.f.py();
        let req = PyDict::new(py);
        req.set_item("op", "resolve").map_err(|_| TransportError::Malformed)?;
        req.set_item("host", host).map_err(|_| TransportError::Malformed)?;
        let v = self.call(req)?;
        let parse = || -> Option<Vec<String>> {
            let d = v.downcast::<PyDict>().ok()?;
            let list = d.get_item("addresses").ok()??;
            let list = list.downcast::<PyList>().ok()?;
            list.iter().map(|a| a.downcast::<PyString>().ok().map(|s| s.to_string())).collect()
        };
        parse().ok_or(TransportError::Malformed)
    }

    fn get(&mut self, r: &GetRequest) -> Result<Response, TransportError> {
        let py = self.f.py();
        let build = || -> PyResult<Bound<'py, PyDict>> {
            let req = PyDict::new(py);
            req.set_item("op", "get")?;
            req.set_item("url", &r.url)?;
            req.set_item("address", &r.address)?;
            let h = PyDict::new(py);
            for (k, v) in &r.headers {
                h.set_item(k, v)?;
            }
            req.set_item("headers", h)?;
            req.set_item("timeout_ms", r.timeout_ms)?;
            req.set_item("max_bytes", r.max_bytes)?;
            Ok(req)
        };
        let req = build().map_err(|_| TransportError::Malformed)?;
        let v = self.call(req)?;
        to_response(&v).ok_or(TransportError::Malformed)
    }
}

/// `{name, type, value, unit, retrieved_at, source: {kind, named, origin, key}}` (with or without `name`).
fn fetched_dict<'py>(py: Python<'py>, f: resolver::Fetched, with_name: bool) -> PyResult<Bound<'py, PyDict>> {
    let dec = py.import("decimal")?.getattr("Decimal")?;
    let d = PyDict::new(py);
    if with_name {
        d.set_item("name", f.name)?;
    }
    d.set_item("type", f.checked.vtype)?;
    match f.checked.value {
        source::Typed::Num(n) => d.set_item("value", dec.call1((n.as_str(),))?)?,
        source::Typed::Str(s) | source::Typed::Date(s) => d.set_item("value", s)?,
        source::Typed::Bool(b) => d.set_item("value", b)?,
    }
    d.set_item("unit", f.checked.unit)?;
    d.set_item("retrieved_at", f.retrieved_at)?;
    let s = PyDict::new(py);
    s.set_item("kind", f.source.kind)?;
    s.set_item("named", f.source.named)?;
    s.set_item("origin", f.source.origin)?;
    s.set_item("key", f.source.key)?;
    d.set_item("source", s)?;
    Ok(d)
}

#[pyfunction]
#[pyo3(signature = (text, name, policy, transport, retrieved_at, allow_network=true, headers=None))]
#[allow(clippy::too_many_arguments)]
fn source_fetch<'py>(
    py: Python<'py>,
    text: &str,
    name: &str,
    policy: &Bound<'py, PyDict>,
    transport: &Bound<'py, PyAny>,
    retrieved_at: &str,
    allow_network: bool,
    headers: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyDict>> {
    if !transport.is_callable() {
        return Err(PyTypeError::new_err("transport must be callable"));
    }
    let hdrs = match headers {
        None => Vec::new(),
        Some(h) if h.is_none() => Vec::new(),
        Some(h) => str_pairs(h).ok_or_else(|| PyTypeError::new_err("headers must be a dict of str to str"))?,
    };
    let p = to_map(policy)?;
    let mut t = PyTransport { f: transport.clone(), fatal: None };
    let r = resolver::source_fetch(text, name, &p, &mut t, retrieved_at, allow_network, &hdrs);
    if let Some(e) = t.fatal.take() {
        return Err(e);
    }
    match r {
        Ok(f) => fetched_dict(py, f, true),
        Err(resolver::FetchError::Arg(msg)) => Err(PyValueError::new_err(msg)),
        Err(resolver::FetchError::Diag(d)) => Err(to_py_err(py, d)),
    }
}

/// Python value -> `SVal`, without interpretation (the core decides what is acceptable).
fn to_sval(v: &Bound<'_, PyAny>, dec: &Bound<'_, PyAny>) -> PyResult<resolver::SVal> {
    use resolver::SVal;
    Ok(if v.is_none() {
        SVal::Null
    } else if v.downcast::<PyBool>().is_ok() {
        SVal::Bool(v.is_truthy()?)
    } else if v.downcast::<PyInt>().is_ok() || v.is_instance(dec)? {
        SVal::Num(v.str()?.to_string())
    } else if v.downcast::<pyo3::types::PyFloat>().is_ok() {
        SVal::Float
    } else if let Ok(s) = v.downcast::<PyString>() {
        SVal::Str(s.to_string())
    } else if v.downcast::<PyList>().is_ok() || v.downcast::<PyTuple>().is_ok() {
        SVal::List
    } else if let Ok(d) = v.downcast::<PyDict>() {
        let mut m = Vec::new();
        for (k, x) in d.iter() {
            // A non-str key becomes "" which the core rejects as an unknown key.
            let key = k.downcast::<PyString>().map(|s| s.to_string()).unwrap_or_default();
            m.push((key, to_sval(&x, dec)?));
        }
        SVal::Map(m)
    } else {
        SVal::Other
    })
}

#[pyfunction]
fn snapshot_apply<'py>(py: Python<'py>, text: &str, snapshot: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyDict>> {
    if snapshot.downcast::<PyList>().is_err() && snapshot.downcast::<PyTuple>().is_err() {
        return Err(PyTypeError::new_err("snapshot must be a list of entries"));
    }
    let dec = py.import("decimal")?.getattr("Decimal")?;
    let mut entries = Vec::new();
    for e in snapshot.try_iter()? {
        entries.push(to_sval(&e?, &dec)?);
    }
    let out = PyDict::new(py);
    for f in wrap(py, resolver::snapshot_apply(text, &entries))? {
        let name = f.name.clone();
        out.set_item(name, fetched_dict(py, f, false)?)?;
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(name = "url_join", signature = (base, reference))]
fn url_join_hook(base: &str, reference: &str) -> PyResult<String> {
    net::url_join(base, reference).ok_or_else(|| PyValueError::new_err("base must be an absolute http(s) URL"))
}

#[pyfunction]
fn sha256_hex(data: &[u8]) -> String {
    resolver::sha256_hex(data)
}

fn attrs_dict<'py>(py: Python<'py>, attrs: Vec<(String, String)>) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    for (k, v) in attrs {
        d.set_item(k, v)?;
    }
    Ok(d)
}

#[pyfunction]
#[pyo3(signature = (text, line=1, column=1))]
fn attrs_parse<'py>(py: Python<'py>, text: &str, line: u32, column: u32) -> PyResult<Bound<'py, PyDict>> {
    let attrs = wrap(py, containers::attrs_parse(text, line, column))?;
    attrs_dict(py, attrs)
}

fn container_dict<'py>(py: Python<'py>, n: containers::Container) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", n.name)?;
    d.set_item("attrs", attrs_dict(py, n.attrs)?)?;
    d.set_item("colons", n.colons)?;
    d.set_item("line", n.line)?;
    d.set_item("close_line", n.close_line)?;
    d.set_item("start", n.start)?;
    d.set_item("body_start", n.body_start)?;
    d.set_item("body_end", n.body_end)?;
    d.set_item("end", n.end)?;
    let mut kids = Vec::with_capacity(n.children.len());
    for k in n.children {
        kids.push(container_dict(py, k)?);
    }
    d.set_item("children", kids)?;
    Ok(d)
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn container_parse<'py>(py: Python<'py>, text: &str, first_line: u32) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let roots = wrap(py, containers::container_parse(text, first_line))?;
    let mut out = Vec::with_capacity(roots.len());
    for n in roots {
        out.push(container_dict(py, n)?);
    }
    Ok(out)
}

#[pyfunction]
#[pyo3(signature = (text, first_line=1))]
fn directive_scan<'py>(py: Python<'py>, text: &str, first_line: u32) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let found = wrap(py, vars::directives(text, first_line))?;
    let mut out = Vec::with_capacity(found.len());
    for dir in found {
        let d = PyDict::new(py);
        d.set_item("start", dir.start)?;
        d.set_item("end", dir.end)?;
        d.set_item("name", dir.name)?;
        d.set_item("line", dir.line)?;
        d.set_item("column", dir.column)?;
        out.push(d);
    }
    Ok(out)
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

// ---- E7.1 hostile-zip reader (G47) ----

/// Internal: CRC-32 of `data` (same value as `zlib.crc32`).
#[pyfunction]
fn crc32(data: &[u8]) -> u32 {
    inflate::crc32(data)
}

/// Internal: raw DEFLATE decompression with an output limit. `_internal.inflate` checks that
/// `max_size` is a non-negative int (not a bool) before calling this.
#[pyfunction]
#[pyo3(name = "inflate")]
fn inflate_hook<'py>(py: Python<'py>, data: &[u8], max_size: u64) -> PyResult<Bound<'py, PyBytes>> {
    let out = wrap(py, inflate::inflate(data, usize::try_from(max_size).unwrap_or(usize::MAX)))?;
    Ok(PyBytes::new(py, &out))
}

/// Convert the optional `limits` dict into core limits. Not a dict: TypeError; a non-int or
/// bool value: TypeError; unknown key, duplicate or value < 1: ValueError.
fn zip_limits_arg(limits: Option<&Bound<'_, PyAny>>) -> PyResult<zip::ZipLimits> {
    let obj = match limits {
        None => return Ok(zip::ZipLimits::defaults()),
        Some(o) if o.is_none() => return Ok(zip::ZipLimits::defaults()),
        Some(o) => o,
    };
    let dict = obj.downcast::<PyDict>().map_err(|_| PyTypeError::new_err("limits must be a dict or None"))?;
    let mut pairs = Vec::new();
    for (k, v) in dict.iter() {
        let key: String = k.extract().map_err(|_| PyTypeError::new_err("zip limit names must be str"))?;
        if v.is_instance_of::<PyBool>() || !v.is_instance_of::<PyInt>() {
            return Err(PyTypeError::new_err("zip limit values must be int"));
        }
        // Negative or huge ints: below 1 is a ValueError, above u64 is clamped like any large value.
        let value: u64 = match v.extract::<i128>() {
            Ok(n) if n < 1 => return Err(PyValueError::new_err("zip limit values must be at least 1")),
            Ok(n) => u64::try_from(n).unwrap_or(u64::MAX),
            Err(_) => u64::MAX,
        };
        pairs.push((key, value));
    }
    zip::ZipLimits::from_pairs(&pairs).map_err(PyValueError::new_err)
}

fn zip_limits_dict<'py>(py: Python<'py>, l: &zip::ZipLimits) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    for (k, v) in l.as_pairs() {
        d.set_item(k, v)?;
    }
    Ok(d)
}

/// Internal: the effective zip limits (defaults, host values clamped to the hard caps).
#[pyfunction]
#[pyo3(signature = (limits=None))]
fn zip_limits<'py>(py: Python<'py>, limits: Option<&Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyDict>> {
    let l = zip_limits_arg(limits)?;
    zip_limits_dict(py, &l)
}

/// Internal: read and verify a whole zip archive given as bytes.
#[pyfunction]
#[pyo3(signature = (data, limits=None))]
fn zip_read<'py>(py: Python<'py>, data: &Bound<'py, PyAny>, limits: Option<&Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyDict>> {
    // Only real `bytes`: PyO3 would also accept other buffer-like objects for &[u8].
    let bytes = data.downcast::<PyBytes>().map_err(|_| PyTypeError::new_err("data must be bytes"))?;
    let l = zip_limits_arg(limits)?;
    let a = wrap(py, zip::zip_read(bytes.as_bytes(), &l))?;
    let members = PyList::empty(py);
    for m in &a.members {
        let d = PyDict::new(py);
        d.set_item("name", &m.name)?;
        d.set_item("method", m.method.as_str())?;
        d.set_item("size", m.size)?;
        d.set_item("compressed_size", m.compressed_size)?;
        d.set_item("crc32", m.crc32)?;
        d.set_item("data", PyBytes::new(py, &m.data))?;
        members.append(d)?;
    }
    let out = PyDict::new(py);
    out.set_item("members", members)?;
    out.set_item("directories", a.directories)?;
    Ok(out)
}

// ---- E7.2 package manifest (G48) ----

fn manifest_to_py<'py>(py: Python<'py>, m: &manifest::Manifest, out: &Bound<'py, PyDict>) -> PyResult<()> {
    out.set_item("format", &m.format)?;
    out.set_item("version", &m.version)?;
    out.set_item("entrypoint", &m.entrypoint)?;
    out.set_item("features", m.features.clone())?;
    // `requests` has the E6.4 document-layer shape, so it can be passed to policy_intersect(document=...).
    let req = PyDict::new(py);
    req.set_item("network", &m.requests.network)?;
    if let Some(h) = &m.requests.hosts {
        req.set_item("hosts", h.clone())?;
    }
    let sec = PyDict::new(py);
    sec.set_item("network", &m.requests.security_network)?;
    let requests = PyDict::new(py);
    requests.set_item("requirements", req)?;
    requests.set_item("security", sec)?;
    out.set_item("requests", requests)?;
    let decimal_cls = py.import("decimal")?.getattr("Decimal")?;
    let meta = PyDict::new(py);
    for (k, v) in &m.metadata {
        meta.set_item(k, yaml_to_py(py, v, &decimal_cls)?)?;
    }
    out.set_item("metadata", meta)?;
    Ok(())
}

/// Internal: check the text of a manifest.yaml.
#[pyfunction]
fn manifest_parse<'py>(py: Python<'py>, text: &str) -> PyResult<Bound<'py, PyDict>> {
    let m = wrap(py, manifest::manifest_parse(text))?;
    let out = PyDict::new(py);
    manifest_to_py(py, &m, &out)?;
    Ok(out)
}

/// Internal: open a `.mdq` package from bytes (no transport: opening never fetches).
#[pyfunction]
#[pyo3(signature = (data, limits=None))]
fn package_open<'py>(py: Python<'py>, data: &Bound<'py, PyAny>, limits: Option<&Bound<'py, PyAny>>) -> PyResult<Bound<'py, PyDict>> {
    let bytes = data.downcast::<PyBytes>().map_err(|_| PyTypeError::new_err("data must be bytes"))?;
    let l = zip_limits_arg(limits)?;
    let p = wrap(py, manifest::package_open(bytes.as_bytes(), &l))?;
    package_to_py(py, &p)
}

fn package_to_py<'py>(py: Python<'py>, p: &manifest::Package) -> PyResult<Bound<'py, PyDict>> {
    let out = PyDict::new(py);
    manifest_to_py(py, &p.manifest, &out)?;
    out.set_item("entrypoint_member", &p.entrypoint_member)?;
    let files = PyDict::new(py);
    for (name, data) in &p.files {
        files.set_item(name, PyBytes::new(py, data))?;
    }
    out.set_item("files", files)?;
    out.set_item("directories", p.directories.clone())?;
    Ok(out)
}

// ---- E7.3 age encryption (G49) ----

/// Only real `bytes` (not bytearray/memoryview/str).
fn bytes_arg<'py>(v: &Bound<'py, PyAny>, what: &str) -> PyResult<Bound<'py, PyBytes>> {
    v.downcast::<PyBytes>().cloned().map_err(|_| PyTypeError::new_err(format!("{what} must be bytes")))
}

/// A list/tuple of str (a bare str is refused, so "AGE-..." is not read as characters).
fn str_list(v: Option<&Bound<'_, PyAny>>, what: &str) -> PyResult<Vec<String>> {
    let Some(v) = v else { return Ok(Vec::new()) };
    if v.is_none() {
        return Ok(Vec::new());
    }
    if v.is_instance_of::<PyString>() || !(v.is_instance_of::<PyList>() || v.is_instance_of::<PyTuple>()) {
        return Err(PyTypeError::new_err(format!("{what} must be a list of str")));
    }
    let mut out = Vec::new();
    for item in v.try_iter()? {
        let item = item?;
        let s = item.downcast::<PyString>().map_err(|_| PyTypeError::new_err(format!("{what} must be a list of str")))?;
        out.push(s.to_string());
    }
    Ok(out)
}

fn identities_arg(v: Option<&Bound<'_, PyAny>>) -> PyResult<Vec<[u8; 32]>> {
    str_list(v, "identities")?
        .iter()
        .map(|s| age::parse_identity(s).map_err(PyValueError::new_err))
        .collect()
}

fn work_factor_arg(n: i64, what: &str) -> PyResult<u8> {
    if !(1..=age::MAX_WORK_FACTOR as i64).contains(&n) {
        return Err(PyValueError::new_err(format!("{what} must be between 1 and {}", age::MAX_WORK_FACTOR)));
    }
    Ok(n as u8)
}

/// Internal: decrypt an age file. `_internal.age_decrypt` checks int/bool argument types first.
#[pyfunction]
#[pyo3(signature = (data, identities=None, passphrase=None, max_work_factor=20))]
fn age_decrypt<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    identities: Option<&Bound<'py, PyAny>>,
    passphrase: Option<&str>,
    max_work_factor: i64,
) -> PyResult<Bound<'py, PyBytes>> {
    let d = bytes_arg(data, "data")?;
    let ids = identities_arg(identities)?;
    let wf = work_factor_arg(max_work_factor, "max_work_factor")?;
    let out = wrap(py, age::age_decrypt(d.as_bytes(), &ids, passphrase, wf))?;
    Ok(PyBytes::new(py, &out))
}

/// Internal: encrypt to X25519 recipients or a passphrase with caller-supplied random bytes.
#[pyfunction]
#[pyo3(signature = (data, random, recipients=None, passphrase=None, work_factor=18))]
fn age_encrypt<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    random: &Bound<'py, PyAny>,
    recipients: Option<&Bound<'py, PyAny>>,
    passphrase: Option<&str>,
    work_factor: i64,
) -> PyResult<Bound<'py, PyBytes>> {
    let d = bytes_arg(data, "data")?;
    let r = bytes_arg(random, "random")?;
    let pks: Vec<[u8; 32]> = str_list(recipients, "recipients")?
        .iter()
        .map(|s| age::parse_recipient(s).map_err(PyValueError::new_err))
        .collect::<PyResult<_>>()?;
    let pw = match passphrase {
        Some(p) => Some((p, work_factor_arg(work_factor, "work_factor")?)),
        None => None,
    };
    let out = age::age_encrypt(d.as_bytes(), &pks, pw, r.as_bytes()).map_err(PyValueError::new_err)?;
    Ok(PyBytes::new(py, &out))
}

/// Internal: how many random bytes `age_encrypt` needs.
#[pyfunction]
#[pyo3(signature = (recipients=0, passphrase=false))]
fn age_random_len(recipients: usize, passphrase: bool) -> usize {
    age::random_len(recipients, passphrase)
}

/// Internal: the `age1...` recipient of an `AGE-SECRET-KEY-1...` identity.
#[pyfunction]
fn age_recipient(identity: &str) -> PyResult<String> {
    let id = age::parse_identity(identity).map_err(PyValueError::new_err)?;
    Ok(age::recipient_of(&id))
}

/// Internal: the identity text for 32 secret bytes (for deterministic tests).
#[pyfunction]
fn age_identity(secret: &Bound<'_, PyAny>) -> PyResult<String> {
    let b = bytes_arg(secret, "secret")?;
    let arr: [u8; 32] = b.as_bytes().try_into().map_err(|_| PyValueError::new_err("secret must be 32 bytes"))?;
    Ok(age::identity_text(&arr))
}

/// Internal: decrypt a `.mdq.age` package and open it.
#[pyfunction]
#[pyo3(signature = (data, identities=None, passphrase=None, limits=None, max_work_factor=20))]
fn package_open_age<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    identities: Option<&Bound<'py, PyAny>>,
    passphrase: Option<&str>,
    limits: Option<&Bound<'py, PyAny>>,
    max_work_factor: i64,
) -> PyResult<Bound<'py, PyDict>> {
    let d = bytes_arg(data, "data")?;
    let ids = identities_arg(identities)?;
    let wf = work_factor_arg(max_work_factor, "max_work_factor")?;
    let l = zip_limits_arg(limits)?;
    let p = wrap(py, manifest::package_open_age(d.as_bytes(), &ids, passphrase, wf, &l))?;
    package_to_py(py, &p)
}

// ---- E8.1a compile / evaluate (G50) ----

fn calc_value_dict<'py>(py: Python<'py>, r: &mdq_core::calc::eval::EvalResult, decimal_cls: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyDict>> {
    use mdq_core::calc::eval::Value as V;
    let d = PyDict::new(py);
    d.set_item("name", &r.name)?;
    d.set_item("line", r.line)?;
    d.set_item("column", r.col)?;
    d.set_item("type", r.value.type_name())?;
    d.set_item("value", decimal_cls.call1((r.value.magnitude().to_canonical(),))?)?;
    let (unit, currency): (Option<&str>, Option<&str>) = match &r.value {
        V::Decimal(_) => (None, None),
        V::Quantity { unit, .. } => (Some(unit.symbol.as_str()), None),
        V::Money { currency, .. } => (None, Some(currency.as_str())),
    };
    d.set_item("unit", unit)?;
    d.set_item("currency", currency)?;
    d.set_item("display", mdq_core::calc::render::render_value(&r.value))?;
    Ok(d)
}

/// Internal: run the compile pipeline. `read(path)` returns bytes, str or None (missing); its
/// exceptions propagate unchanged. `markdown=False` is `evaluate` (data only).
#[pyfunction]
#[pyo3(signature = (entry, read, inputs=None, profile="commonmark", keep_calc=false, markdown=true,
                    max_iterations=10000, max_depth=64, max_expansions=10000, max_bytes=16777216))]
#[allow(clippy::too_many_arguments)]
fn compile_document<'py>(
    py: Python<'py>,
    entry: &str,
    read: &Bound<'py, PyAny>,
    inputs: Option<&Bound<'py, PyAny>>,
    profile: &str,
    keep_calc: bool,
    markdown: bool,
    max_iterations: usize,
    max_depth: usize,
    max_expansions: usize,
    max_bytes: usize,
) -> PyResult<Bound<'py, PyDict>> {
    if !read.is_callable() {
        return Err(PyTypeError::new_err("read must be callable"));
    }
    let prof = export::Profile::parse(profile).ok_or_else(|| PyValueError::new_err("profile must be 'commonmark' or 'gfm'"))?;
    let inputs_yaml = match inputs {
        None => yaml::Yaml::Map(Vec::new()),
        Some(o) if o.is_none() => yaml::Yaml::Map(Vec::new()),
        Some(o) => {
            if o.downcast::<PyDict>().is_err() {
                return Err(PyTypeError::new_err("inputs must be a dict"));
            }
            py_to_yaml(py, o, 0)?
        }
    };
    let failure: std::cell::RefCell<Option<PyErr>> = std::cell::RefCell::new(None);
    let mut reader = |p: &str| -> Result<Option<Vec<u8>>, Diagnostic> {
        let sentinel = || Diagnostic::new(mdq_core::diag::Code::SourceFetchFailed, "read callback failed");
        match read.call1((p,)) {
            Err(e) => {
                *failure.borrow_mut() = Some(e);
                Err(sentinel())
            }
            Ok(v) if v.is_none() => Ok(None),
            Ok(v) => {
                if let Ok(b) = v.downcast::<PyBytes>() {
                    Ok(Some(b.as_bytes().to_vec()))
                } else if let Ok(s) = v.downcast::<PyString>() {
                    Ok(Some(s.to_string().into_bytes()))
                } else {
                    *failure.borrow_mut() = Some(PyTypeError::new_err("read() must return bytes, str or None"));
                    Err(sentinel())
                }
            }
        }
    };
    let opts = compile::Options {
        profile: prof,
        keep_calc,
        markdown,
        imports: imports::Limits { max_depth, max_expansions, max_bytes, rebase_links: true, footnotes: true },
        max_iterations,
    };
    let res = compile::compile(entry, &mut reader, &inputs_yaml, &opts);
    if let Some(e) = failure.borrow_mut().take() {
        return Err(e);
    }
    let r = wrap(py, res)?;
    let decimal_cls = py.import("decimal")?.getattr("Decimal")?;
    let out = PyDict::new(py);
    out.set_item("markdown", r.markdown)?;
    out.set_item("metadata", yaml_to_py(py, &r.metadata, &decimal_cls)?)?;
    out.set_item("provenance", r.provenance)?;
    let blocks = PyList::empty(py);
    for b in &r.calc {
        let d = PyDict::new(py);
        d.set_item("path", &b.path)?;
        d.set_item("line", b.line)?;
        d.set_item("source", &b.source)?;
        let results = PyList::empty(py);
        for x in &b.results {
            results.append(calc_value_dict(py, x, &decimal_cls)?)?;
        }
        d.set_item("results", results)?;
        blocks.append(d)?;
    }
    out.set_item("calc", blocks)?;
    Ok(out)
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
    internal.add_function(wrap_pyfunction!(frontmatter_split, &internal)?)?;
    internal.add_function(wrap_pyfunction!(yaml_load_strict, &internal)?)?;
    internal.add_function(wrap_pyfunction!(meta_merge, &internal)?)?;
    internal.add_function(wrap_pyfunction!(meta_cascade, &internal)?)?;
    internal.add_function(wrap_pyfunction!(utf8_decode, &internal)?)?;
    internal.add_function(wrap_pyfunction!(var_format_scalar, &internal)?)?;
    internal.add_function(wrap_pyfunction!(md_escape_inline, &internal)?)?;
    internal.add_function(wrap_pyfunction!(interpolate, &internal)?)?;
    internal.add_function(wrap_pyfunction!(md_regions, &internal)?)?;
    internal.add_function(wrap_pyfunction!(directive_scan, &internal)?)?;
    internal.add_function(wrap_pyfunction!(generic_fallback, &internal)?)?;
    internal.add_function(wrap_pyfunction!(export_markdown, &internal)?)?;
    internal.add_function(wrap_pyfunction!(source_validate, &internal)?)?;
    internal.add_function(wrap_pyfunction!(url_template_expand, &internal)?)?;
    internal.add_function(wrap_pyfunction!(json_pointer_select, &internal)?)?;
    internal.add_function(wrap_pyfunction!(value_type_check, &internal)?)?;
    internal.add_function(wrap_pyfunction!(ip_is_blocked, &internal)?)?;
    internal.add_function(wrap_pyfunction!(url_classify, &internal)?)?;
    internal.add_function(wrap_pyfunction!(policy_intersect, &internal)?)?;
    internal.add_function(wrap_pyfunction!(policy_decide, &internal)?)?;
    internal.add_function(wrap_pyfunction!(policy_ip_allowed, &internal)?)?;
    internal.add_function(wrap_pyfunction!(source_fetch, &internal)?)?;
    internal.add_function(wrap_pyfunction!(snapshot_apply, &internal)?)?;
    internal.add_function(wrap_pyfunction!(url_join_hook, &internal)?)?;
    internal.add_function(wrap_pyfunction!(sha256_hex, &internal)?)?;
    internal.add_function(wrap_pyfunction!(crc32, &internal)?)?;
    internal.add_function(wrap_pyfunction!(inflate_hook, &internal)?)?;
    internal.add_function(wrap_pyfunction!(zip_limits, &internal)?)?;
    internal.add_function(wrap_pyfunction!(zip_read, &internal)?)?;
    internal.add_function(wrap_pyfunction!(manifest_parse, &internal)?)?;
    internal.add_function(wrap_pyfunction!(package_open, &internal)?)?;
    internal.add_function(wrap_pyfunction!(age_decrypt, &internal)?)?;
    internal.add_function(wrap_pyfunction!(age_encrypt, &internal)?)?;
    internal.add_function(wrap_pyfunction!(age_random_len, &internal)?)?;
    internal.add_function(wrap_pyfunction!(age_recipient, &internal)?)?;
    internal.add_function(wrap_pyfunction!(age_identity, &internal)?)?;
    internal.add_function(wrap_pyfunction!(package_open_age, &internal)?)?;
    internal.add_function(wrap_pyfunction!(compile_document, &internal)?)?;
    internal.add_function(wrap_pyfunction!(attrs_parse, &internal)?)?;
    internal.add_function(wrap_pyfunction!(container_parse, &internal)?)?;
    internal.add_function(wrap_pyfunction!(container_eval, &internal)?)?;
    internal.add_function(wrap_pyfunction!(path_normalise, &internal)?)?;
    internal.add_function(wrap_pyfunction!(path_identity, &internal)?)?;
    internal.add_function(wrap_pyfunction!(path_confine, &internal)?)?;
    internal.add_function(wrap_pyfunction!(heading_plain_text, &internal)?)?;
    internal.add_function(wrap_pyfunction!(heading_match, &internal)?)?;
    internal.add_function(wrap_pyfunction!(slugify, &internal)?)?;
    internal.add_function(wrap_pyfunction!(slug_dedupe, &internal)?)?;
    internal.add_function(wrap_pyfunction!(percent_encode_anchor, &internal)?)?;
    internal.add_function(wrap_pyfunction!(doc_index, &internal)?)?;
    internal.add_function(wrap_pyfunction!(resolve_ref, &internal)?)?;
    internal.add_function(wrap_pyfunction!(import_expand, &internal)?)?;
    internal.add_function(wrap_pyfunction!(rebase_link, &internal)?)?;
    internal.add_function(wrap_pyfunction!(rebase_links, &internal)?)?;
    internal.add_function(wrap_pyfunction!(toc_build, &internal)?)?;
    internal.add_function(wrap_pyfunction!(bib_parse, &internal)?)?;
    internal.add_function(wrap_pyfunction!(citation_resolve, &internal)?)?;
    internal.add_function(wrap_pyfunction!(math_fallback, &internal)?)?;
    internal.add_function(wrap_pyfunction!(footnote_check, &internal)?)?;
    internal.add_function(wrap_pyfunction!(footnote_rename, &internal)?)?;
    internal.add_function(wrap_pyfunction!(highlight_fallback, &internal)?)?;
    internal.add_function(wrap_pyfunction!(callout_fallback, &internal)?)?;
    internal.add_function(wrap_pyfunction!(details_fallback, &internal)?)?;
    internal.add_function(wrap_pyfunction!(columns_fallback, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dim_canonical, &internal)?)?;
    internal.add_function(wrap_pyfunction!(dim_combine, &internal)?)?;
    m.add_submodule(&internal)?;
    Ok(())
}
