//! `compile` and `evaluate`: one document, read through a host callback (spec section 9; E8.1a, G50).
//!
//! The sources are never modified: everything is read through `read` and the result is new text
//! and data. Steps (spec 9), with the order chosen for `calc`:
//!
//! 1. read and decode the entry (G28), split its frontmatter (G22);
//! 2. metadata cascade (G24/G25): root `.mdq.yaml`, every `.mdq.yaml` down to the entry's directory,
//!    frontmatter, sidecar `<stem>.meta.yaml`, then the host `inputs` as the top layer
//!    (`variables.<name>`); check `mdq` and `features` (F01);
//! 3. interpolation scope: `document.*` = the effective metadata, every entry under `variables:` as
//!    a top-level name (a mapping is a namespace), `calc.*` reserved;
//! 4. `:::if`/`:::for` and `{{...}}` per file (G31), with `{{calc.*}}` DEFERRED (left in place),
//!    then imports (G35): the read callback hands `import_expand` already-evaluated files, so
//!    excluded branches are never read;
//! 5. every ```calc block that survived is evaluated in document order (G11: bare names are earlier
//!    assignments, then numeric entries under `variables:`); a name exported twice is an error;
//! 6. second interpolation pass for `{{calc.*}}`; block-ID markers removed (spec 2);
//! 7. `compile` only: export fallbacks for the profile (G41), then ```calc blocks are removed
//!    (`keep_calc` keeps them).
//!
//! Steps 6 and 7 keep the line structure of the expanded text, so every later diagnostic is mapped
//! back to (file, line) through the import source map.

use crate::blocks::{line_bounds, parse, Kind};
use crate::calc::eval::{evaluate_source, parse_host_values, EvalResult, Value};
use crate::containers::container_eval_deferred;
use crate::data::{self, Resolved};
use crate::decimal::Decimal;
use crate::diag::{Code, Diagnostic};
use crate::export::{export_markdown, Profile};
use crate::frontmatter;
use crate::fx::FxContext;
use crate::imports::{import_expand, Expanded, Limits};
use crate::meta::{cascade_values, Cascade, Layer, Merged};
use crate::paths::path_normalise;
use crate::refs::strip_block_ids;
use crate::policy::{self, PVal};
use crate::resolver::{self, FetchError, Fetched, SVal, Transport};
use crate::source::Declared;
use crate::utf8;
use crate::vars::interpolate;
use crate::version::{check_required, SYNTAX_VERSION};
use crate::yaml::Yaml;
use std::collections::HashMap;

type Res<T> = Result<T, Diagnostic>;

/// The host's file reader: the bytes of a root-relative path, or `None` if it does not exist.
pub type Read<'a> = &'a mut dyn FnMut(&str) -> Res<Option<Vec<u8>>>;

/// What `compile` needs to know about a package (E7.2/E7.3), when the files come from one.
#[derive(Clone, Debug)]
pub struct PackageInfo {
    /// The manifest's shared metadata (every key but the package keys): the lowest layer.
    pub metadata: Yaml,
    /// The manifest's `security`/`requirements` as an E6.4 document request layer.
    pub requests: Vec<(String, PVal)>,
    /// Opened from `.mdq.age`: compiled Markdown is plaintext and needs the host's permission.
    pub encrypted: bool,
}

#[derive(Clone)]
pub struct Options {
    pub profile: Profile,
    /// Keep ```calc blocks in the Markdown output (they are always evaluated and reported).
    pub keep_calc: bool,
    /// `compile` = true; `evaluate` = false (no export step, no Markdown).
    pub markdown: bool,
    pub imports: Limits,
    pub max_iterations: usize,
    pub package: Option<PackageInfo>,
    /// The host allows plaintext output of an encrypted package.
    pub allow_plaintext: bool,
}

impl Default for Options {
    fn default() -> Self {
        Options {
            profile: Profile::CommonMark,
            keep_calc: false,
            markdown: true,
            imports: Limits::default(),
            max_iterations: 10_000,
            package: None,
            allow_plaintext: false,
        }
    }
}

/// One evaluated ```calc block.
#[derive(Debug, Clone)]
pub struct CalcBlock {
    pub path: String,
    /// 1-based line of the opening fence in `path`.
    pub line: u32,
    /// The block's content (without the fence lines), as written.
    pub source: String,
    pub results: Vec<EvalResult>,
}

