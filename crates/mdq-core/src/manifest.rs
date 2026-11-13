//! Package manifest and package opening (package.md; E7.2, G48).
//!
//! `manifest_parse` checks the package keys of `manifest.yaml` (`format`, `version`, `entrypoint`,
//! `features`, `security`, `requirements`) and hands every other key back untouched: the manifest
//! shares one metadata schema with frontmatter and sidecars, and those keys enter the cascade
//! later (E8.1). `package_open` = `zip_read` + `manifest_parse` + entrypoint checks.
//!
//! Nothing here can grant a permission: `security` and `requirements` become a REQUEST in the
//! shape of the E6.4 document layer, to be intersected with trusted policy by the host.
//! Opening a package never fetches anything (there is no transport parameter at all).

use crate::diag::{Code, Diagnostic};
use crate::paths;
use crate::policy::{self, PVal};
use crate::utf8;
use crate::version::{KNOWN_PROFILES, MANIFEST_VERSION};
use crate::yaml::{self, Yaml};
use crate::zip::{self, ZipLimits};

type Res<T> = Result<T, Diagnostic>;

pub const MANIFEST_NAME: &str = "manifest.yaml";

fn meta_err(msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::MetadataError, msg)
}

/// The network request of a package, in E6.4 document-layer form.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Requests {
    /// `requirements.network`: none | optional | required (absent = optional).
    pub network: String,
    /// `requirements.hosts`, as written (None = no host request).
    pub hosts: Option<Vec<String>>,
    /// `security.network`: request | none (`deny` is read as `none`).
    pub security_network: String,
}

/// A checked manifest.
#[derive(Debug, Clone, PartialEq)]
pub struct Manifest {
    pub format: String,
    pub version: String,
    /// Root-relative, normalised (no `.`/`..`).
    pub entrypoint: String,
    pub features: Vec<String>,
    pub requests: Requests,
    /// Every other top-level key, in document order, untouched.
    pub metadata: Vec<(String, Yaml)>,
}

/// Look up a key in a YAML mapping.
fn get<'a>(m: &'a [(String, Yaml)], k: &str) -> Option<&'a Yaml> {
    m.iter().find(|(n, _)| n == k).map(|(_, v)| v)
}

/// Convert YAML to the policy value type, so E6.4 can validate `requirements` with its own rules.
fn to_pval(v: &Yaml) -> PVal {
    match v {
        Yaml::Null => PVal::None,
        Yaml::Bool(b) => PVal::Bool(*b),
        // Numbers are canonical decimal text; only whole numbers that fit in i64 are ints.
        Yaml::Number(n) => n.parse::<i64>().map(PVal::Int).unwrap_or(PVal::Other),
        Yaml::Str(s) => PVal::Str(s.clone()),
        Yaml::Seq(items) => PVal::List(items.iter().map(to_pval).collect()),
        Yaml::Map(entries) => PVal::Map(entries.iter().map(|(k, v)| (k.clone(), to_pval(v))).collect()),
    }
}

fn parse_security(v: Option<&Yaml>) -> Res<String> {
    let entries = match v {
        None | Some(Yaml::Null) => return Ok("request".to_string()),
        Some(Yaml::Map(e)) => e,
        Some(_) => return Err(meta_err("security must be a mapping")),
    };
    let mut network = "request".to_string();
    for (k, v) in entries {
        match (k.as_str(), v) {
            // Both spellings: sources-and-permissions.md uses request|none, package.md uses deny.
            ("network", Yaml::Str(s)) if s == "request" => network = "request".to_string(),
            ("network", Yaml::Str(s)) if s == "none" || s == "deny" => network = "none".to_string(),
            ("network", _) => return Err(meta_err("security.network must be one of request, none, deny")),
            // Remote imports are disabled by default and not implemented: `false` restates the
            // default; `true` asks for something this build cannot do.
            ("remote_imports", Yaml::Bool(false)) => {}
            ("remote_imports", Yaml::Bool(true)) => {
                return Err(Diagnostic::new(Code::UnsupportedFeature, "remote imports are not supported"))
            }
            ("remote_imports", _) => return Err(meta_err("security.remote_imports must be a boolean")),
            _ => return Err(meta_err(format!("unknown security key {:?}", k))),
        }
    }
    Ok(network)
}

fn parse_requirements(v: Option<&Yaml>, security_network: &str) -> Res<Requests> {
    let req = match v {
        None | Some(Yaml::Null) => Vec::new(),
        Some(Yaml::Map(e)) => e.clone(),
        Some(_) => return Err(meta_err("requirements must be a mapping")),
    };
    // Validate with the E6.4 rules by building the document layer and intersecting it with
    // nothing (no trusted layer, so the result is always "deny": only the checks matter).
    let req_p: Vec<(String, PVal)> = req.iter().map(|(k, v)| (k.clone(), to_pval(v))).collect();
    let doc = vec![
        ("requirements".to_string(), PVal::Map(req_p)),
        ("security".to_string(), PVal::Map(vec![("network".to_string(), PVal::Str(security_network.to_string()))])),
    ];
    policy::policy_intersect(None, None, Some(&doc))?;
    let network = match get(&req, "network") {
        Some(Yaml::Str(s)) => s.clone(),
        _ => "optional".to_string(),
    };
    let hosts = match get(&req, "hosts") {
        // E6.4 validation above already rejected non-string entries, so `filter_map` drops nothing.
        Some(Yaml::Seq(items)) => Some(
            items
                .iter()
                .filter_map(|i| match i {
                    Yaml::Str(s) => Some(s.clone()),
                    _ => None,
                })
                .collect(),
        ),
        _ => None,
    };
    Ok(Requests { network, hosts, security_network: security_network.to_string() })
}

