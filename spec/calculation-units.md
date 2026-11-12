# MDQ Unit Catalogue — 0.1.0-draft

**Status:** the complete canonical table is now authored (this closes the former release blocker, gap G1). Sections marked **PROPOSED** are not yet in `extended-markdown.md` and need to be adopted there before they are frozen as normative. The machine-readable table is [`units.tsv`](units.tsv); the table in section 4 is generated from it, and the two MUST NOT diverge.

The categories required for `calc` conformance are: length, mass, area, volume, time, temperature, speed, energy, power, pressure, angle and digital information. Currency is a distinct typed dimension and is not part of this table (section 8). No HTTP access is required for physical units.

## 1. Data model

Each unit has a **symbol** (case-sensitive), a **dimension**, an exact **scale** and an exact **offset** relative to the base unit of its dimension:

```text
value_in_base = (value + offset) * scale
value_in_unit = value_in_base / scale - offset
```

`scale` and `offset` are exact rational numbers, written either as a finite decimal (`0.3048`) or as a fraction of two decimals (`5/9`, `4.4482216152605/0.00064516`). Every catalogue factor is derived from the defining relations listed in the table, never from a measured or floating-point value.

Dimensions are products of base dimensions with integer exponents: `L` length, `M` mass, `T` time, `Th` absolute temperature, `dTh` temperature difference, `A` angle, `I` information. Angle and information are deliberate base dimensions of their own, so `rad to m` and `B to kg` are rejected.

## 2. Conversion semantics (PROPOSED, gap G13)

`value to unit` requires both units to have the same dimension, otherwise `MDQ_CALC_ERROR`. The result is the **exact rational value** of

```text
((value + offset_from) * scale_from) / scale_to - offset_to
```

rounded **once** to 28 significant digits, half-even. Intermediate steps MUST NOT be rounded; an implementation that rounds after each step (for example after multiplying by `5/9`) can differ in the last digit and is not conforming. The input value is taken exactly as written, as in section 8.6 of `extended-markdown.md`. Converting a unit to itself returns the value unchanged (canonicalised, but not rounded).

`miles`, `degF` and `km/h` are examples whose factors are not finite decimals (`degF` scale is `5/9`, `km/h` is `5/18`); the rule above is why they need exact fractions rather than rounded decimal constants.

## 3. Unit tokens

- Symbols are **case-sensitive** and there are **no aliases and no plurals** other than the symbol `miles` itself. `mile`, `Miles`, `KM`, `Kg`, `l` (lower-case L), `KB`, `mb`, `C`, `F`, `sec`, `hr` and `metre` are all unknown and MUST be rejected, never guessed.
- A unit token is recognised only in a **unit position**: after a number, or after `to`. In a unit position the tokeniser picks the **longest** symbol in the catalogue (or an ISO 4217 currency code) that matches at that point **and is not immediately followed by** `[A-Za-z0-9_]`. If nothing qualifies, the result is `MDQ_CALC_ERROR` (unknown unit).
- Consequences: `12 mph` is `mph` (not `m` followed by `ph`); `12 mins` is an error (`min` is followed by `s`); `12 km/h` is `km/h`; `12 km/hour` is `km`, then `/`, then the identifier `hour`; `12 m/2` is `m`, then `/`, then `2`; `12 GBP` is a currency, not `GB` followed by `P`.
- `deltaK` appears in the table because it is the unit of the result of subtracting two absolute temperatures. It is **RESULT-ONLY (PROPOSED, gap G14)**: it MUST NOT be accepted as a unit token in `calc` source, including after `to`.

## 4. The table

