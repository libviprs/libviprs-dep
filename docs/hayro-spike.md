# Spike: can hayro replace pdfium as libviprs's PDF backend?

This is the write-up for #164, and the follow-up in #170 that measured what the first pass left open. I used
hayro 0.9.0 (crates.io, pure Rust, MIT or Apache-2.0) and the answer is still: **keep both behind a feature,
do not replace yet**. What changed is that I now know which parts are fine (fidelity, formats, threads,
passwords, xref recovery) and which three are blockers for a strip-based or untrusted-input pipeline (the cost
of strips, unbounded allocation, a pixmap ceiling below `u16::MAX`). The harness is in
[`tools/hayro-spike`](../tools/hayro-spike) and every number below is a row in a CSV under
`tools/hayro-spike/results/`.

## What I ran

The first pass (#164) rendered page 1 of 36 PDFs to 2000 px and compared with pdfium, see the numbers further
down. For #170 I added one harness mode per open question (`rotation`, `fullres`, `strips`, `formats`,
`hostile`, `threads`, `password`) and `run_cases.py`, which drives them and writes the CSVs. Fixtures are
the libviprs-tests canonical rotation PDFs and goldens, its two password files, three codec sheets I generate
(`gen_fixtures.py codecs`) and the public sheets from the pdf-candidates set. The sheets are not committed, the
names are in the CSVs. `fixtures/MANIFEST.csv` has a hash and an origin for every committed file.

Where and how: a Docker Desktop VM on an Apple Silicon laptop (arm64, 4 CPUs fenced, 8 to 10 GB), native, no
emulation. The arm64 bench host was unreachable that day, and the first pass ran on x86_64, so absolute times
here are not comparable with the table in the last section. The pdfium side is pypdfium2 5.15.2 with its
bundled pdfium 159.0.8149, not the build this repo pins, and every pdfium call runs in a child process so a
crash is a row. Hayro's time is parse plus render with no PNG encode (pixels go to a raw file outside the timer),
pdfium's is open plus render, both in-process, so the two are now close to like for like. The laptop was not
quiet (load around 4), so read times as rough, and read ratios more than seconds.

One sizing rule applies to everything from here on: hayro's plain `render()` truncates `width * scale` into a
`u16`, so for a page that is not a whole number of points it comes out a pixel smaller than pdfium
(`password.pdf`, 595.28 by 841.89 pt, renders 595x841 where pdfium gives 596x842). That one pixel is what
inflated the first pass's difference numbers. From here the harness calls `render_into` with a transform that
stretches the page onto exactly the pixel size pdfium picked, so both sides compare pixel for pixel.

## Rotation goldens

I rendered the four `canonical*.pdf` files (`/Rotate` 0, 90, 180, 270) at 72 dpi and compared per pixel
against the libvips-plus-pdfium goldens in libviprs-tests, with the same metric its
`rotation_libvips_pdfium_parity.rs` uses (mean absolute difference over all RGBA channels, tolerance 2.0).

| file | size (golden and hayro) | mean abs diff | pixels over 32 levels |
|---|---|---:|---:|
| canonical.pdf | 595x842 | 0.080 | 0.168% |
| canonical_rotated_90.pdf | 842x595 | 0.080 | 0.168% |
| canonical_rotated_180.pdf | 595x842 | 0.080 | 0.168% |
| canonical_rotated_270.pdf | 842x595 | 0.080 | 0.168% |

All four are the right orientation and size, and sit about 25x inside the tolerance. The historical bug
(a double-applied rotation) scored 78 to 99 on this metric, so a transposed page cannot hide here. For
comparison pypdfium2's pdfium scores 0.000 against the same goldens. Hayro applies `/Rotate` itself through
`initial_transform`, so the strip matrix does not need a per-rotation term, same as the pdfium path.

## Full resolution A0

Three A0-class sheets at 150 and 300 dpi, whole page in one pixmap, against pdfium at the size pdfium picked.
Memory is peak RSS of the process.

