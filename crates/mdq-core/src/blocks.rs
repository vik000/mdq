//! Block structure of CommonMark documents (spec section 2: parse Markdown structure before
//! expanding directives; G27/G29 in docs/TEST_PLAN.md).
//!
//! A line-by-line port of the commonmark.js block algorithm (open-container matching, block
//! starts, lazy continuation) reduced to what directive handling needs: the LEAF blocks
//! (paragraph, heading, fenced/indented code, HTML block, thematic break), with the line range
//! of each and, for paragraphs and headings, the source ranges of the inline content lines with
//! container prefixes (`> `, list indent) removed. Block quotes and list items nest to any
//! depth up to `MAX_DEPTH`. Link reference definitions are ordinary paragraphs here.

use crate::diag::{Code, Diagnostic};
use crate::vars::{starts_with, tag_end};

pub const MAX_DEPTH: usize = 128;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Kind {
    Paragraph,
    Heading,
    CodeFence,
    CodeIndented,
    Html,
    ThematicBreak,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Paragraph => "paragraph",
            Kind::Heading => "heading",
            Kind::CodeFence => "code_fence",
            Kind::CodeIndented => "code_indented",
            Kind::Html => "html",
            Kind::ThematicBreak => "hr",
        }
    }
}

/// One leaf block. Lines are 0-based, `end_line` exclusive; segments are char ranges.
#[derive(Debug, Clone)]
pub struct Leaf {
    pub kind: Kind,
    pub first_line: usize,
    pub end_line: usize,
    pub segments: Vec<(usize, usize)>,
}

/// Start offsets and content end offsets (line terminator excluded) of every line.
/// LF, CRLF and lone CR end lines.
pub fn line_bounds(c: &[char]) -> (Vec<usize>, Vec<usize>) {
    let n = c.len();
    let mut starts = vec![0usize];
    let mut ends: Vec<usize> = Vec::new();
    let mut i = 0;
    while i < n {
        if c[i] == '\n' {
            ends.push(i);
            starts.push(i + 1);
        } else if c[i] == '\r' {
            ends.push(i);
            if i + 1 < n && c[i + 1] == '\n' {
                i += 1;
            }
            starts.push(i + 1);
        }
        i += 1;
    }
    ends.push(n);
    (starts, ends)
}

const HTML_BLOCK_NAMES: &[&str] = &[
    "address", "article", "aside", "base", "basefont", "blockquote", "body", "caption", "center", "col",
    "colgroup", "dd", "details", "dialog", "dir", "div", "dl", "dt", "fieldset", "figcaption", "figure",
    "footer", "form", "frame", "frameset", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hr",
    "html", "iframe", "legend", "li", "link", "main", "menu", "menuitem", "nav", "noframes", "ol",
    "optgroup", "option", "p", "param", "search", "section", "summary", "table", "tbody", "td", "tfoot",
    "th", "thead", "title", "tr", "track", "ul",
];

fn rest_blank(c: &[char], from: usize, e: usize) -> bool {
    c[from..e].iter().all(|&ch| ch == ' ' || ch == '\t')
}

/// Opening code fence at `fi`: (fence char, run length).
fn fence_start(c: &[char], fi: usize, e: usize) -> Option<(char, usize)> {
    let ch = c[fi];
    if ch != '`' && ch != '~' {
        return None;
    }
    let mut j = fi;
    while j < e && c[j] == ch {
        j += 1;
    }
    let len = j - fi;
    if len < 3 {
        return None;
    }
    if ch == '`' && c[j..e].contains(&'`') {
        return None;
    }
    Some((ch, len))
}

fn is_atx(c: &[char], fi: usize, e: usize) -> bool {
    let mut j = fi;
    while j < e && c[j] == '#' {
        j += 1;
    }
    let n = j - fi;
    n >= 1 && n <= 6 && (j == e || c[j] == ' ' || c[j] == '\t')
}

