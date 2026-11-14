"""Generate docs/cheatsheet.html: every snippet is run through the library and its REAL output is shown.

    python scripts/build_cheatsheet.py           # rewrite docs/cheatsheet.html
    python scripts/build_cheatsheet.py --check   # exit 1 if the file is out of date (used by the tests)
"""
from __future__ import annotations

import contextlib
import html
import io
import os
import subprocess
import sys
import textwrap
import zipfile
from decimal import Decimal
from pathlib import Path

import mdq
from mdq import _internal as mi

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "cheatsheet.html"
F = "```"


def run(files, entry="main.md", **kw) -> tuple:
    """(ok, text): the compiled Markdown, or `CODE at path:line:col: message`."""
    try:
        return True, mdq.compile(entry, files, **kw).markdown
    except mdq.MdqError as e:
        where = ":".join(str(x) for x in (e.path, e.line, e.column) if x is not None)
        return False, f"{type(e).__name__} ({e.code}) at {where}\n{e}"


def card(title, files, *, entry="main.md", note="", show=None, args="", **kw) -> str:
    ok, out = run(files, entry, **kw)
    srcs = "".join(
        (f'<div class="fname">{html.escape(name)}</div>' if len(files) > 1 or show else "")
        + f"<pre>{html.escape(text)}</pre>"
        for name, text in files.items() if show is None or name in show)
    label = "Output" + (f" <span class=\"args\">{html.escape(args)}</span>" if args else "")
    cls = "out" if ok else "err"
    return (f'<section class="card"><h2>{html.escape(title)}</h2>'
            + (f"<p>{note}</p>" if note else "")
            + srcs + f'<div class="label">{label if ok else "Error"}</div><pre class="{cls}">{html.escape(out)}</pre></section>')