#[derive(Debug, Clone)]
pub struct Compiled {
    pub markdown: Option<String>,
    pub metadata: Yaml,
    /// (JSON Pointer, layer name) for every metadata leaf (G24).
    pub provenance: Vec<(String, String)>,
    pub calc: Vec<CalcBlock>,
    /// Declared variables (E6.1) with their values, in declaration order.
    pub values: Vec<Resolved>,
}

const RESERVED: [&str; 2] = ["document", "calc"];

fn meta_err(msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::MetadataError, msg)
}

fn in_file(mut d: Diagnostic, path: &str) -> Diagnostic {
    if d.path.is_none() {
        d.path = Some(path.to_string());
    }
    d
}

/// Read and decode a file; `Ok(None)` when it does not exist.
fn read_text(read: Read<'_>, path: &str) -> Res<Option<String>> {
    match read(path)? {
        None => Ok(None),
        Some(bytes) => Ok(Some(utf8::decode(&bytes).map_err(|d| in_file(d, path))?)),
    }
}

fn get<'a>(m: &'a Yaml, k: &str) -> Option<&'a Yaml> {
    match m {
        Yaml::Map(e) => e.iter().find(|(n, _)| n == k).map(|(_, v)| v),
        _ => None,
    }
}

/// `mdq` and `features` (spec F01): an unsupported version or profile fails, never disappears.
fn check_versions(meta: &Yaml) -> Res<()> {
    let version = match get(meta, "mdq") {
        None | Some(Yaml::Null) => SYNTAX_VERSION.to_string(),
        Some(Yaml::Str(s)) => s.clone(),
        Some(Yaml::Number(_)) => return Err(meta_err("mdq must be a quoted string, for example '0.1'")),
        Some(_) => return Err(meta_err("mdq must be a string")),
    };
    let features: Vec<String> = match get(meta, "features") {
        None | Some(Yaml::Null) => Vec::new(),
        Some(Yaml::Seq(items)) => items
            .iter()
            .map(|i| match i {
                Yaml::Str(s) => Ok(s.clone()),
                _ => Err(meta_err("features must be a list of strings")),
            })
            .collect::<Res<_>>()?,
        Some(_) => return Err(meta_err("features must be a list of strings")),
    };
    check_required(&features, &version)
}

/// The interpolation scope (step 3) and the calc inputs (G11): `document.*` = the effective
/// metadata, every entry of the (resolved) `variables` tree a top-level name.
fn scope_and_inputs(meta: &Yaml, vars: &Yaml) -> Res<(Yaml, HashMap<String, Value>)> {
    let vars: Vec<(String, Yaml)> = match vars {
        Yaml::Map(e) => e.clone(),
        _ => Vec::new(),
    };
    let mut scope = vec![("document".to_string(), meta.clone())];
    let mut calc_in = HashMap::new();
    for (k, v) in &vars {
        if RESERVED.contains(&k.as_str()) {
            return Err(meta_err(format!("variables.{} uses a reserved namespace ({})", k, RESERVED.join(", "))));
        }
        // Calc inputs: numbers, and strings in calc literal syntax ("120 EUR", "12 km"). Other
        // values are simply not visible to calc (an unknown name there is MDQ_CALC_ERROR).
        let valid = k.chars().next().is_some_and(|c| c.is_ascii_alphabetic())
            && k.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
            && k != "to";
        if valid {
            match v {
                Yaml::Number(n) => {
                    let canon = crate::vars::canonical_number(n).map_err(|d| meta_err(format!("variables.{}: {}", k, d.message)))?;
                    calc_in.insert(k.clone(), Value::Decimal(Decimal::parse(&canon)?));
                }
                Yaml::Str(s) => {
                    if let Ok(mut m) = parse_host_values(&[(k.clone(), s.clone())]) {
                        if let Some(val) = m.remove(k) {
                            calc_in.insert(k.clone(), val);
                        }
                    }
                }
                _ => {}
            }
        }
        scope.push((k.clone(), v.clone()));
    }
    scope.push(("calc".to_string(), Yaml::Map(Vec::new())));
    Ok((Yaml::Map(scope), calc_in))
}