fn is_thematic(c: &[char], fi: usize, e: usize) -> bool {
    let ch = c[fi];
    if ch != '*' && ch != '-' && ch != '_' {
        return false;
    }
    let mut count = 0;
    for &x in &c[fi..e] {
        if x == ch {
            count += 1;
        } else if x != ' ' && x != '\t' {
            return false;
        }
    }
    count >= 3
}

fn name_matches(c: &[char], from: usize, e: usize, name: &str) -> bool {
    let mut k = from;
    for p in name.chars() {
        if k >= e || c[k].to_ascii_lowercase() != p {
            return false;
        }
        k += 1;
    }
    true
}

/// HTML block start kind (1..=7) of the line whose first non-blank char `<` is at `fi`.
fn html_start(c: &[char], fi: usize, e: usize) -> Option<u8> {
    for name in ["script", "pre", "style", "textarea"] {
        if name_matches(c, fi + 1, e, name) {
            let after = fi + 1 + name.len();
            if after >= e || c[after] == ' ' || c[after] == '\t' || c[after] == '>' {
                return Some(1);
            }
        }
    }
    if starts_with(c, fi, e, "<!--") {
        return Some(2);
    }
    if starts_with(c, fi, e, "<?") {
        return Some(3);
    }
    if starts_with(c, fi, e, "<![CDATA[") {
        return Some(5);
    }
    if starts_with(c, fi, e, "<!") && fi + 2 < e && c[fi + 2].is_ascii_alphabetic() {
        return Some(4);
    }
    let mut j = fi + 1;
    if j < e && c[j] == '/' {
        j += 1;
    }
    for name in HTML_BLOCK_NAMES.iter() {
        let name: &str = name;
        if name_matches(c, j, e, name) {
            let after = j + name.len();
            if after >= e
                || c[after] == ' '
                || c[after] == '\t'
                || c[after] == '>'
                || (c[after] == '/' && after + 1 < e && c[after + 1] == '>')
            {
                return Some(6);
            }
        }
    }
    if let Some(end) = tag_end(c, fi, e) {
        if rest_blank(c, end, e) {
            return Some(7);
        }
    }
    None
}

fn html_ends_on_line(c: &[char], s: usize, e: usize, kind: u8) -> bool {
    let lower: String = c[s..e].iter().collect::<String>().to_ascii_lowercase();
    match kind {
        1 => ["</script>", "</pre>", "</style>", "</textarea>"].iter().any(|n| lower.contains(*n)),
        2 => lower.contains("-->"),
        3 => lower.contains("?>"),
        4 => lower.contains('>'),
        5 => lower.contains("]]>"),
        _ => false,
    }
}

fn list_marker_len(c: &[char], i: usize, e: usize) -> (usize, Option<u64>) {
    if i < e && (c[i] == '*' || c[i] == '+' || c[i] == '-') {
        return (1, None);
    }
    let mut j = i;
    while j < e && c[j].is_ascii_digit() && j - i < 9 {
        j += 1;
    }
    if j > i && j < e && (c[j] == '.' || c[j] == ')') {
        let mut n: u64 = 0;
        for k in i..j {
            n = n * 10 + c[k].to_digit(10).unwrap_or(0) as u64;
        }
        return (j - i + 1, Some(n));
    }
    (0, None)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum NKind {
    Quote,
    Item,
    Para,
    Fence,
    Indented,
    Html,
}

fn is_leaf(k: NKind) -> bool {
    matches!(k, NKind::Para | NKind::Fence | NKind::Indented | NKind::Html)
}

struct Node {
    kind: NKind,
    first: usize,
    last: usize,
    last_nb: usize,
    segs: Vec<(usize, usize)>,
    has_child: bool,
    content_off: usize,
    fch: char,
    flen: usize,
    foff: usize,
    html: u8,
}

impl Node {
    fn new(kind: NKind, first: usize) -> Node {
        Node {
            kind,
            first,
            last: first,
            last_nb: first,
            segs: Vec::new(),
            has_child: false,
            content_off: 0,
            fch: ' ',
            flen: 0,
            foff: 0,
            html: 0,
        }
    }
}

struct Parser<'a> {
    c: &'a [char],
    starts: &'a [usize],
    ends: &'a [usize],
    first_line: u32,
    stack: Vec<Node>,
    leaves: Vec<Leaf>,
    ln: usize,
    end: usize,
    off: usize,
    col: usize,
    nns: usize,
    nns_col: usize,
    indent: usize,
    indented: bool,
    blank: bool,
    matched: usize,
    all_closed: bool,
    line_done: bool,
}

