# Fixture provenance

Every DWG in this directory, where it came from, and under what licence.

## Generated here

Written by `gen/` (ACadSharp's own `DwgWriter`, version 3.7.1) and committed so
the JIT and NativeAOT captures in `captures/` are reads of one fixed file rather
than of whatever a rerun happened to produce. Regenerating them produces
different bytes, because the DWG header carries creation and update timestamps.

| File | sha256 | What it holds |
| --- | --- | --- |
| `g11_shapes.dwg` | `e54abbe4b4b24499783edc1d5c7251db66fa1683fb0197f2f3704b3eeee45813` | AC1032. A line on a named layer, a circle, an arc, text, a block insert, plus header variables and model-layout extents set well away from ACadSharp's defaults. |
| `g11_codepage.dwg` | `04ae1bca9e384c9604aff632b838a3497236daf9d79be7b073148a696d12b089` | AC1018, where DWG stores text against a code page rather than as unicode, with text that Windows-1252 can only partly represent. |

Licence: the writer is ACadSharp (MIT), the content is ours, so these are ours.

## From upstream

ACadSharp ships sample drawings produced by AutoCAD itself. A round-trip of our
own writer only tests the reader against the writer; a real file is what
production sees, so two of them are here.

| File | sha256 | Source | Licence |
| --- | --- | --- | --- |
| `real_AC1032.dwg` | `0e8faaca949c9429c92240082d9ea4d3524aca5baf7c7815ec439245c321b528` | `samples/sample_AC1032.dwg` in <https://github.com/DomCR/ACadSharp/archive/refs/tags/v3.7.1.tar.gz> | MIT |
| `real_AC1018.dwg` | `7d26f908516ec5fb89cc8054af33aff233b68a7b63208bbc5b7f9f4d9bff9866` | `samples/sample_AC1018.dwg` in the same tarball | MIT |

`real_AC1032.dwg` is the one that carries the spike's weight: 163 entities over
36 entity types, 19 layers, 27 block records, 4 layouts, including dimensions,
hatches, multileaders, a table, a raster image and a PDF underlay.
