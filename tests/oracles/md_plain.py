"""Independent Markdown oracle for tests: markdown-it-py (CommonMark + GFM tables,
strikethrough, optionally linkify autolinks).

`analyse(src)` -> (skeleton, texts): `skeleton` is the block token types with, for
every inline token, the non-text inline child types (links, code spans, breaks ...);
`texts` is the plain text of each inline token (softbreak = LF).
"""
import re

from markdown_it import MarkdownIt

TEXT = {"text", "text_special"}


def _md(linkify):
    md = MarkdownIt("commonmark", {"linkify": linkify}).enable(["table", "strikethrough"])
    if linkify:
        md.enable("linkify")
    return md


GFM_AUTOLINK = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.)|@", re.I)


def _gfm_children(kids):
    """Drop linkify-it's fuzzy bare-domain links (`a.io`): GFM only autolinks
    `www.`, `scheme://` and e-mail addresses."""
    out, skip_close = [], False
    for i, k in enumerate(kids):
        if k.type == "link_open" and k.markup == "linkify":
            label = kids[i + 1].content if i + 1 < len(kids) else ""
            if not GFM_AUTOLINK.search(label):
                skip_close = True
                continue
        if k.type == "link_close" and skip_close:
            skip_close = False
            continue
        out.append(k)
    return out


def analyse(src, linkify=False):
    skeleton, texts = [], []
    for tok in _md(linkify).parse(src):
        if tok.type == "inline":
            kids = _gfm_children(tok.children or [])
            skeleton.append(
                "inline[" + ",".join(k.type for k in kids if k.type not in TEXT and k.type != "softbreak") + "]"
            )
            texts.append("".join(
                k.content if k.type in TEXT else ("\n" if k.type == "softbreak" else "") for k in kids
            ))
        else:
            skeleton.append(tok.type)
    return skeleton, texts


def norm(s):
    """Line ends to LF, runs of blank lines to one, edge blank lines dropped."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{2,}", "\n\n", s).strip("\n")


def live_and_literal(src, name="x"):
    """(number of `{{name}}` in inline text, number inside code/HTML tokens)."""
    needle = "{{" + name + "}}"
    live = literal = 0
    for tok in _md(False).parse(src):
        if tok.type == "inline":
            in_autolink = False
            for k in tok.children or []:
                if k.type == "link_open":
                    in_autolink = k.markup == "autolink"
                elif k.type == "link_close":
                    in_autolink = False
                elif k.type in TEXT and in_autolink:
                    literal += k.content.count(needle)  # `<url>` autolinks are never expanded
                elif k.type in TEXT:
                    live += k.content.count(needle)
                elif k.type in ("code_inline", "html_inline"):
                    literal += k.content.count(needle)
        elif tok.type in ("fence", "code_block", "html_block"):
            literal += tok.content.count(needle)
    return live, literal