impl<'a> Parser<'a> {
    fn peek(&self, i: usize) -> Option<char> {
        if i < self.end {
            Some(self.c[i])
        } else {
            None
        }
    }

    fn find_nns(&mut self) {
        let mut i = self.off;
        let mut cols = self.col;
        while i < self.end {
            let ch = self.c[i];
            if ch == ' ' {
                i += 1;
                cols += 1;
            } else if ch == '\t' {
                i += 1;
                cols += 4 - cols % 4;
            } else {
                break;
            }
        }
        self.blank = i >= self.end;
        self.nns = i;
        self.nns_col = cols;
        self.indent = cols - self.col;
        self.indented = self.indent >= 4;
    }

    fn adv_nns(&mut self) {
        self.off = self.nns;
        self.col = self.nns_col;
    }

    fn adv(&mut self, count: usize, columns: bool) {
        let mut count = count;
        while count > 0 {
            let ch = match self.peek(self.off) {
                Some(ch) => ch,
                None => break,
            };
            if ch == '\t' {
                let to_tab = 4 - self.col % 4;
                if columns {
                    let partial = to_tab > count;
                    let add = if to_tab > count { count } else { to_tab };
                    self.col += add;
                    if !partial {
                        self.off += 1;
                    }
                    count -= add;
                } else {
                    self.col += to_tab;
                    self.off += 1;
                    count -= 1;
                }
            } else {
                self.off += 1;
                self.col += 1;
                count -= 1;
            }
        }
    }

    fn emit(&mut self, n: Node) {
        match n.kind {
            NKind::Para => self.leaves.push(Leaf {
                kind: Kind::Paragraph,
                first_line: n.first,
                end_line: n.last + 1,
                segments: n.segs,
            }),
            NKind::Fence => self.leaves.push(Leaf {
                kind: Kind::CodeFence,
                first_line: n.first,
                end_line: n.last + 1,
                segments: Vec::new(),
            }),
            NKind::Indented => self.leaves.push(Leaf {
                kind: Kind::CodeIndented,
                first_line: n.first,
                end_line: n.last_nb + 1,
                segments: Vec::new(),
            }),
            NKind::Html => self.leaves.push(Leaf {
                kind: Kind::Html,
                first_line: n.first,
                end_line: n.last + 1,
                segments: Vec::new(),
            }),
            _ => {}
        }
    }

    fn close_to(&mut self, n: usize) {
        while self.stack.len() > n {
            if let Some(node) = self.stack.pop() {
                self.emit(node);
            }
        }
    }

    fn close_unmatched(&mut self) {
        if !self.all_closed {
            let m = self.matched;
            self.close_to(m);
            self.all_closed = true;
        }
    }

    fn pop_leaf_tips(&mut self) {
        while self.stack.last().map_or(false, |n| is_leaf(n.kind)) {
            if let Some(node) = self.stack.pop() {
                self.emit(node);
            }
        }
    }

    fn add_child(&mut self, node: Node) {
        self.pop_leaf_tips();
        if let Some(top) = self.stack.last_mut() {
            top.has_child = true;
        }
        self.stack.push(node);
    }

    fn add_leaf(&mut self, leaf: Leaf) {
        self.pop_leaf_tips();
        if let Some(top) = self.stack.last_mut() {
            top.has_child = true;
        }
        self.leaves.push(leaf);
    }

