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

## The refused-entity corpus

Written by `gen/` (`fixturegen corpus`, ACadSharp's `DwgWriter` at 3.7.1), one file per
entity kind the flattener does not emit today, so that issues #82 through #90 have a small
input to probe rather than only the 341-record `real_AC1032.dwg`.

Each is shaped so the plausible wrong implementation fails it. A file that merely contains
the entity would be satisfied by any output at all, which is the defect the `g13_ocs_*`
files were added to stop being possible.

Every one has two halves. The top-level entities produce records with `flags` 0, and the
block in each file produces records with bit 0 set, which `docs/WIRE.md` defines as "came
from expanding a nested insertion". A fixture with only the first half lets through the
defect that actually ships: the kind is implemented, the top-level case works, the fixture
is green, and instances inside an INSERT are dropped or emitted untransformed. Both
insertions are deliberately not the identity, one non-uniform and one mirrored, because a
uniform upright insert exercises the flags bit and nothing else.

What these deliberately do NOT vary is layer, colour, linetype, lineweight or thickness.
The wire carries only `handle` and `flags` from the common entity fields, and the
flattener reads `Thickness` nowhere, so there is no field in any record for those to be
wrong in. Setting them would grow the corpus and assert nothing.

Six refused kinds are absent here and stay on the real drawing, because ACadSharp's
`DwgObjectWriter` has no case for them and its final arm throws `NotImplementedException`:
MULTILEADER, MLINE, 3DFACE, 3DSOLID, REGION and PDFUNDERLAY.

Licence: the writer is ACadSharp (MIT), the content is ours, so these are ours.

| File | sha256 | What it holds |
| --- | --- | --- |
| `g13_point.dwg` | `80fb91f9ae02317dac8fe9e9d234caf29e2bf0152dc030812a488f4157fdcd4e` | AC1032. Four POINTs at top level, one at the origin, one off it, one with a Z, one on a non-Z extrusion; then two more inside a block, inserted non-uniformly and mirrored. |
| `g13_solid.dwg` | `dea64a63b283585bb38b4d5a311ff1aadb9eb57cb399380e180294fe7ceafca1` | AC1032. Three SOLIDs at top level: an asymmetric quad whose corner order distinguishes a bow-tie from a correct polygon, a triangle (fourth corner equal to the third), and one on a non-Z extrusion. A fourth, also asymmetric, inside a block. |
| `g13_ray_xline.dwg` | `332411ca8ee2e05fce04520594ac87952ff7c36d18c3c279ff5abda03a49cdae` | AC1032. A RAY and an XLINE with non-axis-aligned directions plus a bounded LINE that gives the drawing finite extents, then one of each inside a block. The mirrored insertion reverses the half line a RAY covers. |
| `g13_polyface_mesh.dwg` | `ccd27150f2a135ded937e02b2ca10a04c58ce250850267e8537839026e1d3c8e` | AC1032. Two POLYFACE_MESHes of six vertices and two non-coplanar faces, one on +Z and one on a non-Z extrusion, vertices ordered so a line threaded through them in storage order self-intersects. A third inside a block. |
| `g13_polygon_mesh.dwg` | `de3286f404c9a39f707a33b4294f0bdbd433ebdc8f821c3a93afa6535170f322` | AC1032. Two 3x4 POLYGON_MESHes with alternating elevation, one on +Z and one on a non-Z extrusion, so an M/N transposition changes the record. A third inside a block. |
| `g13_mesh.dwg` | `962b4ecde6653787467fc4467e2eb7b08a60027f20a85141c1e6f829e1f12517` | AC1032. A MESH of two non-coplanar faces at subdivision level 2, and a second inside a block. The mirrored insertion is what makes face winding testable. |
| `g13_tolerance.dwg` | `28f3d76765aaf05bac62a0cb7d5fbd9652756fa1caed0506211d3c8a4918f799` | AC1032. Two TOLERANCE feature-control frames with two stacked rows, one on +Z and one on a non-Z extrusion, and a third inside a block. |

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

| File | sha256 | What it holds |
| --- | --- | --- |
| `g13_ac1009.dwg` | `0f7c1ae8358511b569895fd84f9d79cedccda520a8a9a762aeff7ae67223265d` | Not a drawing. The six bytes `AC1009` and then zeros, which is a DWG version this build does not read. ACadSharp's writer cannot produce an AC1009 file, so this one is written byte by byte. |
| `g13_arc.dwg` | `c3b09b470908d35265ef50e2aa70db9551533286778dc9182018cd4f059785b1` | One ARC. |
| `g13_circle.dwg` | `6122b77ff962a84e7b18a07c7a3b92da9934ec82044ca363032d53c45167a523` | One CIRCLE. |
| `g13_deep_blocks.dwg` | `2749f007a82dd4156b61c151ebb6097cb1d36d4551d6e31611b8d39e86fb0f19` | Six blocks nested one inside the next, for the max_block_depth bound. |
| `g13_dimension.dwg` | `1460af678540fef90a9514d020fce2ea9c085e5cc4dacc4bc64f81e96b7bd6d4` | A linear DIMENSION with the block ACadSharp generates for it. |
| `g13_dimension_deep.dwg` | `9cf367a3d2de9c1ab80e4716dc48a3db02f0c4105e6adac5b6e70e484c21fd1d` | Three thousand dimensions, each one living inside the previous one's block. Walking that by recursion is a stack overflow the boundary cannot report, and the recursive walk died at roughly 2686 frames, so the fixture is sized well past it. |
| `g13_dimension_shallow.dwg` | `f62d9df1588434447b034dc0fd4c76e806973c63bc83aea2146ebf0d9aca4a36` | The same shape four deep, which is inside every bound and decodes. Without it, refusing the deep one could be a decoder that cannot read a nested dimension at all. |
| `g13_ellipse.dwg` | `253a70252949dd6521e520dd97effa51c976882e6b9395cb1931d575cb0e677b` | One ELLIPSE with a ratio and a parameter range. |
| `g13_hatch.dwg` | `c8697b49ce3eed4bffee80d60c69963d44bf3aa5c1661a33021737b153970da9` | Four HATCHes: a loop of straight edges, a loop with a counter-clockwise circular arc, the same shape with the arc traversed clockwise, and a loop with a spline edge. The clockwise one is there because a boundary arc's direction is a flag rather than a sign on the sweep, and with only counter-clockwise loops a converter that ignored the flag was right on every fixture there was. |
| `g13_insert.dwg` | `c8c1fcb0bdbd34c634c64ffc36421ffac7c59332e310018d9549986f7d5c86d1` | An INSERT of a block that itself inserts a second block, scaled and rotated. The malformed derivatives are cut from this one. |
| `g13_line.dwg` | `9e914e1c1cf03c55b88813679e1aa594b35b9c7c039c6debec799a8bc87c1476` | One LINE on a named layer. |
| `g13_long_text.dwg` | `b9c8b8f42779c6785ff9d19e8f8dfc48384739423978dc41065f8d722c126775` | One MTEXT of 8192 bytes, for the max_string_bytes bound. |
| `g13_many_inserts.dwg` | `72bb475c6071a567d7c672005518bc7e7323b0450cdbca00ebe9404e38047387` | One three-entity block inserted ten thousand times. The amplification benchmark is a decode of this. |
| `g13_mirrored_bulge.dwg` | `4f947ae65e647dbca558eb1d67ba961a38733718f20c8be66b8e197ffd39faaf` | An LWPOLYLINE carrying a bulge, inside a block inserted with `XScale` -1. The two scale magnitudes are equal, so the only thing the transform does that a rotation cannot is change handedness, which is what flips the side of the chord the arc bulges to. |
| `g13_nan_bulge.dwg` | `85e3873b3ba01f03e1451e4dc2e941c432b69b39b03c8ce5e3e502d9e50c39ae` | Two LWPOLYLINEs: one whose bulge is `NaN`, one whose second vertex has an infinite X. Nothing in a drawing has to be finite, and the corpus had no file that carried a value that is not. |
| `g13_nonuniform.dwg` | `e6175bd09a45f390085521c55b42837f049af1f2a3225bf1f1620782150aac7b` | A CIRCLE inside a block inserted with unequal X and Y scale. |
| `g13_polyline.dwg` | `815bf452eb40b0c5f75049e3155f13c0c2f3272b2d3464b92d01ee3636635bd3` | An LWPOLYLINE carrying a bulge, a 2D POLYLINE and a 3D POLYLINE. |
| `g13_scale_16x.dwg` | `a88ecd131e361a1db298d2fef7d20caeb2157cb2edb38456675cdb54b63414cd` | 2048 entities, the 16x point. |
| `g13_scale_1x.dwg` | `b01dd0ad9a175400e6ba6d1c033e8df862a3a85264efda9dfbe362531617a087` | 128 entities, the 1x point of the streaming measurement. |
| `g13_scale_4x.dwg` | `99ded530c49c5f23c2d7a02c62fdb9296cb647f0a1c32c09b849a7ab4a1641c1` | 512 entities, the 4x point. |
| `g13_slot.dwg` | `db6c6facccbce67169329528279cba795d4ddb0aaec26bbb8fe04f6475c68ee6` | One closed LWPOLYLINE shaped like a slot: two straight sides and two semicircular ends, bulges `[0, 1, 0, 1]`. The closing span carries a bulge, and a bulge of exactly 1 is a half turn, which is the largest sweep one span can hold. |
| `g13_slot_block.dwg` | `41245b50e234db83f45be266a88539665b77f21833a14d2fd05a050fb91f933d` | The same slot in a block, inserted three times. Every instance emits the block entity's own handle, so this is the file that says whether a consumer can tell three slots from one. |
| `g13_spline.dwg` | `e2540a81ed652694385e89fba90eec7462c6a67e7d47cb3af45f4edd879b1a5a` | One SPLINE, degree 3, four control points and eight knots. |
| `g13_text.dwg` | `c791143334c6d2ccda33a72e7e85fc2fd93e2c1b8e8acd5a75e1b2254aa54e0f` | One TEXT and one MTEXT. |
| `g13_two_entities.dwg` | `d2955fa942a735f18c57a6cacf3e46175de2f6e2e76e0eea4d5697a3faaee757` | Exactly two LINEs, for the max_entities bound. |
| `g13_unsupported.dwg` | `1a0387b575f894ac8315a1075b0e2399bd3473d24d3eb5b72a2a08f7f62df365` | A POINT and a SOLID, neither of which this version flattens. |
| `g13_wide_polyline.dwg` | `63495a02a7ef1f359f4be91b1964652ac099b6ac352c986367938882aecd1761` | One LWPOLYLINE of 4096 vertices, for the max_polyline_points bound. |
| `g13_wide_spline.dwg` | `dfd4283a4f67ec1a63897e6a9e94a2bc898c7b5123736eccea5b8117ee4fd6f8` | One SPLINE of 20000 control points. max_polyline_points did not apply to splines at all, and the encoder's own guard ran after the points were already gathered. |
| `g13_xref.dwg` | `4e78ce6186919f14ccfe47a442a787c918391070760d5de27962c76887f363ae` | An INSERT of a block record that names an external reference nothing resolves. |
| `g13_xref_long.dwg` | `dbd8b31faf5c6dda27c61de9923179cd01c825389aac223844758624abe1f8b4` | The same INSERT with an 8192-byte reference path, so the warning this library writes about it is longer than a max_string_bytes a caller can set. The path is padded with U+00E9, two bytes each, because the budget a 4096-byte bound leaves is odd and a cut that counted bytes would split one. |

Licence: the writer is ACadSharp (MIT), the content is ours, so these are ours.

Three shapes are not here, and not for want of trying. They are recorded
because the next person will reach for them too.

* **A cycle.** Two dimensions whose blocks hold each other, or two block
  records each inserting the other. ACadSharp cannot build either: a
  `CadObject` gets exactly one owner and `Add` refuses a second, and
  `new Insert(record)` deep-clones a record that belongs to a document, so the
  second edge recurses until the stack goes. A file from another writer can
  carry one, and the walk refuses it the same way it refuses
  `g13_dimension_deep.dwg`, by depth and by entity count, neither of which
  cares whether the graph closes.
* **A fan-out as a file.** A chain of block records each holding two
  insertions of the next. Building it in memory is fine as long as the root
  insertion is made before the chain is filled, but `DwgWriter` does not come
  back from writing one. It is built in memory by the generator instead and
  driven through the flattener directly, which is what the hole was about
  anyway.
* **A string past the 64 KiB default `max_string_bytes`.** ACadSharp's DWG
  round trip silently shortens a long MText value: 8,192 characters come back
  whole, 40,000 come back as 7,232, 70,000 as 4,464 and 200,000 as 3,392.
  `g13_long_text.dwg` exercises the bound with a limit the caller sets.

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
