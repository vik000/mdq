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
