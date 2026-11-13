"""UNSTABLE test hooks: thin re-exports of Rust internals.

Not part of the public API; may change without notice. Exists so every
building block can be tested directly from Python.
"""
from mdq._native import _internal as _i

diagnostic_codes = _i.diagnostic_codes
supported_profiles = _i.supported_profiles
check_required = _i.check_required
_panic_probe = _i._panic_probe

# E1.1 decimal arithmetic (strings in, canonical strings out)
dec_canonical = _i.dec_canonical
dec_add = _i.dec_add
dec_sub = _i.dec_sub
dec_mul = _i.dec_mul
dec_div = _i.dec_div
dec_neg = _i.dec_neg
dec_round_sig = _i.dec_round_sig
dec_precision = _i.dec_precision

# E1.2 unit catalogue and conversion
unit_table = _i.unit_table
unit_lookup = _i.unit_lookup
unit_match_at = _i.unit_match_at
unit_convert = _i.unit_convert

# E1.3 calc lexer
calc_tokenize = _i.calc_tokenize

# E1.4 calc parser
calc_parse = _i.calc_parse

# E1.5 calc evaluator and dimension algebra
calc_eval = _i.calc_eval
dim_canonical = _i.dim_canonical
dim_combine = _i.dim_combine

# E1.6 FX observation selection
fx_parse_instant = _i.fx_parse_instant
fx_select = _i.fx_select
calc_render = _i.calc_render

# E2.1 frontmatter
frontmatter_split = _i.frontmatter_split

# E2.2 YAML loader
yaml_load_strict = _i.yaml_load_strict

# E2.3 metadata merge and cascade
meta_merge = _i.meta_merge
meta_cascade = _i.meta_cascade

# E2.4 variables and escaping
var_format_scalar = _i.var_format_scalar
md_escape_inline = _i.md_escape_inline
interpolate = _i.interpolate
md_regions = _i.md_regions
directive_scan = _i.directive_scan

# E3.1 UTF-8 input
utf8_decode = _i.utf8_decode

# E3.3 attribute lists and fenced containers
attrs_parse = _i.attrs_parse
container_parse = _i.container_parse
container_eval = _i.container_eval

# E4.1 import paths
path_normalise = _i.path_normalise
path_identity = _i.path_identity
path_confine = _i.path_confine

# E4.2 heading identity and anchors
heading_plain_text = _i.heading_plain_text
heading_match = _i.heading_match
slugify = _i.slugify
slug_dedupe = _i.slug_dedupe
percent_encode_anchor = _i.percent_encode_anchor

# E4.3 document index and references
doc_index = _i.doc_index
resolve_ref = _i.resolve_ref

# E4.4 import expansion
import_expand = _i.import_expand

# E4.5 link rebasing
rebase_link = _i.rebase_link
rebase_links = _i.rebase_links

# E5.1 rich-feature fallbacks
highlight_fallback = _i.highlight_fallback
callout_fallback = _i.callout_fallback
details_fallback = _i.details_fallback
columns_fallback = _i.columns_fallback

# E5.2a maths and footnotes
math_fallback = _i.math_fallback
footnote_check = _i.footnote_check
footnote_rename = _i.footnote_rename

# E5.2b citations
bib_parse = _i.bib_parse
citation_resolve = _i.citation_resolve

# E5.3 table of contents
toc_build = _i.toc_build
generic_fallback = _i.generic_fallback
export_markdown = _i.export_markdown

# E6.1 source declarations and URL templates
source_validate = _i.source_validate
url_template_expand = _i.url_template_expand

# E6.2 JSON Pointer selection and type checks
json_pointer_select = _i.json_pointer_select
value_type_check = _i.value_type_check

# E6.3 URL and IP classification
ip_is_blocked = _i.ip_is_blocked
url_classify = _i.url_classify