def code_card(title, code, note="") -> str:
    """Run a Python snippet and show its printed output."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(compile(code, "<cheatsheet>", "exec"), {"__name__": "__cheatsheet__"})
    return (f'<section class="card wide"><h2>{html.escape(title)}</h2>' + (f"<p>{note}</p>" if note else "")
            + f"<pre>{html.escape(code.strip())}</pre><div class=\"label\">Printed</div>"
            f'<pre class="out">{html.escape(buf.getvalue())}</pre></section>')


def package_card() -> str:
    manifest = "format: mdq\nversion: '0.1'\nentrypoint: document.md\nfeatures: [core, calc, package]\ntitle: Guide\n" \
               "requirements:\n  network: optional\n  hosts: [api.example.org]\n"
    doc = "# {{document.title}}\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.yaml", manifest)
        z.writestr("document.md", doc)
    pkg = mdq.PackageFS(buf.getvalue())
    out = mdq.compile(pkg).markdown
    return ('<section class="card"><h2>Packages (.mdq)</h2><p>A ZIP with a root <code>manifest.yaml</code>; '
            '<code>features</code> must include <code>package</code>. Requests never grant: the network stays off '
            'unless the host allows it. <code>.mdq.age</code> = whole-package age encryption '
            '(<code>PackageFS(data, identities=[...])</code>, then <code>compile(pkg, allow_plaintext=True)</code>).</p>'
            f'<div class="fname">manifest.yaml</div><pre>{html.escape(manifest)}</pre>'
            f'<div class="fname">document.md</div><pre>{html.escape(doc)}</pre>'
            f'<div class="label">Output of <code>mdq.compile(mdq.PackageFS(data))</code></div><pre class="out">{html.escape(out)}</pre>'
            f'<div class="label">pkg.requests</div><pre class="out">{html.escape(str(pkg.requests))}</pre></section>')


def cli_card() -> str:
    with contextlib.ExitStack() as st:
        tmp = Path(st.enter_context(__import__("tempfile").TemporaryDirectory()))
        (tmp / "r.md").write_text("---\nvariables:\n  qty: 2\n---\n" + F + "calc\nt = qty * 2.5 EUR\n" + F + "\nTotal {{calc.t}}\n")
        cmds = [["build", "r.md", "--root", str(tmp)], ["build", "r.md", "--root", str(tmp), "--input", "qty=4"],
                ["build", "missing.md", "--root", str(tmp)]]
        lines = []
        for c in cmds:
            r = subprocess.run([sys.executable, "-m", "mdq", *c], capture_output=True, text=True)
            shown = " ".join("." if a == str(tmp) else a for a in c)
            lines.append(f"$ mdq {shown}\n{r.stdout}{r.stderr}[exit {r.returncode}]")
    return ('<section class="card wide"><h2>Command line</h2><p><code>mdq build|check|sources ENTRY --root DIR</code> '
            '(or <code>--package FILE</code>); options <code>--input NAME=VALUE</code>, <code>--snapshot FILE</code>, '
            '<code>--observations FILE --valuation TIME</code>, <code>--profile gfm</code>, <code>--calc-blocks keep</code>, '
            '<code>--allow-plaintext</code>, <code>-o FILE</code> (never a source file), <code>--format json</code> '
            '(one object, schema <code>mdq/cli.schema.json</code>). Offline: no command fetches.</p>'
            f'<pre class="out">{html.escape(chr(10).join(lines))}</pre>'
            '<table><tr><th>Exit</th><th>Meaning</th></tr>'
            '<tr><td>0</td><td>success</td></tr><tr><td>1</td><td>invalid input (syntax, metadata, references, variables, calc, sources)</td></tr>'
            '<tr><td>2</td><td>usage error (bad command line, unreadable option file, <code>-o</code> on a source)</td></tr>'
            '<tr><td>3</td><td>unsupported feature</td></tr><tr><td>4</td><td>denied capability (security)</td></tr>'
            '<tr><td>5</td><td>resource limit</td></tr></table></section>')


def errors_table() -> str:
    rows = []
    for code in mi.diagnostic_codes():
        try:
            mi._diagnostic_probe(code)
        except mdq.MdqError as e:
            cls = type(e)
            family = cls.__mro__[1].__name__
            rows.append(f"<tr><td><code>{code}</code></td><td><code>mdq.{cls.__name__}</code></td><td>{family}</td></tr>")
    return ('<h2>Errors</h2><p>Every error is an <code>mdq.MdqError</code> with <code>.code</code>, <code>.path</code>, '
            '<code>.line</code>, <code>.column</code>; one class per code, grouped in two families. Wrong argument '
            'types stay <code>TypeError</code>/<code>ValueError</code>.</p>'
            '<table><tr><th>Code</th><th>Class</th><th>Parent</th></tr>' + "".join(rows) + "</table>")


OBS = [{"id": "fx-1", "from": "EUR", "to": "USD", "rate": "1.0825", "observed_at": "2026-11-13T16:00:00Z", "provider": "demo"}]
DECL = ("---\nvariables:\n  qty:\n    type: number\n  price:\n    type: number\n    unit: USD\n"
        "    source:\n      kind: http\n      url: https://example.invalid/p\n      format: json\n"
        "      select: /price\n---\n{{qty}} items at {{price}}\n")


def snapshot_entry() -> dict:
    """A real snapshot entry, recorded through a fake transport (no network)."""
    def transport(req):
        if req["op"] == "resolve":
            return {"addresses": ["93.184.216.34"]}
        return {"status": 200, "headers": {}, "body": b'{"price": 19.99}', "peer": req["address"]}
    return mdq.test_source("main.md", {"main.md": DECL}, name="price", transport=transport,
                           retrieved_at="2026-11-14T09:00:00Z", host_policy={"network": "allow"},
                           library_policy={"network": "allow"})


def body() -> str:
    cards = [
        card("Documents and metadata", {"main.md": "---\ntitle: Report\nvariables:\n  team: Engines\n  owner: \"*Ada*\"\n---\n"
                                                   "# {{document.title}}\n\nBy {{team}}, owner {{owner}}.\n"},
             note="<code>{{document.key}}</code> reads metadata; <code>{{name}}</code> an entry under <code>variables:</code>. Values are escaped text."),
        card("Host inputs", {"main.md": "Hello, {{reader}}!\n"}, inputs={"reader": "Grace"},
             note="<code>inputs=</code> (CLI <code>--input</code>) is the top metadata layer, as <code>variables.*</code>.",
             args='inputs={"reader": "Grace"}'),
        card("Calculations", {"main.md": F + "calc\nprice = 120 EUR\nquantity = 4\nsubtotal = price * quantity\n"
                                         "tax = subtotal * 21%\ntotal = subtotal + tax\n" + F + "\nTotal: {{calc.total}}\n"},
             note="One assignment per line; each block has its own scope; the block is left out of the output "
                  "(<code>calc_blocks=\"keep\"</code> keeps it). 28 significant digits, half-even."),
        card("Units and temperatures", {"main.md": F + "calc\nd = 12 miles to km\nt = 72 degF to degC\narea = 3 m * 4 m\n"
                                                   "third = 1 / 3\n" + F + "\nDistance {{calc.d}}; {{calc.t}}; {{calc.area}}; {{calc.third}}\n"},
             note="Conversions round once; unknown units, incompatible dimensions and absolute-temperature arithmetic fail."),
        card("Money and exchange rates", {"main.md": "---\nvariables:\n  total: 100 EUR\n---\n" + F + "calc\nusd = total to USD\n" + F + "\nIn dollars: {{calc.usd}}\n"},
             observations=OBS, valuation="2026-11-14T00:00:00Z", args="observations=[EUR>USD 1.0825], valuation=...",
             note="Currency conversion never fetches a rate: without <code>observations=</code> and a <code>valuation=</code> time "
                  "(CLI <code>--observations FILE --valuation TIME</code>) it fails with MDQ_SOURCE_UNRESOLVED."),
        card("Imports and embeds", {"main.md": "![[ch/intro.md]]\n\n![[ch/plan.md#Goals]]\n\n![[ch/plan.md#^key]]\n",
                                    "ch/intro.md": "---\nignored: frontmatter\n---\n# Intro\n\nSee ![img](pic.png).\n",
                                    "ch/plan.md": "# Plan\n\n## Goals\n\nShip it.\n\n## Notes\n\nA key idea. ^key\n"},
             note="Whole file (frontmatter dropped), one section, or one block; links and images are rebased to the embedding file."),
        card("References, anchors and TOC", {"main.md": "# Guide ^top\n\n[[toc]]\n\n## Setup\n\nSee [[#Setup|the setup]], [[#^top]] "
                                                        "and [[other.md#API]].\n\nA note. ^n1\n", "other.md": "# API\n"},
             note="<code>[[file.md#Heading|label]]</code> links to the rendered copy, or to the file when it is not embedded; "
                  "<code>^id</code> becomes an <code>&lt;a id&gt;</code> anchor; generated slugs never take a block ID."),
        card("Metadata cascade", {".mdq.yaml": "org: Orbit\nlevel: project\n", "docs/.mdq.yaml": "level: folder\n",
                                  "docs/r.md": "---\nlevel: frontmatter\n---\n{{document.org}}, {{document.level}}, {{document.state}}\n",
                                  "docs/r.meta.yaml": "state: draft\n"}, entry="docs/r.md",
             note="Low to high: root <code>.mdq.yaml</code>, each folder's <code>.mdq.yaml</code>, frontmatter, sidecar "
                  "<code>&lt;name&gt;.meta.yaml</code>, then host inputs."),
        card("Conditionals and loops", {"main.md": "---\nvariables:\n  mode: full\n  items: [a, b]\n---\n:::if {var=\"mode\" equals=\"full\"}\nFull report.\n:::\n\n"
                                                   ":::for {each=\"items\" as=\"it\"}\n- item {{it}}\n:::\n"},
             note="Excluded branches are never evaluated: an import inside one is not even read."),
        card("Rich features (portable fallback)", {"main.md": "> [!NOTE]\n> Remember.\n\n==Marked== and $x^2$.[^1]\n\n"
                                                              ":::details {title=\"More\"}\nHidden.\n:::\n\n[^1]: A footnote.\n"},
             note="The <code>commonmark</code> profile turns them into plain CommonMark; <code>gfm</code> keeps <code>$maths$</code>."),
        card("Citations", {"main.md": "---\nbibliography: refs.bib\n---\nAs [@hopper52] says.\n",
                           "refs.bib": "@article{hopper52, author={Hopper, Grace}, title={Education of a Computer}, year={1952}}\n"},
             note="<code>bibliography:</code> is a <code>.bib</code> (BibTeX) or <code>.json</code> (CSL JSON) file, or a list."),
        card("Variable files", {"main.md": "---\nvariables:\n  team: ./data/team.yaml\n---\nLead: {{team.lead}}\n",
                                "data/team.yaml": "lead: Ada\n"},
             note="A string starting <code>./</code> or <code>../</code> and ending .yaml/.yml/.json loads that file as a namespace."),
        card("Declared variables and snapshots", {"main.md": DECL},
             inputs={"qty": 3}, snapshot=[snapshot_entry()], args='inputs={"qty": 3}, snapshot=[entry]',
             note="<code>type:</code> declares a typed variable: host-supplied (<code>inputs=</code>) or, with a "
                  "<code>source:</code>, from a recorded snapshot (<code>snapshot=</code>, CLI <code>--snapshot</code>; "
                  "made by <code>mdq.test_source</code>). Compile never fetches; a missing value fails, even if unused."),
        package_card(),
        cli_card(),
        code_card("Python API", textwrap.dedent('''
            from decimal import Decimal
            import mdq

            files = {"d.md": "```calc\\nx = price * 3\\n```\\nx = {{calc.x}}\\n"}
            r = mdq.compile("d.md", files, inputs={"price": Decimal("2.50")})
            print(repr(r.markdown))
            v = r.calc[0].results[0]
            print(v.name, v.value, type(v.value).__name__, v.display)
            ev = mdq.evaluate("d.md", files, inputs={"price": 4})       # data only
            print(ev.calc[0].results[0].value)
            try:
                mdq.compile("d.md", {"d.md": "{{nope}}\\n"})
            except mdq.UndefinedVariableError as e:
                print(e.code, e.path, e.line, e.column)
            # also: mdq.DirectoryFS(root), mdq.PackageFS(data), mdq.validate_sources(entry, fs),
            #       mdq.test_source(entry, fs, name=..., transport=..., retrieved_at=..., host_policy=..., library_policy=...)
            '''), note="Results are frozen dataclasses with exact <code>Decimal</code> values; floats are refused."),
    ]
    grammar = ('<h2>Calculation grammar at a glance</h2><table><tr><th>Construct</th><th>Example</th></tr>'
               '<tr><td>assignment (one per line, unique per block)</td><td><code>name = expression</code></td></tr>'
               '<tr><td>operators, unary, grouping</td><td><code>+ - * /</code>, <code>-x</code>, <code>(2 + 3) * 4</code></td></tr>'
               '<tr><td>percentage (dimensionless operands)</td><td><code>21%</code> means 0.21</td></tr>'
               '<tr><td>quantity, conversion</td><td><code>12 miles</code>, <code>72 degF to degC</code></td></tr>'
               '<tr><td>money (ISO-style code)</td><td><code>120 EUR</code>; <code>to USD</code> needs an observation</td></tr>'
               '<tr><td>full-line comment</td><td><code># note</code></td></tr>'
               '<tr><td>names</td><td>earlier lines of the block, then numeric entries under <code>variables:</code> (and inputs)</td></tr>'
               '<tr><td>not in 0.1</td><td>functions, strings, <code>^</code>/<code>**</code>, comparisons, conditions, indexing, inline comments, scientific notation, live fetch</td></tr>'
               '</table>')
    return ('<div class="grid">' + "".join(cards) + "</div>" + grammar + errors_table())


CSS = """body{font:16px/1.55 system-ui,-apple-system,sans-serif;margin:0;background:#f6f7f9;color:#202936}
header{background:#17273f;color:white;padding:40px max(24px,calc((100% - 1150px)/2))}h1{margin:0;font-size:2rem}
header p{max-width:860px;color:#dbe7f7}.wrap{max-width:1150px;margin:28px auto;padding:0 22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}
.card{background:white;border:1px solid #dce1e7;border-radius:12px;padding:20px;min-width:0}.card.wide{grid-column:1/-1}
.card p{margin:0 0 10px;font-size:.92rem;color:#3b4656}h2{font-size:1.08rem;margin:0 0 10px}
pre{overflow:auto;background:#101d2e;color:#e6edf5;padding:12px 14px;border-radius:8px;font:13px/1.5 ui-monospace,Consolas,monospace;margin:4px 0 10px}
pre.out{background:#eef6ee;color:#16301b;border:1px solid #cfe3d1}pre.err{background:#fbefef;color:#5a1414;border:1px solid #efcaca}
.label,.fname{font-size:.75rem;text-transform:uppercase;letter-spacing:.04em;color:#596578;margin-top:6px}.fname{text-transform:none;font-family:ui-monospace,Consolas,monospace}
.args{text-transform:none;font-family:ui-monospace,Consolas,monospace}code{font-family:ui-monospace,Consolas,monospace}
table{border-collapse:collapse;width:100%;background:white;margin:6px 0 20px}td,th{border:1px solid #dce1e7;padding:8px;text-align:left;vertical-align:top}
.note{border-left:4px solid #526b95;padding:10px 16px;background:white;margin:22px 0}footer{color:#596578;font-size:.85rem;padding:25px 0}
@media print{body{background:white}.card{break-inside:avoid}header{padding:18px;color:#111;background:white}header p{color:#333}}"""


def page() -> str:
    v = mdq.version_info()
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>MDQ {v['syntax']} Cheatsheet</title><style>{CSS}</style></head><body>"
            f"<header><h1>MDQ {v['syntax']} — Cheatsheet</h1><p>Syntax, Python API and command line. Every output on this page "
            f"was produced by mdq {v['package']} (unit catalogue {v['unit_catalogue']}) when the page was generated by "
            "<code>scripts/build_cheatsheet.py</code>. Normative rules are in <code>spec/</code>.</p></header><main class=\"wrap\">"
            "<div class=\"note\"><strong>Safe by default:</strong> sources are never modified, nothing is fetched, network "
            "requests in documents never grant anything, and every error has a stable code and a location.</div>"
            + body() + "<footer>Generated file — edit <code>scripts/build_cheatsheet.py</code>, then run it. "
            "More: <code>examples/</code> (runnable projects), <code>docs/TEST_PLAN.md</code> section 6 (exact behaviour per feature).</footer>"
            "</main></body></html>\n")


def main(argv) -> int:
    text = page()
    if "--check" in argv:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != text:
            print("docs/cheatsheet.html is out of date: run python scripts/build_cheatsheet.py")
            return 1
        print("docs/cheatsheet.html is up to date")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
