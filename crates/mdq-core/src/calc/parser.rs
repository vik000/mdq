//! Parser for `calc` blocks (spec: extended-markdown.md sections 8.1, 8.5).
//!
//! A hand-written recursive-descent parser, one function per grammar rule:
//!
//! ```text
//! assignment = identifier "=" expression newline
//! expression = sum [ "to" unit ]          (conversion binds weakest)
//! sum        = product { ("+"|"-") product }
//! product    = unary { ("*"|"/") unary }
//! unary      = { "+" | "-" } postfix
//! postfix    = atom [ "%" ]
//! atom       = number [ unit ] | identifier | "(" expression ")"
//! ```
//!
//! The parser only checks SYNTAX (plus duplicate assignment names). Types,
//! units, scopes and forward references are the evaluator's job (E1.5).
//!
//! Decisions where the spec is silent or ambiguous (see docs/TEST_PLAN.md):
//! * `x%` and `(expr)%` parse; the evaluator must then require a dimensionless
//!   operand (G7). `%` after a number WITH a unit (`2 kg%`) is a syntax error;
//! * nesting is limited (G18) so hostile input cannot overflow the stack:
//!   at most `MAX_NESTING` open parentheses/unary signs, and an expression
//!   tree at most `MAX_DEPTH` deep (`MDQ_RESOURCE_LIMIT`).

use std::collections::HashMap;

use crate::calc::lexer::{tokenize, Token, TokenKind};
use crate::diag::{Code, Diagnostic};

type Res<T> = Result<T, Diagnostic>;

/// Most parentheses plus unary signs that may be open at once.
pub const MAX_NESTING: u32 = 200;
/// Deepest expression tree allowed (a long `1+1+1+...` chain counts too).
pub const MAX_DEPTH: u32 = 500;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UnOp {
    Plus,
    Minus,
}

