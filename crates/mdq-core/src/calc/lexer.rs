//! Lexer for `calc` blocks (spec: extended-markdown.md section 8.5).
//!
//! Turns the text of a fenced `calc` block into tokens with 1-based line and
//! column positions (columns count characters, like a Python string index).
//!
//! Decisions taken here where the grammar is silent (logged in docs/TEST_PLAN.md):
//! * `to` is a reserved word and can never be an identifier;
//! * a number must be followed by whitespace or an operator, so `12miles`,
//!   `1e3`, `2x`, `1_000`, `1.` and `1.2.3` are all lexer errors;
//! * a unit is only recognised in *unit position*: after a number, or after `to`.
//!   Elsewhere `h`, `min`, `m` ... are ordinary identifiers;
//! * in unit position, a catalogue unit wins; otherwise exactly three ASCII
//!   capitals form a currency-shaped token (the code list is checked later,
//!   gap G16); anything else is an "unknown unit" error;
//! * only space and tab are whitespace; newline is LF or CRLF.

use crate::decimal::Decimal;
use crate::diag::{Code, Diagnostic};
use crate::units;

type Res<T> = Result<T, Diagnostic>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TokenKind {
    Number,
    Ident,
    Unit,
    Currency,
    To,
    Plus,
    Minus,
    Star,
    Slash,
    LParen,
    RParen,
    Percent,
    Equals,
    Newline,
}