| sheet | dpi | pixels | hayro time | hayro peak | pdfium time | pdfium peak | mean abs grey diff | over 32 levels |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| arasikere-gp-2015-A0 | 150 | 5100x6601 | 0.05 s | 157 MB | 0.13 s | 175 MB | 0.73 | 0.59% |
| arasikere-gp-2015-A0 | 300 | 10200x13201 | 0.11 s | 562 MB | 0.35 s | 504 MB | 0.49 | 0.26% |
| architectural-orders-table-A0 | 150 | 7022x4967 | 0.31 s | 621 MB | 0.46 s | 605 MB | 1.97 | 1.87% |
| architectural-orders-table-A0 | 300 | 14044x9934 | 0.49 s | 977 MB | 0.50 s | 1060 MB | 1.72 | 1.38% |
| covid-vaccination-sites-england-A0 | 150 | 4967x7022 | 2.56 s | 276 MB | 8.49 s | 632 MB | 1.26 | 0.23% |
| covid-vaccination-sites-england-A0 | 300 | 9934x14044 | 5.21 s | 716 MB | 28.41 s | 1829 MB | 1.11 | 0.09% |

All six rendered, none panicked, and each sheet has 4 to 13% ink in pdfium's render so these are not blank
pages agreeing with each other. Hayro is no slower than pdfium on these three and clearly faster on the one
with soft masks. Memory is in the same range as pdfium, and a 140 MP pixmap is 560 MB by itself, so a whole-page
A0 at 300 dpi needs about 1 GB.

**The pixmap ceiling is below `u16::MAX`.** `RenderContext::new` takes `u16` dimensions, and the rasteriser
panics before it gets there: a 65400 px wide pixmap renders, 65450 panics in `vello_cpu` (`span.rs:88`), 65535
and the plain `render()` at 89250 px panic in `vello_common` (`util.rs:237`). The plain `render()` does
not saturate quietly, it panics. So the backend has to cut anything wider or taller than about 65000 px into
pieces and never hand hayro a bigger size. Rows `u16-limit-*` in `fullres.csv`.

## Strip and tile rendering

`render_into` takes a `RenderContext` of the piece's size and an `Affine`, so a strip is the page transform
with a `translate(-x0, -y0)` in front. It works, and stitched pieces match the single-shot render:

| case | pieces | max abs diff | pixels that differ |
|---|---:|---:|---:|
| four canonical rotations, strips of 100 rows and tiles of 128 | 6 to 35 | 0 | 0 |
| arasikere A0 at 150 dpi, strips of 256 rows | 26 | 2 | 7273 of 33.7 M |
| arasikere A0 at 150 dpi, tiles of 512 | 130 | 69 | 12972 of 33.7 M |
| architectural-orders A0 at 150 dpi, strips of 256 | 20 | 1 | 225 of 34.9 M |
| architectural-orders A0 at 150 dpi, tiles of 512 | 140 | 1 | 500 of 34.9 M |

Strips are a seam-free tiling to within 2 grey levels. Tiles have a few pixels with a larger difference
(69 levels on 0.04% of one sheet), so I would not call tiles bit-exact.

**The cost is the problem.** Every piece interprets the whole content stream again and decodes every image
that touches it again. Time of all pieces against one single-shot render, same sheet and size:

| case | single shot | all pieces | ratio | peak RSS, single shot / pieces |
|---|---:|---:|---:|---|
| arasikere 150 dpi, 26 strips | 0.045 s | 0.29 s | 6.5x | 157 MB / 19 MB |
| arasikere 150 dpi, 130 tiles | 0.044 s | 0.79 s | 18x | 157 MB / 14 MB |
| architectural-orders 150 dpi, 20 strips | 0.31 s | 5.0 s | 16x | 770 MB / 636 MB |
| architectural-orders 150 dpi, 140 tiles | 0.30 s | 35.0 s | 118x | 766 MB / 628 MB |
| architectural-orders 300 dpi, 39 strips | not run | 7.8 s | n/a | n/a / 754 MB |

