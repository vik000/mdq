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
use crate::decimal::Decimal;
use crate::diag::{Code, Diagnostic};
use crate::export::{export_markdown, Profile};
use crate::frontmatter;
use crate::fx::FxContext;
use crate::imports::{import_expand, Expanded, Limits};
use crate::meta::{cascade_with_top, Cascade, Layer};
use crate::paths::path_normalise;
use crate::refs::strip_block_ids;
use crate::utf8;
use crate::vars::interpolate;
use crate::version::{check_required, SYNTAX_VERSION};
use crate::yaml::Yaml;
use std::collections::HashMap;

type Res<T> = Result<T, Diagnostic>;

/// The host's file reader: the bytes of a root-relative path, or `None` if it does not exist.
pub type Read<'a> = &'a mut dyn FnMut(&str) -> Res<Option<Vec<u8>>>;

#[derive(Clone, Copy)]
pub struct Options {
    pub profile: Profile,
    /// Keep ```calc blocks in the Markdown output (they are always evaluated and reported).
    pub keep_calc: bool,
    /// `compile` = true; `evaluate` = false (no export step, no Markdown).
    pub markdown: bool,
    pub imports: Limits,
    pub max_iterations: usize,
}

impl Default for Options {
    fn default() -> Self {
        Options { profile: Profile::CommonMark, keep_calc: false, markdown: true, imports: Limits::default(), max_iterations: 10_000 }
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

/// A string that looks like a variable FILE (`./data/project.yaml`): E8.1b reads those. Refusing
/// them now means their meaning cannot silently change when that lands.
fn looks_like_file(s: &str) -> bool {
    let lower = s.to_ascii_lowercase();
    (s.starts_with("./") || s.starts_with("../") || s.starts_with('/'))
        && [".yaml", ".yml", ".json"].iter().any(|e| lower.ends_with(e))
}

/// The interpolation scope (step 3) and the calc inputs (G11) from the effective metadata.
fn scope_and_inputs(meta: &Yaml) -> Res<(Yaml, HashMap<String, Value>)> {
    let vars: Vec<(String, Yaml)> = match get(meta, "variables") {
        None | Some(Yaml::Null) => Vec::new(),
        Some(Yaml::Map(e)) => e.clone(),
        Some(_) => return Err(meta_err("variables must be a mapping")),
    };
    let mut scope = vec![("document".to_string(), meta.clone())];
    let mut calc_in = HashMap::new();
    for (k, v) in &vars {
        if RESERVED.contains(&k.as_str()) {
            return Err(meta_err(format!("variables.{} uses a reserved namespace ({})", k, RESERVED.join(", "))));
        }
        if let Yaml::Str(s) = v {
            if looks_like_file(s) {
                return Err(Diagnostic::new(
                    Code::UnsupportedFeature,
                    format!("variables.{}: variable files are not supported yet", k),
                ));
            }
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
pub fn compile(entry: &str, read: Read<'_>, inputs: &Yaml, opts: &Options) -> Res<Compiled> {
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
    let top = match inputs {
        Yaml::Map(e) if !e.is_empty() => Some(Yaml::Map(vec![("variables".to_string(), inputs.clone())])),
        _ => None,
    };
    let merged = cascade_with_top(&cas, top.as_ref().map(|t| ("<inputs>", t)))?;
    check_versions(&merged.value)?;
    let (scope, calc_in) = scope_and_inputs(&merged.value)?;

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
    let fx = FxContext::empty();
    let mut blocks: Vec<CalcBlock> = Vec::new();
    let mut exported: HashMap<String, (String, u32)> = HashMap::new();
    let mut calc_ns: Vec<(String, Yaml)> = Vec::new();
    for f in &fences {
        let source = lines_text(&c, &starts, &ends, f.a, f.b);
        let (path, open_line) = locate(&ex, f.open as u32 + 1).unwrap_or((entry.clone(), f.open as u32 + 1));
        let line = exact_line(originals.get(&path), &source).unwrap_or(open_line);
        let results = evaluate_source(&source, line + 1, &calc_in, &fx).map_err(|d| in_file(d, &path))?;
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
        let out = export_markdown(&text3, opts.profile, &[], 1).map_err(|d| remap(&ex, d))?;
        Some(if opts.keep_calc { out } else { remove_calc_blocks(&out)? })
    } else {
        None
    };
    Ok(Compiled { markdown, metadata: merged.value, provenance: merged.provenance, calc: blocks })
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
