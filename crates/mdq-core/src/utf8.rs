//! UTF-8 input decoding (spec section 1: input MUST reject malformed UTF-8; G28).
//!
//! * Malformed input (invalid or overlong bytes, surrogates, values above
//!   U+10FFFF, truncated final sequence) is MDQ_INVALID_SYNTAX; the message
//!   names the byte offset, and line/column locate it in the decoded text.
//! * One leading BOM (U+FEFF) is dropped; any further U+FEFF is content.
//! * NUL (U+0000) is rejected (MDQ_INVALID_SYNTAX), not replaced.
//! * Line endings are NOT normalised here; later stages accept LF, CRLF, CR.
//! * Inputs above 16 MiB are MDQ_RESOURCE_LIMIT.

use crate::diag::{Code, Diagnostic};

pub const MAX_INPUT: usize = 16 * 1024 * 1024;

/// 1-based (line, column in chars) of the position just after `prefix`.
fn position_after(prefix: &str) -> (u32, u32) {
    let chars: Vec<char> = prefix.chars().collect();
    let mut line: u32 = 1;
    let mut col: u32 = 1;
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '\n' {
            line += 1;
            col = 1;
        } else if chars[i] == '\r' {
            if i + 1 < chars.len() && chars[i + 1] == '\n' {
                i += 1;
            }
            line += 1;
            col = 1;
        } else {
            col += 1;
        }
        i += 1;
    }
    (line, col)
}

pub fn decode(data: &[u8]) -> Result<String, Diagnostic> {
    if data.len() > MAX_INPUT {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let bom_len = if data.starts_with(&[0xEF, 0xBB, 0xBF]) { 3 } else { 0 };
    let text = match std::str::from_utf8(data) {
        Ok(t) => t,
        Err(e) => {
            let at = e.valid_up_to();
            let valid = std::str::from_utf8(&data[bom_len.min(at)..at]).unwrap_or("");
            let (line, col) = position_after(valid);
            let what = match e.error_len() {
                Some(n) => format!("invalid byte sequence ({} byte(s))", n),
                None => "truncated sequence at the end of the input".to_string(),
            };
            return Err(Diagnostic::new(
                Code::InvalidSyntax,
                format!("malformed UTF-8: {} at byte offset {}", what, at),
            )
            .at_position(line, col));
        }
    };
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    if let Some(i) = text.find('\0') {
        let (line, col) = position_after(&text[..i]);
        return Err(Diagnostic::new(
            Code::InvalidSyntax,
            format!("NUL character at byte offset {}", i + bom_len),
        )
        .at_position(line, col));
    }
    Ok(text.to_string())
}