/// A declared number with a unit must be usable by calc: the unit is a catalogue unit or a
/// currency code, otherwise the declaration is refused (never silently invisible to calc).
fn check_units(values: &[Resolved]) -> Res<()> {
    for r in values {
        if let (crate::source::Typed::Num(n), Some(u)) = (&r.checked.value, &r.checked.unit) {
            if parse_host_values(&[("v".to_string(), format!("{} {}", n, u))]).is_err() {
                return Err(Diagnostic::new(
                    Code::SourceInvalid,
                    format!("variables.{}: unit {:?} is neither a catalogue unit nor a currency code", r.name, u),
                ));
            }
        }
    }
    Ok(())
}

/// Directory part of a root-relative path ("" for the root).
fn dir_of(path: &str) -> &str {
    path.rfind('/').map(|i| &path[..i]).unwrap_or("")
}

fn join(dir: &str, name: &str) -> String {
    if dir.is_empty() {
        name.to_string()
    } else {
        format!("{}/{}", dir, name)
    }
}

/// A fenced code block whose info string's first word is `calc`.
struct CalcFence {
    /// 0-based line of the opening fence, and the content lines `a..b`.
    open: usize,
    a: usize,
    b: usize,
    /// Leaf range, for removal.
    end: usize,
}

/// Find ```calc fences. One inside a block quote or list item is refused (not silently skipped).
/// The fences plus the text as chars and its line starts/ends (to read the content back).
type Fences = (Vec<CalcFence>, Vec<char>, Vec<usize>, Vec<usize>);

fn calc_fences(text: &str) -> Res<Fences> {
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    let mut out = Vec::new();
    for leaf in parse(&c, &starts, &ends, 1)? {
        if leaf.kind != Kind::CodeFence {
            continue;
        }
        let line: String = c[starts[leaf.first_line]..ends[leaf.first_line]].iter().collect();
        let trimmed = line.trim_start_matches(' ');
        let indent = line.len() - trimmed.len();
        let fence_char = trimmed.chars().next().unwrap_or(' ');
        let (prefixed, after) = if indent <= 3 && (fence_char == '`' || fence_char == '~') {
            (false, trimmed)
        } else {
            // A fence behind quote/list markers: look past them only to tell whether it is calc.
            let k = line.find(['`', '~']).unwrap_or(line.len());
            (true, &line[k..])
        };
        let fc = after.chars().next().unwrap_or('`');
        let run = after.chars().take_while(|&ch| ch == fc).count();
        let info = after[run..].trim();
        if info.split_whitespace().next() != Some("calc") {
            continue;
        }
        if prefixed {
            return Err(Diagnostic::new(Code::UnsupportedFeature, "calc blocks inside block quotes or list items are not supported")
                .at_position(leaf.first_line as u32 + 1, 1));
        }
        // Content: up to the closing fence (same character, at least as long, nothing after it).
        let last = leaf.end_line - 1;
        let close: String = c[starts[last]..ends[last]].iter().collect();
        let ct = close.trim();
        let closed = last > leaf.first_line && ct.chars().all(|ch| ch == fc) && ct.chars().count() >= run;
        let b = if closed { last } else { leaf.end_line };
        out.push(CalcFence { open: leaf.first_line, a: leaf.first_line + 1, b, end: leaf.end_line });
    }
    Ok((out, c, starts, ends))
}

/// Text of lines `a..b` (0-based, exclusive), joined with `\n`.
fn lines_text(c: &[char], starts: &[usize], ends: &[usize], a: usize, b: usize) -> String {
    (a..b).map(|k| c[starts[k]..ends[k]].iter().collect::<String>()).collect::<Vec<_>>().join("\n")
}

/// Map a 1-based line of the expanded text to (file, 1-based line) through the import source map.
fn locate(ex: &Expanded, line: u32) -> Option<(String, u32)> {
    let idx = line.checked_sub(1)? as usize;
    let s = ex.map.iter().find(|s| s.out_start <= idx && idx < s.out_end)?;
    Some((s.path.clone(), (s.src_start + (idx - s.out_start)) as u32))
}

fn remap(ex: &Expanded, mut d: Diagnostic) -> Diagnostic {
    if d.path.is_none() {
        if let Some(l) = d.line {
            if let Some((p, sl)) = locate(ex, l) {
                d.path = Some(p);
                d.line = Some(sl);
            }
        }
    }
    d
}

