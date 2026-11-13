//! `export_markdown`: evaluated document body to portable Markdown (spec sections 7 and 9; G41 in
//! docs/TEST_PLAN.md). Pipeline: details, columns, generic containers, callouts, highlights,
//! citations, maths (profile `commonmark` only), footnote check, TOC.

use crate::blocks::{line_bounds, parse, Kind};
use crate::cite::{citation_resolve, Format};
use crate::containers::{container_parse, Container};
use crate::diag::{Code, Diagnostic};
use crate::imports::find_embeds;
use crate::notes::{footnote_check, math_fallback};
use crate::rich::{
    callout_fallback, columns_fallback, details_fallback, generic_fallback, highlight_fallback, leaf_text, toc_build,
};
use crate::vars::directives;

type Res<T> = Result<T, Diagnostic>;

const MAX_INPUT: usize = 16 * 1024 * 1024;

#[derive(Clone, Copy, PartialEq)]
pub enum Profile {
    CommonMark,
    Gfm,
}

impl Profile {
    pub fn parse(s: &str) -> Option<Profile> {
        match s {
            "commonmark" => Some(Profile::CommonMark),
            "gfm" => Some(Profile::Gfm),
            _ => None,
        }
    }
}

fn unsupported(msg: String, line: u32, col: u32) -> Diagnostic {
    Diagnostic::new(Code::UnsupportedFeature, msg).at_position(line, col)
}

fn check_containers(kids: &[Container]) -> Res<()> {
    for k in kids {
        if k.name == "if" || k.name == "for" {
            return Err(unsupported(
                format!("unevaluated container :::{}: evaluate conditionals and loops before exporting", k.name),
                k.line,
                1,
            ));
        }
        let resolved = matches!(k.name.as_str(), "details" | "columns" | "column");
        let optional = k.attrs.iter().rev().find(|(a, _)| a == "optional").map(|x| x.1.as_str()) == Some("true");
        if !resolved && !optional {
            return Err(unsupported(
                format!("container :::{} has no handler (add optional=\"true\" to keep its body)", k.name),
                k.line,
                1,
            ));
        }
        check_containers(&k.children)?;
    }
    Ok(())
}

pub fn export_markdown(text: &str, profile: Profile, bibs: &[(Format, String)], first_line: u32) -> Res<String> {
    if text.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    if let Some(d) = directives(text, first_line)?.first() {
        return Err(unsupported(
            format!("unevaluated variable {{{{{}}}}}: evaluate variables before exporting", d.name),
            d.line,
            d.column,
        ));
    }
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    for leaf in parse(&c, &starts, &ends, 1)? {
        if !matches!(leaf.kind, Kind::Paragraph | Kind::Heading) {
            continue;
        }
        let (s, origin) = leaf_text(&c, &leaf.segments);
        if let Some((st, _, _)) = find_embeds(&s).first() {
            let off = origin[*st];
            let li = starts.partition_point(|&x| x <= off) - 1;
            return Err(unsupported(
                "unexpanded import ![[...]]: expand imports before exporting".to_string(),
                first_line + li as u32,
                (off - starts[li] + 1) as u32,
            ));
        }
    }
    check_containers(&container_parse(text, first_line)?)?;
    // error pass on the original text, so diagnostics carry original line numbers
    callout_fallback(text, first_line)?;
    highlight_fallback(text, first_line)?;
    if profile == Profile::CommonMark {
        math_fallback(text, first_line)?;
    }
    citation_resolve(text, bibs, first_line)?;
    footnote_check(text, first_line)?;
    toc_build(text, first_line)?;
    let mut t = details_fallback(text, first_line)?;
    t = columns_fallback(&t, first_line)?;
    t = generic_fallback(&t, first_line)?;
    t = callout_fallback(&t, first_line)?;
    t = highlight_fallback(&t, first_line)?;
    t = citation_resolve(&t, bibs, first_line)?;
    if profile == Profile::CommonMark {
        t = math_fallback(&t, first_line)?;
    }
    footnote_check(&t, first_line)?;
    toc_build(&t, first_line)
}