The memory win is real on a vector sheet (157 MB down to 19 MB) and almost gone on a raster sheet, because the
JPEG is decoded whole for every strip (636 MB of 770 MB). Pdfium's crop-based strips cost about 1x on both
sheets (0.09 s against 0.09 s, 0.40 s against 0.40 s), so this is hayro-specific. The `RenderCache` is shared
across the pieces in my runs and that did not rescue it. Tiles are out. Strips only make sense when the pixmap
cannot fit in memory.

## JBIG2, JPX and CCITT sheets

Real sheets in the sets: CCITT on page 1 of `usace_swf_gpw_daca67-06-d-0029`, JPX on
`usace_norfolk_hoskins_creek_dredging_plans` and on `tm11b3`. There is no JBIG2 sheet in the sets, so I
built three small ones (`gen_fixtures.py codecs`, A4 bilevel text page at 150 dpi): JBIG2 generic region and
JBIG2 symbol dictionary from jbig2enc 0.32 (Apache-2.0, built from source, used only as an external tool to make the fixtures and never committed or linked), and CCITT G4 from Pillow. Everything rendered at 2000 px on the long
side.

| codec | file | hayro | pdfium | mean abs diff vs pdfium | vs the source bitmap |
|---|---|---:|---:|---:|---|
| CCITT G4 | usace_swf_gpw (real) | 0.05 s | 0.09 s | 1.67 | n/a |
| CCITT G4 | codec-ccitt-g4 | 0.007 s | 0.005 s | 6.02 | identical, 0 pixels differ |
| JPX | usace_norfolk (real) | 0.18 s | 0.45 s | 5.04 | n/a |
| JPX | tm11b3 (real) | 0.13 s | 0.14 s | 1.46 | n/a |
| JBIG2 generic | codec-jbig2-generic | 0.009 s | 0.013 s | 6.02 | identical, 0 pixels differ |
| JBIG2 symbol | codec-jbig2-symbol | 0.007 s | 0.007 s | 6.02 | identical, 0 pixels differ |