    fn tip_is_para(&self) -> bool {
        self.stack.last().map_or(false, |n| n.kind == NKind::Para)
    }

    fn add_line(&mut self, idx: usize) {
        let ln = self.ln;
        let blank = self.blank;
        let end = self.end;
        let mut i = self.off;
        while i < end && (self.c[i] == ' ' || self.c[i] == '\t') {
            i += 1;
        }
        let n = &mut self.stack[idx];
        n.last = ln;
        if !blank {
            n.last_nb = ln;
        }
        if n.kind == NKind::Para {
            n.segs.push((i, end));
        }
    }

    /// Continuation of the open node at `idx`: 0 matched, 1 not matched, 2 line consumed.
    fn cont(&mut self, idx: usize) -> u8 {
        let (kind, content_off, has_child, fch, flen, foff, html) = {
            let n = &self.stack[idx];
            (n.kind, n.content_off, n.has_child, n.fch, n.flen, n.foff, n.html)
        };
        match kind {
            NKind::Quote => {
                if !self.indented && self.peek(self.nns) == Some('>') {
                    self.adv_nns();
                    self.adv(1, false);
                    if matches!(self.peek(self.off), Some(' ') | Some('\t')) {
                        self.adv(1, true);
                    }
                    0
                } else {
                    1
                }
            }
            NKind::Item => {
                if self.blank {
                    if !has_child {
                        return 1;
                    }
                    self.adv_nns();
                } else if self.indent >= content_off {
                    self.adv(content_off, true);
                } else {
                    return 1;
                }
                0
            }
            NKind::Fence => {
                if self.indent < 4 && self.peek(self.nns) == Some(fch) {
                    let mut j = self.nns;
                    while j < self.end && self.c[j] == fch {
                        j += 1;
                    }
                    if j - self.nns >= flen && rest_blank(self.c, j, self.end) {
                        self.stack[idx].last = self.ln;
                        if let Some(node) = self.stack.pop() {
                            self.emit(node);
                        }
                        return 2;
                    }
                }
                let mut i = foff;
                while i > 0 && matches!(self.peek(self.off), Some(' ') | Some('\t')) {
                    self.adv(1, true);
                    i -= 1;
                }
                0
            }
            NKind::Indented => {
                if self.indent >= 4 {
                    self.adv(4, true);
                } else if self.blank {
                    self.adv_nns();
                } else {
                    return 1;
                }
                0
            }
            NKind::Html => {
                if self.blank && (html == 6 || html == 7) {
                    1
                } else {
                    0
                }
            }
            NKind::Para => {
                if self.blank {
                    1
                } else {
                    0
                }
            }
        }
    }

