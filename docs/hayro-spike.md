# Spike: can hayro replace pdfium as libviprs's PDF backend?

This is the write-up for #164. I took a first pass at it with hayro 0.9.0 (crates.io, pure Rust, MIT or
Apache-2.0) and the answer for now is: **keep both behind a feature, do not replace yet**. The harness and the
raw numbers are in [`tools/hayro-spike`](../tools/hayro-spike) (`results.csv` has one row per PDF).

## What I ran

A small binary renders page 1 of 36 PDFs to 2000 px on the long side, and pypdfium2 renders the same page at
the same scale. The 36 are the 35 arch, ISO and US sheets from the `pdf-candidates` set plus an exported DWG
(`exported.pdf`). The PDFs themselves are not committed here, the names are in `results.csv`. Native x86_64
container on MARS. Hayro's time includes process start, parse and PNG encode while pdfium's is render only, so
the speed numbers are an upper bound for hayro.

## Numbers (36 of 36 rendered)

| | median | worst |
|---|---:|---:|
| pixels differing by more than 32 grey levels | 2.6% | 9.6% (`bbmp-ward1-2020-A2`, a dense A2 ward map) |
| mean absolute grey difference | 2.7 | 11.0 |
| hayro wall time | 0.24 s | 5.8 s (`census_metro_micro_06_California_2020`) |
| pdfium render time | 0.055 s | 2.4 s |
| hayro peak RSS | 28 MB | 621 MB (`architectural-orders-table-A0`) |

- No panic and no timeout on any of the 36.
- The ten worst by differing pixels are `bbmp-ward1-2020-A2`, `architectural-orders-table-A0`,
  `census-harrisonburg`, `f8949`, `f1040`, `USTopo_quickstart`, `usace_swf_gpw_daca67-06-d-0029`,
  `chorographia-naumburg-A3`, `usace_sam_tda_adjacency_diagrams_2026` and
  `usace_norfolk_hoskins_creek_dredging_plans`. I have not looked at them by eye yet.
- Most of the difference is thin linework landing half a pixel apart (hayro's pixmap comes out one pixel smaller
  than pdfium's on a few sheets, and I resize it to compare). Read the percentages as a lower bound on
  disagreement, not as a fidelity verdict.

## Encryption, the one hard gap

Hayro refuses a user-password PDF: `Decryption(PasswordProtected)`, and its README lists encrypted files as a gap.
A file with only an owner password opens fine (`enc/owner-password-only.pdf` in my run). libviprs has a
user-password fixture, so swapping today would be a regression.

## Not covered yet

- The rotation-correctness goldens in libviprs-tests and the large architectural sheets at full resolution.
- Tile or strip rendering. Hayro's render call takes one pixmap with `u16` dimensions, so strips need
  `render_into` with a transform, which I have not tried.
- JBIG2, JPX and CCITT sheets, hostile input and broken xref recovery, and thread behaviour (whether it needs
  anything like `pdfium_lock()`).
- An API fit check against what libviprs exposes (no pdfium-render types leak out today, so the swap should stay
  internal).

## Recommendation

Keep both backends behind a feature: pdfium stays the default, hayro is an opt-in `pdf-hayro` backend that
libviprs can select per document, and a password-protected PDF falls back to pdfium (or is refused with a typed
error that says which backend could not open it). That lets the two run side by side on real sheets before anyone
decides to retire this repo's pdfium binaries. Replacing outright needs encrypted-PDF support and a strip story
first, and I would not open the migration issues until both exist.

What a follow-up would build, if you want it: the backend feature in libviprs, a fidelity cell per rotation golden
against both backends, and an A0 strip run measuring peak memory.