/// Compile (or, with `opts.markdown = false`, only evaluate) the document at `entry`.
pub fn compile(entry: &str, read: Read<'_>, inputs: &Yaml, snapshot: &[SVal], fx: &FxContext, opts: &Options) -> Res<Compiled> {
    if opts.markdown && !opts.allow_plaintext && opts.package.as_ref().is_some_and(|p| p.encrypted) {
        return Err(Diagnostic::new(
            Code::SecurityDenied,
            "compiling an encrypted package produces plaintext: the host must allow it (allow_plaintext)",
        ));
    }
    let g = gather(entry, read, opts.package.as_ref())?;
    let (entry, text, cas, bottom) = (g.entry, g.text, g.cas, g.bottom);
    let bottom_ref = bottom.as_ref().map(|b| ("manifest.yaml", b));
    // Declarations come from the files only: an input layered on top would replace the
    // declaration it is meant to fill. Inputs for declared variables are taken out first.
    let files_only = cascade_values(&cas, bottom_ref, None)?;
    if !matches!(get(&files_only.value, "variables"), None | Some(Yaml::Null) | Some(Yaml::Map(_))) {
        return Err(meta_err("variables must be a mapping"));
    }
    let decls = data::declarations(&files_only.value, &files_only.provenance)?;
    let mut rest = inputs.clone();
    let host_values = data::take_declared_inputs(&decls, &mut rest)?;
    let top = match &rest {
        Yaml::Map(e) if !e.is_empty() => Some(Yaml::Map(vec![("variables".to_string(), rest.clone())])),
        _ => None,
    };
    let merged = cascade_values(&cas, bottom_ref, top.as_ref().map(|t| ("<inputs>", t)))?;
    check_versions(&merged.value)?;
    let values = data::resolve(&decls, snapshot, &host_values)?;
    check_units(&values)?;
    let mut vars = match get(&merged.value, "variables") {
        None | Some(Yaml::Null) => Yaml::Map(Vec::new()),
        Some(m @ Yaml::Map(_)) => m.clone(),
        Some(_) => return Err(meta_err("variables must be a mapping")),
    };
    data::load_var_files(&mut vars, &merged.provenance, &mut |p| read_text(read, p))?;
    data::apply_values(&mut vars, &values);
    let bibs = data::load_bibliographies(&merged.value, &merged.provenance, &mut |p| read_text(read, p))?;
    // The effective metadata as the document sees it: variable files loaded, declarations
    // replaced by their values.
    let mut effective = merged.value.clone();
    if let Yaml::Map(e) = &mut effective {
        if let Some(slot) = e.iter_mut().find(|(k, _)| k == "variables") {
            slot.1 = vars.clone();
        }
    }
    let (scope, calc_in) = scope_and_inputs(&effective, &vars)?;

    // 4. containers (calc deferred) per file, then imports of already-evaluated files.
    let mut originals: HashMap<String, String> = HashMap::new();
    let eval_file = |path: &str, text: &str| -> Res<String> {
        let sp = frontmatter::split(text).map_err(|d| in_file(d, path))?;
        let body = container_eval_deferred(sp.body, &scope, "calc", sp.body_line, opts.max_iterations)
            .map_err(|d| in_file(d, path))?;
        // Keep the original frontmatter lines so `import_expand` counts file lines correctly.
        let t = text.strip_prefix('\u{feff}').unwrap_or(text);
        let head = &t[..t.len() - sp.body.len()];
        Ok(format!("{}{}", head, body))
    };
    let root_eval = eval_file(&entry, &text)?;
    originals.insert(entry.clone(), text.clone());
    let mut import_read = |p: &str| -> Res<Option<String>> {
        match read_text(read, p)? {
            None => Ok(None),
            Some(t) => {
                let ev = eval_file(p, &t)?;
                originals.insert(p.to_string(), t);
                Ok(Some(ev))
            }
        }
    };
    let ex = import_expand(&root_eval, &entry, &mut import_read, opts.imports)?;

    // 5. calc blocks
    let (fences, c, starts, ends) = calc_fences(&ex.text).map_err(|d| remap(&ex, d))?;
    let mut blocks: Vec<CalcBlock> = Vec::new();
    let mut exported: HashMap<String, (String, u32)> = HashMap::new();
    let mut calc_ns: Vec<(String, Yaml)> = Vec::new();
    for f in &fences {
        let source = lines_text(&c, &starts, &ends, f.a, f.b);
        let (path, open_line) = locate(&ex, f.open as u32 + 1).unwrap_or((entry.clone(), f.open as u32 + 1));
        let line = exact_line(originals.get(&path), &source).unwrap_or(open_line);
        let results = evaluate_source(&source, line + 1, &calc_in, fx).map_err(|d| in_file(d, &path))?;
        for r in &results {
            if let Some((p, l)) = exported.get(&r.name) {
                return Err(Diagnostic::new(
                    Code::CalcError,
                    format!("calc.{} is already defined by the calc block at {}:{}", r.name, p, l),
                )
                .at(path.clone(), r.line, r.col));
            }
        }
        for r in &results {
            exported.insert(r.name.clone(), (path.clone(), line));
            calc_ns.push((r.name.clone(), Yaml::Str(crate::calc::render::render_value(&r.value))));
        }
        blocks.push(CalcBlock { path, line, source, results });
    }

    // 6. calc references, block IDs (both keep the line structure)
    let calc_scope = Yaml::Map(vec![("calc".to_string(), Yaml::Map(calc_ns))]);
    let text2 = interpolate(&ex.text, &calc_scope, 1).map_err(|d| remap(&ex, d))?;
    let text3 = strip_block_ids(&text2).map_err(|d| remap(&ex, d))?;

    // 7. export, then drop the calc blocks
    let markdown = if opts.markdown {
        let out = export_markdown(&text3, opts.profile, &bibs, 1).map_err(|d| remap(&ex, d))?;
        Some(if opts.keep_calc { out } else { remove_calc_blocks(&out)? })
    } else {
        None
    };
    Ok(Compiled { markdown, metadata: effective, provenance: merged.provenance, calc: blocks, values })
}