impl TokenKind {
    pub fn as_str(self) -> &'static str {
        match self {
            TokenKind::Number => "number",
            TokenKind::Ident => "ident",
            TokenKind::Unit => "unit",
            TokenKind::Currency => "currency",
            TokenKind::To => "to",
            TokenKind::Plus => "plus",
            TokenKind::Minus => "minus",
            TokenKind::Star => "star",
            TokenKind::Slash => "slash",
            TokenKind::LParen => "lparen",
            TokenKind::RParen => "rparen",
            TokenKind::Percent => "percent",
            TokenKind::Equals => "equals",
            TokenKind::Newline => "newline",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Token {
    pub kind: TokenKind,
    /// Number: the text as written. Unit/currency: the symbol. Ident: the name.
    pub text: String,
    pub line: u32,
    pub col: u32,
}

fn err(message: impl Into<String>, line: u32, col: u32) -> Diagnostic {
    Diagnostic::new(Code::CalcError, message).at_position(line, col)
}

fn push(out: &mut Vec<Token>, kind: TokenKind, text: String, line: u32, col: u32) {
    out.push(Token { kind, text, line, col });
}

fn is_word_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// The maximal run of `[A-Za-z0-9_]` starting at `start`.
fn take_word(chars: &[char], start: usize) -> String {
    chars[start..].iter().take_while(|c| is_word_char(**c)).collect()
}

/// Three ASCII capitals, e.g. `EUR`. (Which codes are real is checked later.)
fn is_currency_shaped(word: &str) -> bool {
    word.len() == 3 && word.chars().all(|c| c.is_ascii_uppercase())
}

/// Tokenize a whole `calc` block. `first_line` is the number given to the
/// first line of `text` (so callers can report document line numbers).
///
/// Blank lines and full-line `#` comments produce no tokens. Every other
/// line ends with a `Newline` token, including a last line with no terminator.
pub fn tokenize(text: &str, first_line: u32) -> Res<Vec<Token>> {
    let mut tokens = Vec::new();
    let pieces: Vec<&str> = text.split('\n').collect(); // never empty
    let last = pieces.len() - 1;
    for (idx, piece) in pieces.iter().enumerate() {
        let line_no = first_line.saturating_add(idx as u32);
        // CRLF: drop the CR of a terminated line. A CR anywhere else is an error later.
        let line = if idx < last { piece.strip_suffix('\r').unwrap_or(piece) } else { piece };
        let trimmed = line.trim_start_matches(&[' ', '\t'][..]);
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue; // blank line or full-line comment
        }
        lex_line(line, line_no, &mut tokens)?;
        let col = line.chars().count() as u32 + 1;
        push(&mut tokens, TokenKind::Newline, "\n".to_string(), line_no, col);
    }
    Ok(tokens)
}

fn lex_line(line: &str, line_no: u32, out: &mut Vec<Token>) -> Res<()> {
    let chars: Vec<char> = line.chars().collect();
    let mut i = 0usize;
    let mut prev: Option<TokenKind> = None; // previous token on THIS line
    while i < chars.len() {
        let c = chars[i];
        if c == ' ' || c == '\t' {
            i += 1;
            continue;
        }
        let col = (i + 1) as u32;

        // ---- unit position: a letter right after a number or after `to` ----
        if c.is_ascii_alphabetic() && matches!(prev, Some(TokenKind::Number) | Some(TokenKind::To)) {
            let word = take_word(&chars, i);
            if prev == Some(TokenKind::Number) && word == "to" {
                push(out, TokenKind::To, word, line_no, col);
                i += 2;
                prev = Some(TokenKind::To);
                continue;
            }
            if let Some(unit) = units::match_at(line, i) {
                let len = unit.symbol.chars().count();
                push(out, TokenKind::Unit, unit.symbol.clone(), line_no, col);
                i += len;
                prev = Some(TokenKind::Unit);
                continue;
            }
            if is_currency_shaped(&word) {
                push(out, TokenKind::Currency, word, line_no, col);
                i += 3;
                prev = Some(TokenKind::Currency);
                continue;
            }
            return Err(err(format!("unknown unit {word:?}"), line_no, col));
        }

        // ---- numbers: 12, 12.5, .5 ----
        if c.is_ascii_digit() || (c == '.' && i + 1 < chars.len() && chars[i + 1].is_ascii_digit()) {
            let start = i;
            while i < chars.len() && chars[i].is_ascii_digit() {
                i += 1;
            }
            if i < chars.len() && chars[i] == '.' {
                if i + 1 < chars.len() && chars[i + 1].is_ascii_digit() {
                    i += 1;
                    while i < chars.len() && chars[i].is_ascii_digit() {
                        i += 1;
                    }
                } else {
                    return Err(err("invalid number: digits are required after the decimal point", line_no, col));
                }
            }
            if i < chars.len() && (is_word_char(chars[i]) || chars[i] == '.') {
                return Err(err(
                    "invalid number: a number must be followed by whitespace or an operator",
                    line_no,
                    col,
                ));
            }
            let text: String = chars[start..i].iter().collect();
            // Reuse the decimal parser for its length limit; keep its error code.
            Decimal::parse(&text).map_err(|mut d| {
                d.line = Some(line_no);
                d.column = Some(col);
                d
            })?;
            push(out, TokenKind::Number, text, line_no, col);
            prev = Some(TokenKind::Number);
            continue;
        }

        // ---- identifiers and the reserved word `to` ----
        if c.is_ascii_alphabetic() {
            let word = take_word(&chars, i);
            let len = word.chars().count();
            let kind = if word == "to" { TokenKind::To } else { TokenKind::Ident };
            push(out, kind, word, line_no, col);
            i += len;
            prev = Some(kind);
            continue;
        }

        // ---- single-character tokens ----
        let kind = match c {
            '+' => Some(TokenKind::Plus),
            '-' => Some(TokenKind::Minus),
            '*' => Some(TokenKind::Star),
            '/' => Some(TokenKind::Slash),
            '(' => Some(TokenKind::LParen),
            ')' => Some(TokenKind::RParen),
            '%' => Some(TokenKind::Percent),
            '=' => Some(TokenKind::Equals),
            _ => None,
        };
        if let Some(kind) = kind {
            push(out, kind, c.to_string(), line_no, col);
            i += 1;
            prev = Some(kind);
            continue;
        }

        // ---- anything else is an error, with a helpful message ----
        let message = match c {
            '#' => "inline comments are not supported; `#` starts a comment only at the start of a line".to_string(),
            '\r' => "a carriage return is only allowed as part of a CRLF line ending".to_string(),
            ';' => "`;` is not allowed: use one assignment per line".to_string(),
            '"' | '\'' => "strings are not supported in calc".to_string(),
            '^' => "exponentiation is not supported".to_string(),
            _ => format!("unexpected character {:?} (U+{:04X})", c, c as u32),
        };
        return Err(err(message, line_no, col));
    }
    Ok(())
}