    /// Try to start a block at the next non-blank char: 0 none, 1 container, 2 leaf.
    fn block_starts(&mut self, ckind: Option<NKind>) -> u8 {
        let ln = self.ln;
        let e = self.end;
        let c = self.c;
        if !self.indented && self.peek(self.nns) == Some('>') {
            self.adv_nns();
            self.adv(1, false);
            if matches!(self.peek(self.off), Some(' ') | Some('\t')) {
                self.adv(1, true);
            }
            self.close_unmatched();
            self.add_child(Node::new(NKind::Quote, ln));
            return 1;
        }
        if !self.indented && self.peek(self.nns) == Some('#') && is_atx(c, self.nns, e) {
            let mut p = self.nns;
            while p < e && c[p] == '#' {
                p += 1;
            }
            while p < e && (c[p] == ' ' || c[p] == '\t') {
                p += 1;
            }
            let n = p - self.nns;
            self.adv_nns();
            self.adv(n, false);
            self.close_unmatched();
            let seg = (self.off, e);
            self.add_leaf(Leaf {
                kind: Kind::Heading,
                first_line: ln,
                end_line: ln + 1,
                segments: vec![seg],
            });
            self.line_done = true;
            return 2;
        }
        if !self.indented && matches!(self.peek(self.nns), Some('`') | Some('~')) {
            if let Some((fch, flen)) = fence_start(c, self.nns, e) {
                self.close_unmatched();
                let mut node = Node::new(NKind::Fence, ln);
                node.fch = fch;
                node.flen = flen;
                node.foff = self.indent;
                self.add_child(node);
                self.adv_nns();
                self.adv(flen, false);
                return 2;
            }
        }
        if !self.indented && self.peek(self.nns) == Some('<') {
            if let Some(kind) = html_start(c, self.nns, e) {
                let cond = ckind != Some(NKind::Para) && !(!self.all_closed && !self.blank && self.tip_is_para());
                if kind < 7 || cond {
                    self.close_unmatched();
                    let mut node = Node::new(NKind::Html, ln);
                    node.html = kind;
                    self.add_child(node);
                    return 2;
                }
            }
        }
        if !self.indented && ckind == Some(NKind::Para) && self.nns < e {
            let mut rest_end = e;
            while rest_end > self.nns && (c[rest_end - 1] == ' ' || c[rest_end - 1] == '\t') {
                rest_end -= 1;
            }
            let ch = c[self.nns];
            if (ch == '=' || ch == '-') && c[self.nns..rest_end].iter().all(|&x| x == ch) {
                self.close_unmatched();
                if let Some(para) = self.stack.pop() {
                    self.leaves.push(Leaf {
                        kind: Kind::Heading,
                        first_line: para.first,
                        end_line: ln + 1,
                        segments: para.segs,
                    });
                }
                self.line_done = true;
                return 2;
            }
        }
        if !self.indented && self.nns < e && is_thematic(c, self.nns, e) {
            self.close_unmatched();
            self.add_leaf(Leaf {
                kind: Kind::ThematicBreak,
                first_line: ln,
                end_line: ln + 1,
                segments: Vec::new(),
            });
            self.line_done = true;
            return 2;
        }
        if !self.indented && self.nns < e {
            let (mlen, start) = list_marker_len(c, self.nns, e);
            let mut ok = mlen > 0;
            if ok && ckind == Some(NKind::Para) {
                if let Some(s) = start {
                    if s != 1 {
                        ok = false;
                    }
                }
            }
            let after = self.nns + mlen;
            if ok && after < e && c[after] != ' ' && c[after] != '\t' {
                ok = false;
            }
            if ok && ckind == Some(NKind::Para) && rest_blank(c, after, e) {
                ok = false;
            }
            if ok {
                let marker_offset = self.indent;
                self.adv_nns();
                self.adv(mlen, true);
                let sc = self.col;
                let so = self.off;
                loop {
                    self.adv(1, true);
                    if !(self.col - sc < 5 && matches!(self.peek(self.off), Some(' ') | Some('\t'))) {
                        break;
                    }
                }
                let blank_item = self.peek(self.off).is_none();
                let spaces = self.col - sc;
                let padding;
                if spaces >= 5 || spaces < 1 || blank_item {
                    padding = mlen + 1;
                    self.col = sc;
                    self.off = so;
                    if matches!(self.peek(self.off), Some(' ') | Some('\t')) {
                        self.adv(1, true);
                    }
                } else {
                    padding = mlen + spaces;
                }
                self.close_unmatched();
                let mut node = Node::new(NKind::Item, ln);
                node.content_off = marker_offset + padding;
                self.add_child(node);
                return 1;
            }
        }
        if self.indented && !self.tip_is_para() && !self.blank {
            self.adv(4, true);
            self.close_unmatched();
            self.add_child(Node::new(NKind::Indented, ln));
            return 2;
        }
        0
    }

