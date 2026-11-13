//! Data for `compile` (E8.1b, G51): declared variables, variable files and bibliographies.
//!
//! * Declared variables (E6.1 `type`/`source` declarations under `variables:`): `compile` never
//!   fetches. A variable with a `source` takes its value from the host's snapshot (E6.5); one
//!   without (host-supplied) from `inputs`, type-checked (E6.2). Every declared variable needs a
//!   value: none is MDQ_SOURCE_UNRESOLVED, even if the text never uses it.
//! * Variable files: a string directly under `variables:` that starts with `./` or `../` and ends
//!   in .yaml/.yml/.json is read (relative to the file that declared it) and must be a mapping;
//!   it replaces the string, so it becomes a namespace. Files inside it are not followed.
//! * Bibliographies: `bibliography:` (a path or a list), relative to the declaring file; the
//!   extension gives the format (.bib BibTeX, .json CSL JSON).

use crate::cite::{bib_parse, Format};
use crate::diag::{Code, Diagnostic};
use crate::json::{self, Json};
use crate::paths::path_normalise;
use crate::resolver::{snapshot_resolve, Fetched, SVal, SourceRef};
use crate::source::{source_validate_value, value_type_check, Checked, Declared, Typed};
use crate::yaml::{self, Yaml};

type Res<T> = Result<T, Diagnostic>;

/// One declared variable with its value.
#[derive(Debug, Clone)]
pub struct Resolved {
    /// Dotted name under `variables:` (`price`, `fx.eur`).
    pub name: String,
    pub checked: Checked,
    /// Snapshot values only: when it was retrieved, and the source identity.
    pub retrieved_at: Option<String>,
    pub source: Option<SourceRef>,
}

fn in_file(mut d: Diagnostic, path: &str) -> Diagnostic {
    if d.path.is_none() {
        d.path = Some(path.to_string());
    }
    d
}

/// RFC 6901 pointer of `variables.<dotted name>` (as in the metadata provenance).
fn pointer(prefix: &str, dotted: &str) -> String {
    let mut p = prefix.to_string();
    for seg in dotted.split('.') {
        p.push('/');
        p.push_str(&seg.replace('~', "~0").replace('/', "~1"));
    }
    p
}

/// The layer (file) that set the value at `ptr`, or one of its leaves.
pub fn layer_of<'a>(prov: &'a [(String, String)], ptr: &str) -> Option<&'a str> {
    prov.iter()
        .find(|(p, _)| p == ptr || p.starts_with(&format!("{}/", ptr)))
        .map(|(_, l)| l.as_str())
}

/// Directory of a layer name (a root-relative file path, or `<inputs>` = the root).
fn base_dir(layer: Option<&str>) -> &str {
    match layer {
        Some(l) if !l.starts_with('<') => l.rfind('/').map(|i| &l[..i]).unwrap_or(""),
        _ => "",
    }
}

fn lookup<'a>(v: &'a Yaml, dotted: &str) -> Option<&'a Yaml> {
    let mut cur = v;
    for seg in dotted.split('.') {
        match cur {
            Yaml::Map(e) => cur = &e.iter().find(|(k, _)| k == seg)?.1,
            _ => return None,
        }
    }
    Some(cur)
}

/// Remove `dotted` from a mapping (used to keep host values for declared variables out of the
/// metadata layers, where they would overwrite the declaration).
fn remove(v: &mut Yaml, dotted: &str) {
    let Yaml::Map(entries) = v else { return };
    match dotted.split_once('.') {
        None => entries.retain(|(k, _)| k != dotted),
        Some((first, rest)) => {
            if let Some((_, child)) = entries.iter_mut().find(|(k, _)| k == first) {
                remove(child, rest);
            }
        }
    }
}

/// Replace the value at `dotted` (it exists: it is a declaration).
fn set(v: &mut Yaml, dotted: &str, value: Yaml) {
    let Yaml::Map(entries) = v else { return };
    let (first, rest) = match dotted.split_once('.') {
        Some((a, b)) => (a, Some(b)),
        None => (dotted, None),
    };
    if let Some((_, child)) = entries.iter_mut().find(|(k, _)| k == first) {
        match rest {
            None => *child = value,
            Some(r) => set(child, r, value),
        }
    }
}

fn yaml_to_json(v: &Yaml) -> Json {
    match v {
        Yaml::Null => Json::Null,
        Yaml::Bool(b) => Json::Bool(*b),
        Yaml::Number(n) => Json::Num(n.clone()),
        Yaml::Str(s) => Json::Str(s.clone()),
        Yaml::Seq(items) => Json::Arr(items.iter().map(yaml_to_json).collect()),
        Yaml::Map(e) => Json::Obj(e.iter().map(|(k, v)| (k.clone(), yaml_to_json(v))).collect()),
    }
}

