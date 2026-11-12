//! Frontmatter splitting (spec section 5; decisions G5/G22 in docs/TEST_PLAN.md).
//!
//! * A document has frontmatter iff its FIRST line is `---` (trailing spaces or
//!   tabs allowed). A leading thematic break must therefore be written another
//!   way (`***`, `___`, `- - -`). This settles the ambiguity listed in spec 12.
//! * The block ends at the next line that is `---` (same rule). The YAML inside
//!   is not parsed here, so a `---` inside a YAML block scalar still closes it.
//! * Opened but never closed is MDQ_METADATA_ERROR (reported at line 1).
//! * A leading U+FEFF is ignored and never appears in the returned body.
//! * Line ends are `\n` or `\r\n`; a lone `\r` does not end a line.
//! * The returned frontmatter has `\n` line ends; the body is verbatim.
//! * Whether the content is a mapping is checked by the YAML loader (E2.2).

use crate::diag::{Code, Diagnostic};

type Res<T> = Result<T, Diagnostic>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Split<'a> {
    /// The text between the fences (no fence lines), or `None` when absent.
    pub frontmatter: Option<String>,
    /// 1-based line of the first frontmatter content line (2); 0 when absent.
    pub frontmatter_line: u32,
    /// Everything after the closing fence line (the whole text when absent).
    pub body: &'a str,
    /// 1-based line of the first body line.
    pub body_line: u32,
}

/// The line starting at byte `pos`: (content without its terminator, next pos).
fn next_line(s: &str, pos: usize) -> Option<(&str, usize)> {
    if pos >= s.len() {
        return None;
    }
    let rest = &s[pos..];
    match rest.find('\n') {
        Some(i) => {
            let line = &rest[..i];
            Some((line.strip_suffix('\r').unwrap_or(line), pos + i + 1))
        }
        None => Some((rest, s.len())),
    }
}

fn is_fence(line: &str) -> bool {
    line.strip_prefix("---")
        .map_or(false, |rest| rest.chars().all(|c| c == ' ' || c == '\t'))
}

pub fn split(text: &str) -> Res<Split<'_>> {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let absent = Split { frontmatter: None, frontmatter_line: 0, body: text, body_line: 1 };
    let Some((first, mut pos)) = next_line(text, 0) else {
        return Ok(absent);
    };
    if !is_fence(first) {
        return Ok(absent);
    }
    let mut lines: Vec<&str> = Vec::new();
    let mut line_no: u32 = 1;
    while let Some((line, next)) = next_line(text, pos) {
        line_no += 1;
        if is_fence(line) {
            return Ok(Split {
                frontmatter: Some(lines.join("\n")),
                frontmatter_line: 2,
                body: &text[next..],
                body_line: line_no + 1,
            });
        }
        lines.push(line);
        pos = next;
    }
    Err(Diagnostic::new(
        Code::MetadataError,
        "unterminated frontmatter: the `---` on line 1 has no closing `---` line \
         (write a leading thematic break as `***`)",
    )
    .at_position(1, 1))
}
