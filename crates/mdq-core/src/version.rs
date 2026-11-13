//! Versions and feature profiles (spec feature F01).

use crate::diag::{Code, Diagnostic};

pub const SPEC_VERSION: &str = "0.1.0-draft";
/// Value accepted for the `mdq:` metadata key and the manifest `version`.
pub const SYNTAX_VERSION: &str = "0.1";
pub const MANIFEST_VERSION: &str = "0.1";
pub const UNIT_CATALOGUE_VERSION: &str = "0.1.0-draft";

/// Every profile the spec defines.
pub const KNOWN_PROFILES: [&str; 4] = ["core", "rich", "calc", "package"];

/// Profiles this build actually implements. Grows as epics land: `package` since E7.2;
/// core, rich and calc are added when `compile` exists (E8.1).
static SUPPORTED_PROFILES: [&str; 1] = ["package"];

pub fn supported_profiles() -> &'static [&'static str] {
    &SUPPORTED_PROFILES
}

/// Fail if a document requires a syntax version or profile we cannot honour.
/// Spec: unsupported *required* features must fail, never silently disappear.
pub fn check_required(profiles: &[String], version: &str) -> Result<(), Diagnostic> {
    if version != SYNTAX_VERSION {
        return Err(Diagnostic::new(
            Code::UnsupportedFeature,
            format!("unsupported MDQ version {version:?}; this build supports {SYNTAX_VERSION:?}"),
        ));
    }
    for p in profiles {
        if !KNOWN_PROFILES.contains(&p.as_str()) {
            return Err(Diagnostic::new(
                Code::UnsupportedFeature,
                format!("unknown profile {p:?}"),
            ));
        }
        if !SUPPORTED_PROFILES.contains(&p.as_str()) {
            return Err(Diagnostic::new(
                Code::UnsupportedFeature,
                format!("profile {p:?} is not supported by this build"),
            ));
        }
    }
    Ok(())
}