    fn line(&mut self, ln: usize) -> Result<(), Diagnostic> {
        self.ln = ln;
        self.end = self.ends[ln];
        self.off = self.starts[ln];
        self.col = 0;
        self.line_done = false;
        let n_open = self.stack.len();
        let mut matched = 0usize;
        let mut all_matched = true;
        let mut idx = 0usize;
        while idx < n_open {
            self.find_nns();
            let r = self.cont(idx);
            if r == 0 {
                matched += 1;
            } else if r == 1 {
                all_matched = false;
                break;
            } else {
                return Ok(());
            }
            idx += 1;
        }
        self.matched = matched;
        self.all_closed = all_matched;
        let mut ckind: Option<NKind> = if matched > 0 { Some(self.stack[matched - 1].kind) } else { None };
        let mut matched_leaf = matches!(ckind, Some(NKind::Fence) | Some(NKind::Indented) | Some(NKind::Html));
        while !matched_leaf {
            self.find_nns();
            let res = self.block_starts(ckind);
            if res == 0 {
                self.adv_nns();
                break;
            }
            if self.stack.len() > MAX_DEPTH {
                return Err(Diagnostic::new(
                    Code::ResourceLimit,
                    format!("block nesting is deeper than {} levels", MAX_DEPTH),
                )
                .at_position(self.first_line + ln as u32, (self.nns - self.starts[ln]) as u32 + 1));
            }
            ckind = self.stack.last().map(|n| n.kind);
            if res == 2 {
                matched_leaf = true;
            }
            if self.line_done {
                break;
            }
        }
        if self.line_done {
            return Ok(());
        }
        if !self.all_closed && !self.blank && self.tip_is_para() {
            let top = self.stack.len() - 1;
            self.add_line(top);
        } else {
            self.close_unmatched();
            match ckind {
                Some(k) if is_leaf(k) => {
                    let top = self.stack.len() - 1;
                    self.add_line(top);
                    let (nkind, html) = {
                        let n = &self.stack[top];
                        (n.kind, n.html)
                    };
                    if nkind == NKind::Html
                        && html >= 1
                        && html <= 5
                        && html_ends_on_line(self.c, self.off, self.end, html)
                    {
                        if let Some(node) = self.stack.pop() {
                            self.emit(node);
                        }
                    }
                }
                _ => {
                    if self.off < self.end && !self.blank {
                        self.add_child(Node::new(NKind::Para, ln));
                        self.adv_nns();
                        let top = self.stack.len() - 1;
                        self.add_line(top);
                    }
                }
            }
        }
        Ok(())
    }
}

/// The leaf blocks of the document in source order. `first_line` is only used in diagnostics.
pub fn parse(c: &[char], starts: &[usize], ends: &[usize], first_line: u32) -> Result<Vec<Leaf>, Diagnostic> {
    let mut p = Parser {
        c,
        starts,
        ends,
        first_line,
        stack: Vec::new(),
        leaves: Vec::new(),
        ln: 0,
        end: 0,
        off: 0,
        col: 0,
        nns: 0,
        nns_col: 0,
        indent: 0,
        indented: false,
        blank: true,
        matched: 0,
        all_closed: true,
        line_done: false,
    };
    let mut nl = starts.len();
    if nl > 1 && starts[nl - 1] == c.len() {
        nl -= 1; // no phantom line after a final line end
    }
    for ln in 0..nl {
        p.line(ln)?;
    }
    p.close_to(0);
    let mut leaves = p.leaves;
    leaves.sort_by(|a, b| (a.first_line, a.end_line).cmp(&(b.first_line, b.end_line)));
    Ok(leaves)
}

/// `parse` over raw text (16 MiB input limit).
pub fn regions(text: &str) -> Result<Vec<Leaf>, Diagnostic> {
    if text.len() > 16 * 1024 * 1024 {
        return Err(Diagnostic::new(Code::ResourceLimit, "input is larger than 16 MiB"));
    }
    let c: Vec<char> = text.chars().collect();
    let (starts, ends) = line_bounds(&c);
    parse(&c, &starts, &ends, 1)
}
