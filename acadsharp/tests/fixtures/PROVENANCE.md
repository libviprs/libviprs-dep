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

## The G1.3 corpus

Written by `gen/` as well (`fixturegen corpus`, ACadSharp's `DwgWriter` at
3.7.1) and committed for the same reason: a DWG header carries creation and
update timestamps, so regenerating produces different bytes and an expectation
compared against a freshly written file would be comparing two different files.
One file per entity kind the adapter flattens, plus the ones that exist only to
be refused. `tests/expectations/MANIFEST.json` pins these digests a second time
and the stream tests recompute them, so changing a fixture without rerunning
the generator is a red test that names the fixture.

| `g13_ac1009.dwg` | `0f7c1ae8358511b569895fd84f9d79cedccda520a8a9a762aeff7ae67223265d` | Not a drawing. The six bytes `AC1009` and then zeros, which is a DWG version this build does not read. ACadSharp's writer cannot produce an AC1009 file, so this one is written byte by byte. |
| `g13_arc.dwg` | `df3e472bef51cd4ceae3bde9297d674173ef2eb9ef006d01859e4182c0ad484e` | One ARC. |
| `g13_circle.dwg` | `09489d5c2d91eb7f0d71ef99a5e5ad926fef5f02e6f83b75505aeb978f5a3e8d` | One CIRCLE. |
| `g13_deep_blocks.dwg` | `b6cc1596101d2a1395eb734b780525b17d2b3843383185a0bcfa6b1cd1c1e564` | Six blocks nested one inside the next, for the max_block_depth bound. |
| `g13_dimension.dwg` | `18bccc3672409d6d976edeee43f8199c56bdb408a159dc0abb3f67a0e5af9f2e` | A linear DIMENSION with the block ACadSharp generates for it. |
| `g13_ellipse.dwg` | `50dfc3a7936dab60d033b420c08767c3a014d136e20dc37f6a92ffe828330cc4` | One ELLIPSE with a ratio and a parameter range. |
| `g13_hatch.dwg` | `7fc8f78d914b7245863f63eeec90d0258c7282969cff659c90b0a6bb2aa2d6fd` | Four HATCHes: a loop of straight edges, a loop with a circular arc, a loop with a spline edge, and one with no boundary at all. |
| `g13_insert.dwg` | `68e912765e1c7ccef106fe6bd136d6fdd4fd779d959f127d1773399cc0ac7fac` | An INSERT of a block that itself inserts a second block, scaled and rotated. The malformed derivatives are cut from this one. |
| `g13_line.dwg` | `7f813d1853d0abe63654b168a7daacad34214432afc157589271c7ca2a825ce0` | One LINE on a named layer. |
| `g13_long_text.dwg` | `7f45af814c71c87d7360b54ea36a20a971be1e9e8fdeda8736ae8988bfe2d2c5` | One MTEXT of 8192 bytes, for the max_string_bytes bound. |
| `g13_many_inserts.dwg` | `dc7e02a78708663a139a58b176a68b053ee94ac802a10ffb2631ac9bc32e1af1` | One three-entity block inserted ten thousand times. The amplification benchmark is a decode of this. |
| `g13_nonuniform.dwg` | `32e5583a9e78d6d29bccc1e2d150708c110248b741e9a71f42e02560759e05b5` | A CIRCLE inside a block inserted with unequal X and Y scale. |
| `g13_polyline.dwg` | `3bfb5f11bc681c15d4829a2ede8923936210da16c3a054a1fcaa4a614058f0b8` | An LWPOLYLINE carrying a bulge, a 2D POLYLINE and a 3D POLYLINE. |
| `g13_scale_16x.dwg` | `b68e2878c916dc2baa2d00fbe1e1cefbb1eff0bd5baf6048b5b177eaf489585b` | 2048 entities, the 16x point. |
| `g13_scale_1x.dwg` | `40a0a8c92c8c731a72748c9f3685c5a14b3f6e2a8ab832c1aeb2d25c481c7cdf` | 128 entities, the 1x point of the streaming measurement. |
| `g13_scale_4x.dwg` | `5f122fbc40fce247aa960b7e06f782de1c07a891483fe743f59c85d69a0d5149` | 512 entities, the 4x point. |
| `g13_spline.dwg` | `7cbd150685a1ac657db15a053a86471497170e0948cc9b2611be8b25fff1f4d6` | One SPLINE, degree 3, four control points and eight knots. |
| `g13_text.dwg` | `1e7d3881498763aebfef3aa90d3ceb2c70786ab993d009f68a1458ac2346b530` | One TEXT and one MTEXT. |
| `g13_two_entities.dwg` | `7fa13b20d5d3f0f748e62e1f3345163f155de19d5f4379ad4942f62942b72645` | Exactly two LINEs, for the max_entities bound. |
| `g13_unsupported.dwg` | `fda0963b0ecaab011a1d309bfd78e9e1e5b898dd10a4f2cd097fdd0f9a59dd7b` | A POINT and a SOLID, neither of which this version flattens. |
| `g13_wide_polyline.dwg` | `1240e92c3a3ff26c68381b7f7b25bda46f3d756d8be5283a881ea74c3220b4b4` | One LWPOLYLINE of 4096 vertices, for the max_polyline_points bound. |
| `g13_xref.dwg` | `2824107829ce93938479662f0e3126397ea756cea384ea062d5a892ec4f70f57` | An INSERT of a block record that names an external reference nothing resolves. |

Licence: the writer is ACadSharp (MIT), the content is ours, so these are ours.

Two files are not committed because nothing needs them to be. The malformed
derivatives (`g13_insert.dwg` truncated at 25, 50 and 90 percent, and with 64
bytes flipped under seed 4713) are derived at test time from the fixture above,
so the test cannot drift from the file it claims to mutilate. The padded input
the path-versus-memory measurement uses is `g13_many_inserts.dwg` with 32 MiB
of pseudorandom bytes appended, built by `regenerate.py` and thrown away,
because committing 32 MB to make one RSS number legible is a bad trade.

## The notification fixture

The issue asks for a file that produces a reader notification, and says the
writer will not emit one. It turned out both halves of that are true and one is
a surprise:

* `real_AC1032.dwg`, already in this directory, is the real-world case. It
  carries objects ACadSharp has no class for (`ACDBASSOCPERSSUBENTMANAGER`,
  `WIPEOUTVARIABLES`, `ACDBDETAILVIEWSTYLE` and others) and reading it raises
  31 notifications, every one of which crosses as a `Warning` record. That is
  the fixture the warning tests use.
* Every file `DwgWriter` produces also raises four, all the same one: a
  `TableStyle.CellStyle` referencing a `TextStyle` by an empty handle and an
  empty name. That is an upstream round-trip gap, not something this corpus
  arranged, and it is recorded here because it is in every expectation in
  `tests/expectations` and someone reading one will wonder.