fn json_to_yaml(v: &Json) -> Res<Yaml> {
    Ok(match v {
        Json::Null => Yaml::Null,
        Json::Bool(b) => Yaml::Bool(*b),
        // JSON keeps the source digits (`1e3`); metadata numbers are canonical decimals.
        Json::Num(n) => Yaml::Number(crate::vars::canonical_number(n)?),
        Json::Str(s) => Yaml::Str(s.clone()),
        Json::Arr(items) => Yaml::Seq(items.iter().map(json_to_yaml).collect::<Res<_>>()?),
        Json::Obj(e) => Yaml::Map(e.iter().map(|(k, v)| Ok((k.clone(), json_to_yaml(v)?))).collect::<Res<_>>()?),
    })
}

/// How a resolved value appears to `{{...}}`: numbers with a unit as `"120 EUR"` text.
pub fn display_value(c: &Checked) -> Yaml {
    match (&c.value, &c.unit) {
        (Typed::Num(n), None) => Yaml::Number(n.clone()),
        (Typed::Num(n), Some(u)) => Yaml::Str(format!("{} {}", n, u)),
        (Typed::Str(s), _) | (Typed::Date(s), _) => Yaml::Str(s.clone()),
        (Typed::Bool(b), _) => Yaml::Bool(*b),
    }
}

/// The declarations of the metadata BEFORE host inputs are layered on top (an input would
/// otherwise replace the declaration it is meant to fill). Errors get the declaring file.
pub fn declarations(meta: &Yaml, prov: &[(String, String)]) -> Res<Vec<Declared>> {
    source_validate_value(meta).map_err(|d| {
        // E6.1 messages name the key path (`variables.price.type ...`): its first two segments
        // give the pointer whose layer declared it.
        let word = d.message.split_whitespace().find(|w| w.starts_with("variables.") || w.starts_with("sources."));
        let layer = word.and_then(|w| {
            let mut segs = w.trim_end_matches([':', ',']).split('.');
            let (top, name) = (segs.next()?, segs.next()?);
            layer_of(prov, &pointer(&format!("/{}", top), name))
        });
        match layer {
            Some(p) => in_file(d.clone(), p),
            None => d,
        }
    })
}

/// Split the host inputs: values for host-supplied declared variables (returned) are removed from
/// `inputs`; an input for a variable that has a `source` is refused (its value is the snapshot's).
pub fn take_declared_inputs(decls: &[Declared], inputs: &mut Yaml) -> Res<Vec<(String, Yaml)>> {
    let mut out = Vec::new();
    for d in decls {
        if let Some(v) = lookup(inputs, &d.name) {
            if d.source.is_some() {
                return Err(Diagnostic::new(
                    Code::SourceInvalid,
                    format!("variables.{} has a source: give its value through the snapshot, not inputs", d.name),
                ));
            }
            out.push((d.name.clone(), v.clone()));
            remove(inputs, &d.name);
        }
    }
    Ok(out)
}

/// Values for every declaration (snapshot for sources, inputs for host-supplied ones).
pub fn resolve(decls: &[Declared], snapshot: &[SVal], host: &[(String, Yaml)]) -> Res<Vec<Resolved>> {
    let fetched: Vec<Fetched> = snapshot_resolve(decls, snapshot)?;
    let mut out = Vec::new();
    for d in decls {
        if d.source.is_some() {
            if let Some(f) = fetched.iter().find(|f| f.name == d.name) {
                out.push(Resolved {
                    name: d.name.clone(),
                    checked: f.checked.clone(),
                    retrieved_at: Some(f.retrieved_at.clone()),
                    source: Some(f.source.clone()),
                });
            }
            continue;
        }
        let Some((_, v)) = host.iter().find(|(n, _)| *n == d.name) else {
            return Err(Diagnostic::new(
                Code::SourceUnresolved,
                format!("no value for the host-supplied variable '{}' (pass it in inputs)", d.name),
            ));
        };
        let checked = value_type_check(&yaml_to_json(v), &d.vtype, d.unit.as_deref(), false)
            .map_err(|e| Diagnostic::new(e.code, format!("{} (variable '{}')", e.message, d.name)))?;
        out.push(Resolved { name: d.name.clone(), checked, retrieved_at: None, source: None });
    }
    Ok(out)
}

/// Write the resolved values into the `variables` tree (replacing their declarations).
pub fn apply_values(vars: &mut Yaml, values: &[Resolved]) {
    for r in values {
        set(vars, &r.name, display_value(&r.checked));
    }
}