/// The entry file and its metadata layers (step 1 and the file part of step 2).
struct Gathered {
    entry: String,
    text: String,
    cas: Cascade,
    /// The package manifest's metadata (the lowest layer), if any.
    bottom: Option<Yaml>,
}

fn gather(entry: &str, read: Read<'_>, package: Option<&PackageInfo>) -> Res<Gathered> {
    let entry = path_normalise("", entry)?;
    let text = read_text(read, &entry)?.ok_or_else(|| {
        in_file(Diagnostic::new(Code::MissingImport, "the entry file does not exist"), &entry)
    })?;
    let sp = frontmatter::split(&text).map_err(|d| in_file(d, &entry))?;

    // 2. metadata cascade
    let dir = dir_of(&entry).to_string();
    let mut cas = Cascade::default();
    if let Some(t) = read_text(read, ".mdq.yaml")? {
        cas.project = Some(Layer::new(".mdq.yaml", &t, 1));
    }
    let mut acc = String::new();
    for seg in dir.split('/').filter(|s| !s.is_empty()) {
        acc = join(&acc, seg);
        let p = join(&acc, ".mdq.yaml");
        if let Some(t) = read_text(read, &p)? {
            cas.directories.push(Layer::new(&p, &t, 1));
        }
    }
    if let Some(fm) = &sp.frontmatter {
        cas.frontmatter = Some(Layer::new(&entry, fm, sp.frontmatter_line));
    }
    let file = entry.rsplit('/').next().unwrap_or(&entry);
    let stem = file.rfind('.').map(|i| &file[..i]).unwrap_or(file);
    let sidecar = join(&dir, &format!("{}.meta.yaml", stem));
    if let Some(t) = read_text(read, &sidecar)? {
        cas.sidecar = Some(Layer::new(&sidecar, &t, 1));
    }
    let bottom = package.map(|p| p.metadata.clone()).filter(|m| matches!(m, Yaml::Map(e) if !e.is_empty()));
    Ok(Gathered { entry, text, cas, bottom })
}

/// The effective metadata of the files (manifest, `.mdq.yaml`, frontmatter, sidecar; no inputs).
fn files_metadata(g: &Gathered) -> Res<Merged> {
    cascade_values(&g.cas, g.bottom.as_ref().map(|b| ("manifest.yaml", b)), None)
}

/// Every declared variable of the document, checked offline (spec 3 `validate_source`).
pub fn validate_sources(entry: &str, read: Read<'_>, package: Option<&PackageInfo>) -> Res<Vec<Declared>> {
    let g = gather(entry, read, package)?;
    let m = files_metadata(&g)?;
    if !matches!(get(&m.value, "variables"), None | Some(Yaml::Null) | Some(Yaml::Map(_))) {
        return Err(meta_err("variables must be a mapping"));
    }
    data::declarations(&m.value, &m.provenance)
}

