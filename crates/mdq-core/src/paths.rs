//! Import paths (spec section 3; G32 in docs/TEST_PLAN.md).
//!
//! `path_normalise` resolves a reference against the importing file's directory without touching
//! the file system. `path_identity` is the NFC form used to compare paths. `path_confine`
//! resolves a root-relative path on disk and refuses anything that ends up outside the root.

use crate::diag::{Code, Diagnostic};
use std::path::PathBuf;
use unicode_normalization::UnicodeNormalization;

type Res<T> = Result<T, Diagnostic>;

const MAX_PATH: usize = 4096;
const MAX_SEGMENT: usize = 255;

fn e(code: Code, msg: impl Into<String>) -> Diagnostic {
    Diagnostic::new(code, msg)
}

fn has_scheme(p: &str) -> bool {
    let mut ch = p.chars();
    match ch.next() {
        Some(f) if f.is_ascii_alphabetic() => {}
        _ => return false,
    }
    for x in ch {
        if x == ':' {
            return true;
        }
        if !(x.is_ascii_alphanumeric() || x == '+' || x == '.' || x == '-') {
            return false;
        }
    }
    false
}

fn apply(stack: &[String], p: &str, what: &str) -> Res<Vec<String>> {
    if p.is_empty() {
        return Err(e(Code::InvalidSyntax, format!("{} is empty", what)));
    }
    if p.chars().any(|c| c < ' ' || c == '\u{7f}') {
        return Err(e(Code::InvalidSyntax, format!("{} contains a control character", what)));
    }
    if p.starts_with('/') {
        return Err(e(
            Code::SecurityDenied,
            format!("{} \"{}\" is absolute; only paths relative to the document are allowed", what, p),
        ));
    }
    if has_scheme(p) {
        return Err(e(
            Code::SecurityDenied,
            format!(
                "{} \"{}\" has a scheme or drive letter; only relative file paths are allowed (write ./ before a name that contains a colon)",
                what, p
            ),
        ));
    }
    if p.contains('\\') {
        return Err(e(
            Code::InvalidSyntax,
            format!("{} \"{}\" contains a backslash; use / as the separator", what, p),
        ));
    }
    if p.len() > MAX_PATH {
        return Err(e(Code::ResourceLimit, format!("{} is longer than {} bytes", what, MAX_PATH)));
    }
    let mut out: Vec<String> = stack.to_vec();
    let segs: Vec<&str> = p.split('/').collect();
    for (k, g) in segs.iter().enumerate() {
        if g.is_empty() {
            let msg = if k == segs.len() - 1 {
                format!("{} \"{}\" ends with / (names a directory)", what, p)
            } else {
                format!("{} \"{}\" has an empty segment (//)", what, p)
            };
            return Err(e(Code::InvalidSyntax, msg));
        }
        if g.len() > MAX_SEGMENT {
            return Err(e(Code::ResourceLimit, format!("a path segment is longer than {} bytes", MAX_SEGMENT)));
        }
        if *g == "." {
            continue;
        }
        if *g == ".." {
            if out.pop().is_none() {
                return Err(e(Code::SecurityDenied, format!("{} \"{}\" leaves the root", what, p)));
            }
        } else {
            out.push((*g).to_string());
        }
    }
    Ok(out)
}

/// A root-relative directory as segments ("" = root).
pub(crate) fn dir_segments(d: &str, what: &str) -> Res<Vec<String>> {
    if d.is_empty() {
        Ok(Vec::new())
    } else {
        apply(&[], d, what)
    }
}

/// Resolve `rel` against `base` (root-relative directory, "" = root). The result is a
/// root-relative path with `/` separators and no `.`/`..` segments.
pub fn path_normalise(base: &str, rel: &str) -> Res<String> {
    let start = if base.is_empty() { Vec::new() } else { apply(&[], base, "base directory")? };
    let stack = apply(&start, rel, "path")?;
    if stack.is_empty() {
        return Err(e(Code::InvalidSyntax, format!("path \"{}\" does not name a file", rel)));
    }
    Ok(stack.join("/"))
}

/// The NFC form of a path, for comparing paths.
pub fn path_identity(path: &str) -> String {
    path.nfc().collect()
}

/// Resolve the root-relative `rel` under the real directory `root`, following symlinks, and
/// return the canonical file path. Outside the root (including through a symlink) is
/// MDQ_SECURITY_DENIED; a missing file or a directory is MDQ_MISSING_IMPORT.
pub fn path_confine(root: &str, rel: &str) -> Res<String> {
    let norm = path_normalise("", rel)?;
    let real_root = std::fs::canonicalize(root)
        .map_err(|_| e(Code::SecurityDenied, "the import root is not an accessible directory"))?;
    if !real_root.is_dir() {
        return Err(e(Code::SecurityDenied, "the import root is not a directory"));
    }
    let mut tries: Vec<String> = vec![norm.clone()];
    let nfc: String = norm.nfc().collect();
    if !tries.contains(&nfc) {
        tries.push(nfc);
    }
    let nfd: String = norm.nfd().collect();
    if !tries.contains(&nfd) {
        tries.push(nfd);
    }
    let mut found: Option<PathBuf> = None;
    for cand in &tries {
        let mut p = real_root.clone();
        for seg in cand.split('/') {
            p.push(seg);
        }
        match std::fs::canonicalize(&p) {
            Ok(f) => {
                found = Some(f);
                break;
            }
            Err(err) if err.kind() == std::io::ErrorKind::PermissionDenied => {
                return Err(e(Code::SecurityDenied, format!("\"{}\" cannot be accessed", norm)));
            }
            Err(_) => continue,
        }
    }
    let found = match found {
        Some(f) => f,
        None => return Err(e(Code::MissingImport, format!("\"{}\" does not exist", norm))),
    };
    if !found.starts_with(&real_root) {
        return Err(e(Code::SecurityDenied, format!("\"{}\" resolves outside the import root", norm)));
    }
    if !found.is_file() {
        return Err(e(Code::MissingImport, format!("\"{}\" is not a file", norm)));
    }
    match found.to_str() {
        Some(s) => Ok(s.to_string()),
        None => Err(e(Code::InvalidSyntax, "the resolved path is not valid UTF-8")),
    }
}