fn is_var_file(s: &str) -> bool {
    let lower = s.to_ascii_lowercase();
    (s.starts_with("./") || s.starts_with("../")) && [".yaml", ".yml", ".json"].iter().any(|e| lower.ends_with(e))
}

/// Load the mapping in a YAML or JSON data file.
fn load_mapping(path: &str, text: &str) -> Res<Yaml> {
    let v = if path.to_ascii_lowercase().ends_with(".json") {
        json_to_yaml(&json::parse(text, "variable file")?)?
    } else {
        yaml::load(text, true, 1)?
    };
    match v {
        Yaml::Map(_) => Ok(v),
        _ => Err(Diagnostic::new(Code::MetadataError, "a variable file must contain a mapping")),
    }
}

/// Replace variable-file strings directly under `vars` with the mappings they name.
pub fn load_var_files(
    vars: &mut Yaml,
    prov: &[(String, String)],
    read: &mut dyn FnMut(&str) -> Res<Option<String>>,
) -> Res<()> {
    let Yaml::Map(entries) = vars else { return Ok(()) };
    for (k, v) in entries.iter_mut() {
        let Yaml::Str(s) = v else { continue };
        if !is_var_file(s) {
            continue;
        }
        let layer = layer_of(prov, &pointer("/variables", k));
        let declared_in = layer.unwrap_or("<inputs>").to_string();
        let path = path_normalise(base_dir(layer), s).map_err(|d| in_file(d, &declared_in))?;
        let text = read(&path)?.ok_or_else(|| {
            in_file(
                Diagnostic::new(Code::MissingImport, format!("the variable file for variables.{} does not exist", k)),
                &declared_in,
            )
        })?;
        *v = load_mapping(&path, &text).map_err(|d| in_file(d, &path))?;
    }
    Ok(())
}

/// Read every bibliography named by `bibliography:` and check it parses.
pub fn load_bibliographies(
    meta: &Yaml,
    prov: &[(String, String)],
    read: &mut dyn FnMut(&str) -> Res<Option<String>>,
) -> Res<Vec<(Format, String)>> {
    let entry = match meta {
        Yaml::Map(e) => e.iter().find(|(k, _)| k == "bibliography").map(|(_, v)| v),
        _ => None,
    };
    let declared_in = layer_of(prov, "/bibliography").unwrap_or("<inputs>").to_string();
    let bad = || in_file(Diagnostic::new(Code::MetadataError, "bibliography must be a path or a list of paths"), &declared_in);
    let paths: Vec<String> = match entry {
        None | Some(Yaml::Null) => return Ok(Vec::new()),
        Some(Yaml::Str(s)) => vec![s.clone()],
        Some(Yaml::Seq(items)) => items
            .iter()
            .map(|i| match i {
                Yaml::Str(s) => Ok(s.clone()),
                _ => Err(bad()),
            })
            .collect::<Res<_>>()?,
        Some(_) => return Err(bad()),
    };
    let base = base_dir(layer_of(prov, "/bibliography")).to_string();
    let mut out = Vec::new();
    for p in paths {
        let path = path_normalise(&base, &p).map_err(|d| in_file(d, &declared_in))?;
        let lower = path.to_ascii_lowercase();
        let format = if lower.ends_with(".bib") {
            Format::Bibtex
        } else if lower.ends_with(".json") {
            Format::CslJson
        } else {
            return Err(in_file(
                Diagnostic::new(Code::UnsupportedFeature, "a bibliography must be .bib (BibTeX) or .json (CSL JSON)"),
                &declared_in,
            ));
        };
        let text = read(&path)?.ok_or_else(|| {
            in_file(Diagnostic::new(Code::MissingImport, "the bibliography file does not exist"), &declared_in)
        })?;
        bib_parse(&text, format).map_err(|d| in_file(d, &path))?;
        out.push((format, text));
    }
    Ok(out)
}

/// Give a value error (missing snapshot entry, missing input, type mismatch) the path of the file
/// that declared the variable it names ('name' in quotes in the message).
pub fn locate_declared(d: Diagnostic, decls: &[Declared], prov: &[(String, String)]) -> Diagnostic {
    if d.path.is_some() {
        return d;
    }
    let named = decls.iter().find(|x| d.message.contains(&format!("'{}'", x.name)));
    match named.and_then(|x| layer_of(prov, &pointer("/variables", &x.name))) {
        Some(layer) if !layer.starts_with('<') => in_file(d.clone(), layer),
        _ => d,
    }
}