/// Check the text of `manifest.yaml`. Diagnostics carry the YAML line where the loader gives one.
pub fn manifest_parse(text: &str) -> Res<Manifest> {
    let entries = match yaml::load(text, true, 1)? {
        Yaml::Map(e) => e,
        _ => return Err(meta_err("the manifest must be a mapping")),
    };
    let req_str = |k: &str| -> Res<&str> {
        match get(&entries, k) {
            None => Err(meta_err(format!("the manifest has no '{}'", k))),
            Some(Yaml::Str(s)) => Ok(s.as_str()),
            Some(Yaml::Number(_)) if k == "version" => Err(meta_err("version must be a quoted string, for example '0.1'")),
            Some(_) => Err(meta_err(format!("'{}' must be a string", k))),
        }
    };
    let format = req_str("format")?;
    if format != "mdq" {
        return Err(Diagnostic::new(Code::UnsupportedFeature, "the manifest format is not 'mdq'"));
    }
    let version = req_str("version")?;
    if version != MANIFEST_VERSION {
        return Err(Diagnostic::new(
            Code::UnsupportedFeature,
            format!("unsupported package version; this build supports {:?}", MANIFEST_VERSION),
        ));
    }
    let ep = req_str("entrypoint")?;
    // G32 path rules: absolute, scheme/drive and `..` out of the root are MDQ_SECURITY_DENIED.
    let entrypoint = paths::path_normalise("", ep)?;
    if !entrypoint.ends_with(".md") {
        return Err(meta_err("the entrypoint must be a Markdown file (.md)"));
    }

    let features = match get(&entries, "features") {
        None => return Err(meta_err("the manifest has no 'features'")),
        Some(Yaml::Seq(items)) => {
            let mut out: Vec<String> = Vec::new();
            for i in items {
                let Yaml::Str(s) = i else {
                    return Err(meta_err("features must be a list of strings"));
                };
                if !KNOWN_PROFILES.contains(&s.as_str()) {
                    return Err(Diagnostic::new(Code::UnsupportedFeature, format!("unknown profile {:?}", s)));
                }
                if out.contains(s) {
                    return Err(meta_err(format!("profile {:?} is listed twice", s)));
                }
                out.push(s.clone());
            }
            out
        }
        Some(_) => return Err(meta_err("features must be a list of strings")),
    };
    if !features.iter().any(|f| f == "package") {
        return Err(meta_err("features must include 'package'"));
    }

    let security_network = parse_security(get(&entries, "security"))?;
    let requests = parse_requirements(get(&entries, "requirements"), &security_network)?;

    const PACKAGE_KEYS: [&str; 6] = ["format", "version", "entrypoint", "features", "security", "requirements"];
    let metadata = entries.iter().filter(|(k, _)| !PACKAGE_KEYS.contains(&k.as_str())).cloned().collect();
    Ok(Manifest {
        format: format.to_string(),
        version: version.to_string(),
        entrypoint,
        features,
        requests,
        metadata,
    })
}

/// An opened package: the checked manifest plus every file and directory of the archive.
#[derive(Debug, Clone, PartialEq)]
pub struct Package {
    pub manifest: Manifest,
    /// The archive member name of the entrypoint (as stored in the archive).
    pub entrypoint_member: String,
    pub files: Vec<(String, Vec<u8>)>,
    pub directories: Vec<String>,
}

/// Give a diagnostic a file path if it has none yet.
fn in_file(mut d: Diagnostic, path: &str) -> Diagnostic {
    if d.path.is_none() {
        d.path = Some(path.to_string());
    }
    d
}

/// Open a `.mdq` package from its bytes.
pub fn package_open(data: &[u8], lim: &ZipLimits) -> Res<Package> {
    let archive = zip::zip_read(data, lim)?;
    let manifest_member = archive
        .members
        .iter()
        .find(|m| m.name == MANIFEST_NAME)
        .ok_or_else(|| meta_err("the package has no root manifest.yaml"))?;
    let text = utf8::decode(&manifest_member.data)
        .map_err(|d| in_file(Diagnostic { code: Code::MetadataError, ..d }, MANIFEST_NAME))?;
    let manifest = manifest_parse(&text).map_err(|d| in_file(d, MANIFEST_NAME))?;

    // Member names are unique in NFC (E7.1), so comparing NFC forms finds at most one.
    let want = paths::path_identity(&manifest.entrypoint);
    let entry = archive
        .members
        .iter()
        .find(|m| paths::path_identity(&m.name) == want)
        .ok_or_else(|| in_file(Diagnostic::new(Code::MissingImport, "the entrypoint is not in the package"), MANIFEST_NAME))?;
    // The entrypoint must be valid input (G28: strict UTF-8, MDQ_INVALID_SYNTAX at the bad byte).
    utf8::decode(&entry.data).map_err(|d| in_file(d, &entry.name))?;

    let entrypoint_member = entry.name.clone();
    Ok(Package {
        manifest,
        entrypoint_member,
        files: archive.members.into_iter().map(|m| (m.name, m.data)).collect(),
        directories: archive.directories,
    })
}