| Symbol | Name | Dimension | Scale to base | Offset | Base unit | Kind | Definition |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `m` | metre | `L1` | `1` | `0` | `m` | linear | SI base unit |
| `km` | kilometre | `L1` | `1000` | `0` | `m` | linear | 1000 m |
| `cm` | centimetre | `L1` | `0.01` | `0` | `m` | linear | 0.01 m |
| `mm` | millimetre | `L1` | `0.001` | `0` | `m` | linear | 0.001 m |
| `in` | inch | `L1` | `0.0254` | `0` | `m` | linear | international inch, exactly 0.0254 m |
| `ft` | foot | `L1` | `0.3048` | `0` | `m` | linear | 12 in |
| `yd` | yard | `L1` | `0.9144` | `0` | `m` | linear | 3 ft |
| `miles` | statute mile | `L1` | `1609.344` | `0` | `m` | linear | 1760 yd (the symbol is the plural `miles`) |
| `kg` | kilogram | `M1` | `1` | `0` | `kg` | linear | SI base unit |
| `g` | gram | `M1` | `0.001` | `0` | `kg` | linear | 0.001 kg |
| `mg` | milligram | `M1` | `0.000001` | `0` | `kg` | linear | 0.000001 kg |
| `lb` | pound | `M1` | `0.45359237` | `0` | `kg` | linear | avoirdupois pound, exactly 0.45359237 kg |
| `oz` | ounce | `M1` | `0.028349523125` | `0` | `kg` | linear | 1/16 lb |
| `m2` | square metre | `L2` | `1` | `0` | `m2` | linear | SI coherent unit |
| `km2` | square kilometre | `L2` | `1000000` | `0` | `m2` | linear | (1000 m)^2 |
| `ft2` | square foot | `L2` | `0.09290304` | `0` | `m2` | linear | (0.3048 m)^2 |
| `acre` | acre | `L2` | `4046.8564224` | `0` | `m2` | linear | international acre, 43560 ft2 |
| `ha` | hectare | `L2` | `10000` | `0` | `m2` | linear | 10000 m2 |
| `m3` | cubic metre | `L3` | `1` | `0` | `m3` | linear | SI coherent unit |
| `L` | litre | `L3` | `0.001` | `0` | `m3` | linear | 0.001 m3 (upper-case L only) |
| `mL` | millilitre | `L3` | `0.000001` | `0` | `m3` | linear | 0.000001 m3 |
| `gal_us` | US liquid gallon | `L3` | `0.003785411784` | `0` | `m3` | linear | 231 in3, exactly 0.003785411784 m3 |
| `qt_us` | US liquid quart | `L3` | `0.000946352946` | `0` | `m3` | linear | 1/4 gal_us |
| `s` | second | `T1` | `1` | `0` | `s` | linear | SI base unit |
| `min` | minute | `T1` | `60` | `0` | `s` | linear | 60 s |
| `h` | hour | `T1` | `3600` | `0` | `s` | linear | 3600 s |
| `day` | day | `T1` | `86400` | `0` | `s` | linear | exactly 86400 s (not a calendar day) |
| `K` | kelvin | `Th1` | `1` | `0` | `K` | absolute_temperature | SI base unit |
| `degC` | degree Celsius | `Th1` | `1` | `273.15` | `K` | absolute_temperature | K = degC + 273.15 |
| `degF` | degree Fahrenheit | `Th1` | `5/9` | `459.67` | `K` | absolute_temperature | degC = (degF - 32) * 5 / 9 |
| `deltaK` | kelvin difference | `dTh1` | `1` | `0` | `deltaK` | difference | RESULT-ONLY: difference of two absolute temperatures |
| `m/s` | metre per second | `L1*T-1` | `1` | `0` | `m/s` | linear | SI coherent unit |
| `km/h` | kilometre per hour | `L1*T-1` | `5/18` | `0` | `m/s` | linear | 1000 m / 3600 s |
| `mph` | mile per hour | `L1*T-1` | `0.44704` | `0` | `m/s` | linear | 1 miles / 1 h |
| `J` | joule | `M1*L2*T-2` | `1` | `0` | `J` | linear | SI coherent unit |
| `kJ` | kilojoule | `M1*L2*T-2` | `1000` | `0` | `J` | linear | 1000 J |
| `cal` | thermochemical calorie | `M1*L2*T-2` | `4.184` | `0` | `J` | linear | exactly 4.184 J (not the kilocalorie) |
| `kWh` | kilowatt hour | `M1*L2*T-2` | `3600000` | `0` | `J` | linear | 1000 W * 3600 s |
| `W` | watt | `M1*L2*T-3` | `1` | `0` | `W` | linear | SI coherent unit |
| `kW` | kilowatt | `M1*L2*T-3` | `1000` | `0` | `W` | linear | 1000 W |
| `hp` | mechanical horsepower | `M1*L2*T-3` | `745.69987158227022` | `0` | `W` | linear | 550 ft*lbf/s, lbf = 0.45359237 kg * 9.80665 m/s2 |
| `Pa` | pascal | `M1*L-1*T-2` | `1` | `0` | `Pa` | linear | SI coherent unit |
| `kPa` | kilopascal | `M1*L-1*T-2` | `1000` | `0` | `Pa` | linear | 1000 Pa |
| `bar` | bar | `M1*L-1*T-2` | `100000` | `0` | `Pa` | linear | 100000 Pa |
| `psi` | pound-force per square inch | `M1*L-1*T-2` | `4.4482216152605/0.00064516` | `0` | `Pa` | linear | lbf / in2 (not a finite decimal) |
| `rad` | radian | `A1` | `1` | `0` | `rad` | linear | SI coherent unit |
| `deg` | degree of arc | `A1` | `3.1415926535897932384626433832795028841972/180` | `0` | `rad` | linear | PI / 180 using the constant PI below |
| `bit` | bit | `I1` | `1` | `0` | `bit` | linear | base unit |
| `B` | byte | `I1` | `8` | `0` | `bit` | linear | 8 bit |
| `kB` | kilobyte | `I1` | `8000` | `0` | `bit` | linear | 1000 B |
| `MB` | megabyte | `I1` | `8000000` | `0` | `bit` | linear | 1000^2 B |
| `GB` | gigabyte | `I1` | `8000000000` | `0` | `bit` | linear | 1000^3 B |
| `KiB` | kibibyte | `I1` | `8192` | `0` | `bit` | linear | 1024 B |
| `MiB` | mebibyte | `I1` | `8388608` | `0` | `bit` | linear | 1024^2 B |
| `GiB` | gibibyte | `I1` | `8589934592` | `0` | `bit` | linear | 1024^3 B |

