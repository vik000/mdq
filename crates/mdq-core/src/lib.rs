//! MDQ core. All logic lives here, in plain Rust with no PyO3 dependency.
//! The `mdq-python` crate only wraps these functions for Python.

pub mod calc;
pub mod decimal;
pub mod diag;
pub mod dim;
pub mod frontmatter;
pub mod fx;
pub mod meta;
pub mod units;
pub mod vars;
pub mod version;
pub mod yaml;