impl UnOp {
    pub fn as_str(self) -> &'static str {
        match self {
            UnOp::Plus => "+",
            UnOp::Minus => "-",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BinOp {
    Add,
    Sub,
    Mul,
    Div,
}

impl BinOp {
    pub fn as_str(self) -> &'static str {
        match self {
            BinOp::Add => "+",
            BinOp::Sub => "-",
            BinOp::Mul => "*",
            BinOp::Div => "/",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExprKind {
    /// A plain number, text as written.
    Number(String),
    /// `number unit` or `number CUR`.
    Quantity { number: String, unit: String, currency: bool },
    Ident(String),
    Unary { op: UnOp, operand: Box<Expr> },
    /// Postfix `%`.
    Percent(Box<Expr>),
    Binary { op: BinOp, lhs: Box<Expr>, rhs: Box<Expr> },
    /// `operand to unit`.
    Convert { operand: Box<Expr>, unit: String, currency: bool },
}

/// An expression node. `line`/`col` point at the token that best locates
/// errors: the number/name, the unary sign, the `%`, the binary operator, or `to`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Expr {
    pub kind: ExprKind,
    pub line: u32,
    pub col: u32,
    depth: u32,
}

impl Expr {
    /// Build a node, computing its depth and enforcing `MAX_DEPTH`.
    pub fn new(kind: ExprKind, line: u32, col: u32) -> Res<Expr> {
        let child_depth = match &kind {
            ExprKind::Number(_) | ExprKind::Quantity { .. } | ExprKind::Ident(_) => 0,
            ExprKind::Unary { operand, .. } => operand.depth,
            ExprKind::Percent(operand) => operand.depth,
            ExprKind::Convert { operand, .. } => operand.depth,
            ExprKind::Binary { lhs, rhs, .. } => lhs.depth.max(rhs.depth),
        };
        let depth = child_depth + 1;
        if depth > MAX_DEPTH {
            return Err(Diagnostic::new(
                Code::ResourceLimit,
                format!("expression is too deeply nested (limit {MAX_DEPTH})"),
            )
            .at_position(line, col));
        }
        Ok(Expr { kind, line, col, depth })
    }

    pub fn depth(&self) -> u32 {
        self.depth
    }
}

/// One `name = expression` line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Assignment {
    pub name: String,
    pub line: u32,
    pub col: u32,
    pub expr: Expr,
}

fn perr(tok: &Token, message: impl Into<String>) -> Diagnostic {
    Diagnostic::new(Code::CalcError, message).at_position(tok.line, tok.col)
}

/// Human-readable name of a token for error messages.
fn describe(t: &Token) -> String {
    match t.kind {
        TokenKind::Newline => "end of line".to_string(),
        TokenKind::Number => format!("number {}", t.text),
        TokenKind::Ident => format!("name `{}`", t.text),
        TokenKind::Unit => format!("unit `{}`", t.text),
        TokenKind::Currency => format!("currency `{}`", t.text),
        TokenKind::To => "`to`".to_string(),
        _ => format!("`{}`", t.text),
    }
}

struct Parser {
    toks: Vec<Token>,
    pos: usize,
    nest: u32,
}

impl Parser {
    /// The current token. The last token of every line is a Newline and the
    /// parser never moves past a Newline except to finish a statement, so
    /// this is always a valid index; `min` is only a safety net.
    fn peek(&self) -> &Token {
        &self.toks[self.pos.min(self.toks.len() - 1)]
    }

    fn peek_kind(&self) -> TokenKind {
        self.peek().kind
    }

    /// Return the current token and advance.
    fn next(&mut self) -> Token {
        let t = self.peek().clone();
        if self.pos < self.toks.len() {
            self.pos += 1;
        }
        t
    }

    fn enter(&mut self) -> Res<()> {
        self.nest += 1;
        if self.nest > MAX_NESTING {
            let t = self.peek().clone();
            return Err(Diagnostic::new(
                Code::ResourceLimit,
                format!("expression is nested too deeply (limit {MAX_NESTING})"),
            )
            .at_position(t.line, t.col));
        }
        Ok(())
    }

    fn leave(&mut self) {
        self.nest -= 1;
    }

    fn parse_statement(&mut self) -> Res<Assignment> {
        self.nest = 0;
        let name_tok = self.next();
        match name_tok.kind {
            TokenKind::Ident => {}
            TokenKind::To => {
                return Err(perr(&name_tok, "`to` is a reserved word and cannot be used as a name"));
            }
            _ => {
                return Err(perr(
                    &name_tok,
                    format!("expected a name at the start of a statement, found {}", describe(&name_tok)),
                ));
            }
        }
        let eq = self.peek().clone();
        if eq.kind != TokenKind::Equals {
            return Err(perr(
                &eq,
                format!("expected `=` after `{}`, found {}", name_tok.text, describe(&eq)),
            ));
        }
        self.next();
        let expr = self.parse_expression()?;
        let end = self.peek().clone();
        if end.kind != TokenKind::Newline {
            let hint = match end.kind {
                TokenKind::Ident | TokenKind::Number | TokenKind::LParen => {
                    " (implicit multiplication is not supported)"
                }
                _ => "",
            };
            return Err(perr(&end, format!("unexpected {} after the expression{}", describe(&end), hint)));
        }
        self.next();
        Ok(Assignment { name: name_tok.text, line: name_tok.line, col: name_tok.col, expr })
    }

    fn parse_expression(&mut self) -> Res<Expr> {
        self.enter()?;
        let operand = self.parse_sum()?;
        let result = if self.peek_kind() == TokenKind::To {
            let to_tok = self.next();
            let unit_tok = self.peek().clone();
            let currency = match unit_tok.kind {
                TokenKind::Unit => false,
                TokenKind::Currency => true,
                _ => {
                    return Err(perr(
                        &unit_tok,
                        format!("expected a unit after `to`, found {}", describe(&unit_tok)),
                    ));
                }
            };
            self.next();
            let converted = Expr::new(
                ExprKind::Convert { operand: Box::new(operand), unit: unit_tok.text, currency },
                to_tok.line,
                to_tok.col,
            )?;
            if self.peek_kind() == TokenKind::To {
                let t = self.peek().clone();
                return Err(perr(
                    &t,
                    "chained conversions are not allowed; use parentheses or a second assignment",
                ));
            }
            converted
        } else {
            operand
        };
        self.leave();
        Ok(result)
    }

    fn parse_sum(&mut self) -> Res<Expr> {
        let mut lhs = self.parse_product()?;
        loop {
            let op = match self.peek_kind() {
                TokenKind::Plus => BinOp::Add,
                TokenKind::Minus => BinOp::Sub,
                _ => break,
            };
            let tok = self.next();
            let rhs = self.parse_product()?;
            lhs = Expr::new(
                ExprKind::Binary { op, lhs: Box::new(lhs), rhs: Box::new(rhs) },
                tok.line,
                tok.col,
            )?;
        }
        Ok(lhs)
    }

    fn parse_product(&mut self) -> Res<Expr> {
        let mut lhs = self.parse_unary()?;
        loop {
            let op = match self.peek_kind() {
                TokenKind::Star => BinOp::Mul,
                TokenKind::Slash => BinOp::Div,
                _ => break,
            };
            let tok = self.next();
            let rhs = self.parse_unary()?;
            lhs = Expr::new(
                ExprKind::Binary { op, lhs: Box::new(lhs), rhs: Box::new(rhs) },
                tok.line,
                tok.col,
            )?;
        }
        Ok(lhs)
    }

    fn parse_unary(&mut self) -> Res<Expr> {
        let op = match self.peek_kind() {
            TokenKind::Plus => UnOp::Plus,
            TokenKind::Minus => UnOp::Minus,
            _ => return self.parse_postfix(),
        };
        let tok = self.next();
        self.enter()?;
        let operand = self.parse_unary()?;
        self.leave();
        Expr::new(ExprKind::Unary { op, operand: Box::new(operand) }, tok.line, tok.col)
    }

    fn parse_postfix(&mut self) -> Res<Expr> {
        let (atom, bare_quantity) = self.parse_atom()?;
        if self.peek_kind() != TokenKind::Percent {
            return Ok(atom);
        }
        let pct = self.next();
        if bare_quantity {
            return Err(perr(&pct, "`%` cannot be applied to a quantity with a unit"));
        }
        if self.peek_kind() == TokenKind::Percent {
            let t = self.peek().clone();
            return Err(perr(&t, "`%` cannot be applied twice"));
        }
        Expr::new(ExprKind::Percent(Box::new(atom)), pct.line, pct.col)
    }

    /// Returns the atom and whether it is a bare `number unit` quantity
    /// (not parenthesised), which `%` may not follow.
    fn parse_atom(&mut self) -> Res<(Expr, bool)> {
        let tok = self.peek().clone();
        match tok.kind {
            TokenKind::Number => {
                self.next();
                let u = self.peek().clone();
                if u.kind == TokenKind::Unit || u.kind == TokenKind::Currency {
                    self.next();
                    let e = Expr::new(
                        ExprKind::Quantity {
                            number: tok.text,
                            unit: u.text,
                            currency: u.kind == TokenKind::Currency,
                        },
                        tok.line,
                        tok.col,
                    )?;
                    Ok((e, true))
                } else {
                    Ok((Expr::new(ExprKind::Number(tok.text), tok.line, tok.col)?, false))
                }
            }
            TokenKind::Ident => {
                self.next();
                Ok((Expr::new(ExprKind::Ident(tok.text), tok.line, tok.col)?, false))
            }
            TokenKind::LParen => {
                self.next();
                let inner = self.parse_expression()?;
                let close = self.peek().clone();
                if close.kind != TokenKind::RParen {
                    return Err(perr(&close, format!("expected `)`, found {}", describe(&close))));
                }
                self.next();
                Ok((inner, false))
            }
            TokenKind::Equals => Err(perr(
                &tok,
                "`=` is not allowed in an expression: comparisons and assignment expressions are not supported",
            )),
            TokenKind::To => Err(perr(&tok, "expected an expression, found `to` (`to` is a reserved word)")),
            _ => Err(perr(&tok, format!("expected an expression, found {}", describe(&tok)))),
        }
    }
}

/// Parse a token stream (from `lexer::tokenize`) into assignments.
/// Duplicate names within the block are rejected here, at the second name.
pub fn parse(tokens: Vec<Token>) -> Res<Vec<Assignment>> {
    let mut p = Parser { toks: tokens, pos: 0, nest: 0 };
    let mut out: Vec<Assignment> = Vec::new();
    let mut seen: HashMap<String, u32> = HashMap::new();
    while p.pos < p.toks.len() {
        let a = p.parse_statement()?;
        if let Some(first_line) = seen.get(&a.name) {
            return Err(Diagnostic::new(
                Code::CalcError,
                format!("duplicate assignment to `{}` (first assigned on line {})", a.name, first_line),
            )
            .at_position(a.line, a.col));
        }
        seen.insert(a.name.clone(), a.line);
        out.push(a);
    }
    Ok(out)
}

/// Lex and parse a whole `calc` block.
pub fn parse_source(text: &str, first_line: u32) -> Res<Vec<Assignment>> {
    parse(tokenize(text, first_line)?)
}