`degC`, `degF` and `K` are absolute temperatures: they cannot be multiplied, divided, added to each other or used in compound units (`extended-markdown.md` section 8.7). Subtracting two of them gives a `deltaK` difference.

## 5. Constants used only in definitions

- `PI = 3.1415926535897932384626433832795028841972` (40 decimal places, rounded). It is a fixed exact decimal, so `deg` is exact relative to this definition and `180 deg to rad` is reproducible across implementations.
- Standard gravity `9.80665 m/s2`, exact by definition, used only to define `lbf` (and from it `hp` and `psi`). They are not available inside `calc`.

## 6. Derived dimensions and result units (PROPOSED, gap G2)

When two quantities are multiplied or divided, dimension exponents are added or subtracted (`extended-markdown.md` section 8.7). The result unit is chosen as follows:

1. If the dimension is **zero**, the result is a plain dimensionless decimal. The value is the ratio **after converting both operands to base units** (`5 km / 2 m` is `2500`).
2. If the dimension is **not** a catalogue dimension (for example `5 m * 3 s`), the operation is rejected with `MDQ_CALC_ERROR`.
3. Otherwise, if both operands have the same unit `u` and `u2` or `u3` exists in the catalogue, the result uses it (`m * m` gives `m2`; `km * km` gives `km2`; `ft * ft` gives `ft2`). If the operands have units `a` and `b` and the symbol `a/b` exists, the result uses it (`km / h` gives `km/h`; `m / s` gives `m/s`). The value is `value_a * value_b` or `value_a / value_b`, which agrees with the table scales.
4. Otherwise the result uses the **coherent unit** of the dimension (scale 1, offset 0: `m2`, `m3`, `m/s`, `J`, `W`, `Pa`, `rad`, `bit`, ...) and the value is computed from base-unit values. For example `10 W * 2 h` is `72000 J`, and `100 miles / 2 h` is `22.352 m/s`.

Absolute temperatures and `deltaK` never produce a derived dimension (`deltaK * deltaK` is rejected).

## 7. Exact reference relations

