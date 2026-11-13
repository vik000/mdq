//! MDQ core. All logic lives here, in plain Rust with no PyO3 dependency.
//! The `mdq-python` crate only wraps these functions for Python.

pub mod blocks;
pub mod calc;
pub mod containers;
pub mod decimal;
pub mod diag;
pub mod dim;
pub mod entities;
pub mod frontmatter;
pub mod fx;
pub mod headings;
pub mod imports;
pub mod links;
pub mod meta;
pub mod paths;
pub mod refs;
pub mod unitab;
pub mod units;
pub mod utf8;
pub mod vars;
pub mod version;
pub mod yaml;