/// The document's network requests: the manifest's, then `requirements`/`security` of every
/// metadata file. They are combined most-restrictively by `policy_intersect_docs`.
fn document_requests(g: &Gathered, package: Option<&PackageInfo>) -> Res<Vec<Vec<(String, PVal)>>> {
    let mut out = Vec::new();
    if let Some(p) = package {
        out.push(p.requests.clone());
    }
    let layers = g.cas.project.iter().chain(g.cas.directories.iter()).chain(g.cas.frontmatter.iter()).chain(g.cas.sidecar.iter());
    for l in layers {
        let doc = crate::yaml::load(&l.text, true, l.first_line).map_err(|d| in_file(d, &l.name))?;
        let mut req = Vec::new();
        for key in ["requirements", "security"] {
            if let Some(v) = get(&doc, key) {
                req.push((key.to_string(), to_pval(v)));
            }
        }
        if !req.is_empty() {
            // Validate this layer on its own so an error names its file.
            policy::policy_intersect(None, None, Some(&req)).map_err(|d| in_file(d, &l.name))?;
            out.push(req);
        }
    }
    Ok(out)
}

fn to_pval(v: &Yaml) -> PVal {
    match v {
        Yaml::Null => PVal::None,
        Yaml::Bool(b) => PVal::Bool(*b),
        Yaml::Number(n) => n.parse::<i64>().map(PVal::Int).unwrap_or(PVal::Other),
        Yaml::Str(s) => PVal::Str(s.clone()),
        Yaml::Seq(items) => PVal::List(items.iter().map(to_pval).collect()),
        Yaml::Map(e) => PVal::Map(e.iter().map(|(k, v)| (k.clone(), to_pval(v))).collect()),
    }
}

/// One authorised retrieval of a declared variable of the document (spec 3 `test_source`).
/// The policy is the intersection of the two trusted layers and every document request; the
/// result is a snapshot entry for `compile`.
#[allow(clippy::too_many_arguments)]
pub fn test_source(
    entry: &str,
    read: Read<'_>,
    package: Option<&PackageInfo>,
    name: &str,
    host: Option<&[(String, PVal)]>,
    library: Option<&[(String, PVal)]>,
    t: &mut dyn Transport,
    retrieved_at: &str,
    allow_network: bool,
    headers: &[(String, String)],
) -> Result<Fetched, FetchError> {
    let g = gather(entry, read, package)?;
    let m = files_metadata(&g)?;
    let reqs = document_requests(&g, package)?;
    let docs: Vec<&[(String, PVal)]> = reqs.iter().map(|r| r.as_slice()).collect();
    let eff = policy::policy_intersect_docs(host, library, &docs)?;
    let map = policy::effective_map(&eff);
    resolver::source_fetch_value(&m.value, name, &map, t, retrieved_at, allow_network, headers)
}

/// The 1-based line of the opening fence of the ONE calc block in `original` whose content is
/// `source` (conditionals before it may have shifted the expanded line); None if not unique.
fn exact_line(original: Option<&String>, source: &str) -> Option<u32> {
    let text = original?;
    let sp = frontmatter::split(text).ok()?;
    let (fences, c, starts, ends) = calc_fences(sp.body).ok()?;
    let hits: Vec<&CalcFence> = fences.iter().filter(|f| lines_text(&c, &starts, &ends, f.a, f.b) == source).collect();
    match hits.as_slice() {
        [one] => Some(one.open as u32 + sp.body_line),
        _ => None,
    }
}

/// Remove every ```calc block (whole lines) from exported Markdown.
fn remove_calc_blocks(text: &str) -> Res<String> {
    let (fences, c, starts, _ends) = calc_fences(text)?;
    if fences.is_empty() {
        return Ok(text.to_string());
    }
    let mut out = String::with_capacity(text.len());
    let mut pos = 0;
    for f in &fences {
        let a = starts[f.open];
        let b = if f.end < starts.len() { starts[f.end] } else { c.len() };
        out.extend(c[pos..a].iter());
        pos = b;
    }
    out.extend(c[pos..].iter());
    Ok(out)
}