The relations in the original draft hold exactly with this table: `1 in = 0.0254 m`, `1 lb = 0.45359237 kg`, `1 mile = 1609.344 m`, `1 gal_us = 0.003785411784 m3`, `K = degC + 273.15`, `degC = (degF - 32) * 5 / 9`, `1 KiB = 1024 B`, `1 kB = 1000 B`. `C` and `F` MUST NOT denote temperature units.

## 8. Currency

A currency amount uses a three-letter ISO 4217 code in unit position (`120 EUR`). Currency is a distinct dimension: it never converts to a physical unit, and `to <currency>` needs a host-supplied observation (`extended-markdown.md` section 8.8). Which codes are valid (the full active ISO 4217 list, or only host-declared ones) is an open question (gap G16).

## 9. Conformance vectors

Each row is the exact conversion of the given value, rounded once to 28 significant digits (half-even), printed in canonical form. They are generated from the table by exact rational arithmetic.

| Value | From | To | Expected result (28 digits, half-even) |
| --- | --- | --- | --- |
| `12` | `miles` | `km` | `19.312128` |
| `1` | `in` | `m` | `0.0254` |
| `1` | `ft` | `in` | `12` |
| `1` | `yd` | `ft` | `3` |
| `1` | `miles` | `ft` | `5280` |
| `1` | `lb` | `oz` | `16` |
| `1` | `kg` | `lb` | `2.204622621848775807229738013` |
| `1` | `oz` | `g` | `28.349523125` |
| `1` | `acre` | `ft2` | `43560` |
| `1` | `ha` | `acre` | `2.471053814671653422482439292` |
| `1` | `km2` | `ha` | `100` |
| `1` | `gal_us` | `L` | `3.785411784` |
| `1` | `qt_us` | `mL` | `946.352946` |
| `1` | `L` | `gal_us` | `0.2641720523581484153798999216` |
| `1` | `day` | `h` | `24` |
| `1` | `h` | `min` | `60` |
| `100` | `degC` | `degF` | `212` |
| `32` | `degF` | `degC` | `0` |
| `212` | `degF` | `degC` | `100` |
| `-40` | `degF` | `degC` | `-40` |
| `72` | `degF` | `degC` | `22.22222222222222222222222222` |
| `0` | `K` | `degC` | `-273.15` |
| `0` | `degC` | `K` | `273.15` |
| `300` | `K` | `degF` | `80.33` |
| `1` | `mph` | `m/s` | `0.44704` |
| `1` | `km/h` | `m/s` | `0.2777777777777777777777777778` |
| `100` | `km/h` | `mph` | `62.13711922373339696174341844` |
| `1` | `m/s` | `km/h` | `3.6` |
| `1` | `kWh` | `kJ` | `3600` |
| `1` | `cal` | `J` | `4.184` |
| `1` | `hp` | `kW` | `0.74569987158227022` |
| `1` | `bar` | `kPa` | `100` |
| `1` | `psi` | `Pa` | `6894.757293168361336722673445` |
| `1` | `bar` | `psi` | `14.5037737730209215154241028` |
| `180` | `deg` | `rad` | `3.141592653589793238462643383` |
| `1` | `rad` | `deg` | `57.29577951308232087679815481` |
| `90` | `deg` | `rad` | `1.570796326794896619231321692` |
| `1` | `GiB` | `B` | `1073741824` |
| `1` | `GB` | `B` | `1000000000` |
| `1` | `KiB` | `kB` | `1.024` |
| `1` | `GiB` | `GB` | `1.073741824` |
| `1` | `B` | `bit` | `8` |

## 10. Decisions taken in this draft (to review)

- `miles` is the only spelling of the mile; `mile` is rejected.
- `cal` is the thermochemical calorie (4.184 J), not the International Table calorie (4.1868 J) and not the kilocalorie.
- `hp` is the mechanical (imperial) horsepower, 745.69987158227022 W, not the metric horsepower (735.49875 W).
- `gal_us` and `qt_us` are US liquid measures; `ft`, `yd`, `miles`, `acre` use the international definitions (not the US survey ones).
- `day` is exactly 86400 s.
- Area and volume have only the symbols listed (no `cm2`, `in2`, `ft3`, ...); more may be added in a later catalogue version without changing existing definitions.
- Information uses `bit` as the base unit so that every factor is an integer.