The three generated sheets map the image 1:1 onto a 1240x1754 pixmap, and hayro reproduces the source bitmap
exactly, so those decoders are right. The 6.02 against pdfium on the same files is resampling, not decoding:
at 2000 px the image is scaled up 1.14x, hayro samples it harder-edged and pdfium smooths it. For the real JPX
sheet at 5.04 I looked at both renders side by side and they read the same, with hayro a touch lighter (58.6%
of pixels dark against pdfium's 63.0%). What I did not cover is a scanner JBIG2 with halftone or refinement
regions, or CCITT group 3, so those stay open.

## Hostile input and xref recovery

`gen_fixtures.py hostile` builds 28 inputs from one small base page, `hostile.csv` has a row each, and every
one runs in a child process with a 90 s timeout and a 6 GB address-space limit.

- **Hayro: 23 rendered, 4 refused with a typed error, 1 aborted, 0 panics, 0 stack overflows, 0 hangs.** The
  four refusals are empty, noise, header only and 100000 nested dictionaries. Pdfium refuses four (the first
  three and the file cut in half) and renders the nested dictionaries. For the 1e9 pt MediaBox my pdfium child
  declines to render at all (its own page-size guard), where hayro scales the page to 800 px and renders it.
- **Xref recovery is good.** With the table missing, `startxref` wrong or past the end, every offset 11 bytes
  off or zero, 1 KiB of junk before the header, 64 KiB after the last `%%EOF`, no `%%EOF`, and a wrong stream
  `/Length`, hayro rendered the same picture as the undamaged base, byte for byte, every time (so did pdfium).
  On the file cut in half hayro renders something and pdfium refuses.
- **The one abort is a real hole.** A 1e6 pt line stroked with a 0.001 pt dash pattern makes hayro ask for a
  7.5 GB allocation, and Rust turns a failed allocation into `abort`, not a panic, so `catch_unwind` does not
  help and the process dies after 1.27 s. Pdfium draws the same file in under a millisecond (0.6 ms).
- **Decompression is not capped.** A 1 MB Flate content stream that inflates to 1 GiB costs hayro 1065 MB of
  RSS in 0.52 s (pdfium used 2094 MB in 0.98 s). A Flate image declared 32768 by 32768 that really inflates to 1 GiB
  costs hayro 1065 MB and 2.2 s, where pdfium finishes in 0.24 s at 64 MB. Both grow linearly with the file
  claim, so a hostile upload can pick its own memory bill.
- Recursion is fine: a form XObject and a tiling pattern that paint themselves, a page tree that contains
  itself, a `/Length` that references itself, a million `q`, 200000 nested `[`, a 400000 segment path (0.13 s,
  170 MB) all finish in under 0.2 s with a pixmap.
- 6000 byte-level mutants (flips, truncations, cut chunks) of two small files: hayro 3933 rendered, 2067
  refused, 0 panics, slowest mutant 0.8 ms. Pdfium on its own 6000 mutants rendered 2242 and
  refused 3758. The two use different random streams, so only the shape compares: hayro is the more
  forgiving reader, and not once did a forgiven file crash it.

So hayro is safe against malformed structure and unsafe against allocation size. Used on files libviprs
does not trust, it needs a memory cap around it and a process boundary, because the failure mode is an abort.

## Thread behaviour

- `Pdf` is `Send` and `Sync`. `RenderContext` is `Send`. `RenderCache` is not `Send`, it holds `Rc` caches
  (compile probes in `examples/`, rows `probe` in `threads.csv`).
- No `static mut` and no `thread_local!` in the hayro crates or `vello_cpu`. The only globals are `LazyLock`
  and `OnceLock` lookup tables that never change after first use.
- Twelve renders of an A2 map at 1500 px on the laptop VM, one `RenderCache` per render, 1, 2 and 4 threads:

| threads | own Pdf per thread | one shared Pdf |
|---:|---|---|
| 1 | 0.30 s | 0.31 s (0.98x) |
| 2 | 0.17 s (1.8x) | 0.15 s (2.0x) |
| 4 | 0.11 s (2.7x) | 0.084 s (3.6x) |

  Peak RSS goes from 25 MB to 76 or 79 MB at four threads. Every render in every run was byte-identical to the
  single-threaded one.

So a `pdfium_lock()`-style global mutex is not needed for hayro: nothing is shared mutably, a `Pdf` can sit
behind an `Arc` and be read from every thread, and each worker builds its own `RenderCache`. The lock keeps
guarding the pdfium side. Within one render hayro is single-threaded here (`vello_cpu`'s multithreading
feature is off, there is no rayon in `Cargo.lock`), so parallelism comes from rendering several pieces or pages at once.

## Password files

Correction to the first pass: hayro does not refuse every user-password PDF. `hayro-syntax` 0.9 has
`Pdf::new_with_password`, and the README's "no encrypted files" is out of date for it. Against the two
libviprs-tests files:

| case | hayro | pdfium |
|---|---|---|
| user password, none given | refused, `Decryption(PasswordProtected)` | refused |
| user password, wrong one | refused, the same variant | refused |
| user password, right one (`secret`, AES-256 R6) | renders | renders |
| owner password only, none given | renders | renders |
| owner password only, owner password given | renders | renders |

Two things to design around. Hayro uses one error for "needed" and "wrong", so libviprs's
`PasswordRequired` against `WrongPassword` has to come from whether the caller passed a password. And I only
have an AES-256 R6 file, so RC4 and AES-128 files are untested.

## Decision

The new numbers move the recommendation from "keep both, wait for encryption" to "keep both, and here is what
a hayro backend must do on day one".

**Recommendation: add an opt-in `pdf-hayro` Cargo feature next to `pdfium`, off by default, selected per
document, with pdfium as the fallback.** Fidelity is inside the existing tolerances (0.08 on the rotation
goldens, 0.5 to 2.0 grey levels on A0), the codecs decode, thread use needs no lock, and passwords work. Build
it with these rules:

- Render whole pages in one shot, and use strips only when the pixmap cannot fit in memory, in large strips
  (256 rows costs 6 to 17x, tiles cost up to 118x).
- Never ask for more than about 65000 px a side in one piece.
- Run it where a memory cap and a process boundary exist, and treat it as a trusted-input backend until the
  allocation holes are closed. An untrusted file goes to pdfium, or to hayro inside a sandbox.
- No global lock for hayro. Share `Pdf` behind an `Arc`, one `RenderCache` per thread.
- Pass the password through to `new_with_password` and derive "needed" or "wrong" from whether one was given.
- Size pixmaps explicitly with `render_into`, as the harness does, instead of the plain `render()`.
- Give the hayro cells their own pixel tolerance in the parity tests rather than reusing pdfium's.

**Alternative A: stay on pdfium only.** No new backend, no second tolerance, and the hostile-input story stays
what it is today (pdfium gets through the Flate image bomb in 0.2 s and the dash case in under a millisecond). The price is carrying the pdfium
binaries for every target forever, and no route to a pure-Rust build. I would pick this if the strip and
untrusted-input blockers matter more than a second backend.

**Alternative B: make hayro the default and pdfium the opt-in, once upstream fixes three things.** The three
are cheap strips (decode images once and reuse them across pieces), allocation caps that fail with an error
instead of an abort, and a pixmap ceiling that is the type's maximum. This is the end state that retires this
repo's pdfium archives, but today it would make libviprs's strip pipeline 6 to 118x slower and its untrusted
input path abort-prone, so it is not buildable yet. I would revisit it when those land.

## The first pass: 36 sheets at 2000 px

A small binary rendered page 1 of 36 PDFs to 2000 px on the long side and pypdfium2 rendered the same page at
the same scale: the 35 arch, ISO and US sheets from the `pdf-candidates` set plus an exported DWG
(`exported.pdf`). Native x86_64 container on a shared build host. The pdfium baseline is pypdfium2's bundled
pdfium. Hayro's time there includes process start, parse and PNG encode and pdfium's does not, so the speed
numbers are an upper bound for hayro. These are the numbers from #164, kept as they were, `results.csv` has the
rows.

| | median | worst |
|---|---:|---:|
| pixels differing by more than 32 grey levels | 2.6% | 9.6% (`bbmp-ward1-2020-A2`, a dense A2 ward map) |
| mean absolute grey difference | 2.7 | 11.0 |
| hayro wall time | 0.24 s | 5.8 s (`census_metro_micro_06_California_2020`) |
| pdfium render time | 0.055 s | 2.4 s |
| hayro peak RSS | 28 MB | 621 MB (`architectural-orders-table-A0`) |

All 36 rendered, no panic and no timeout. The pixmap was one pixel smaller than pdfium's on all 36, and
`run.py` resized hayro's image to pdfium's before diffing, which overstates the difference (it counts
resampling as disagreement). The per-sheet differences in the A0 table above, taken at exactly pdfium's size,
are the better fidelity number: 0.5 to 2.0 grey levels.

## Rerunning it

```
cd tools/hayro-spike
cargo build --release
python3 run_cases.py all        # or rotation, fullres, strips, formats, hostile, threads, password
python3 gen_fixtures.py codecs  # only to rebuild the JBIG2 and CCITT sheets (needs jbig2enc)
```

`run_cases.py` wants pypdfium2, Pillow, numpy and pikepdf, and the pdf-candidates sheets in `PDFS`
(default `../../pdfs-all`, with `arch/`, `iso/` and `us/` under it).
