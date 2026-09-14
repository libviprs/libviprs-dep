# ADR 0002: what this decoder refuses, and why each one

**REFUSED by decision, not by backlog.** Eight entity kinds now leave a warning
that says somebody looked. Seven of them carry the new code 109,
`ENTITY_REFUSED_BY_DESIGN`, because the answer will not change by waiting:
their geometry is outside the drawing, or it is in a form nothing on this
boundary evaluates, or no record wire version 2 defines can hold it. The
eighth, `WIPEOUT`, stays on code 100 because it is genuinely unfinished work,
and it gets its own sentence because it is the one omission that reveals rather
than loses. No wire version moves and no record is added.

Date: 2026-09-13. Issues: libviprs/libviprs-dep#85, #88, #89, #90, #91, #92.

## Why this is a record rather than eight open issues

Before this, every kind this build does not flatten left the same warning: code
100, `X is not a primitive this version flattens`. That sentence is true of two
completely different situations, and `docs/WIRE.md` forbids a consumer parsing
the message, so 100 was the only thing either of them carried.

"Nobody has got to MLINE yet" and "no decoder built on this reader will ever
render a 3DSOLID" are different facts. A consumer deciding whether to show a
placeholder, warn somebody, or go and find another tool wants the second one,
and could not get it. That is the gap this ADR and code 109 close.

Adding a warning code costs almost nothing here, which is the other half of the
argument. `WIRE.md` says a consumer skips a code it does not know and does not
refuse the stream, so a new code is not a wire version bump, and
`test_warning_codes.py` already proves the C# enumeration and the specification
table name the same set with no .NET anywhere. One constant, one `Name` case,
one documentation row.

## The table

`native/Adapter/RefusedKinds.cs` is this table in code, and
`tests/test_refusal_decisions.py` holds the two plus `WIRE.md`'s warning-code
table to the same kinds and the same codes.

| Kind | Disposition | Code | Why | What would reopen it |
| --- | --- | --- | --- | --- |
| `3DSOLID` | refused | `ENTITY_REFUSED_BY_DESIGN` | The geometry is an embedded ACIS stream, which is a boundary representation rather than a tessellation. ACadSharp surfaces the bytes and does not evaluate them, and neither does acadrust, the independent Rust reader #92 compared against. | A B-rep evaluator gets written or vendored, which is categorically larger than anything else on this list and is a project rather than an issue. |
| `REGION` | refused | `ENTITY_REFUSED_BY_DESIGN` | The same embedded ACIS stream as 3DSOLID, in two dimensions. | The same evaluator. If one ever lands, these two rows come back together. |
| `SHAPE` | refused | `ENTITY_REFUSED_BY_DESIGN` | The outline lives in an external SHX file. The entity carries a placement, a size, a rotation and a name, and this decoder opens no path a drawing names. | Either a placement record lands on a later wire (see below), or the whole external-resource policy changes, which is a security boundary this repository does not have anywhere yet. |
| `IMAGE` | refused | `ENTITY_REFUSED_BY_DESIGN` | What it displays is an external raster resolved through a definition object holding a file path. Same rule: no path out of a drawing is ever opened. | A placement record on a later wire, carrying the frame and the referenced name as an opaque string, with no resolution. |
| `PDFUNDERLAY` | refused | `ENTITY_REFUSED_BY_DESIGN` | The same shape as IMAGE with an external PDF behind it. | The same placement record as IMAGE, and the two land together or not at all. |
| `RAY` | refused | `ENTITY_REFUSED_BY_DESIGN` | A base point and a direction, running forever. Every record wire 2 defines is bounded, and clipping to the drawing extents to get a Line is the trap #85 exists to name: it is the easiest thing to implement and the hardest to notice being wrong, and `g13_bad_extents.dwg` is in the corpus precisely because extents cannot be trusted. | An unbounded record on a later wire, carrying base point and direction and leaving the clip to whoever knows the viewport. |
| `XLINE` | refused | `ENTITY_REFUSED_BY_DESIGN` | The same as RAY, running both ways. One record covers both kinds with a flag or two entries. | The same unbounded record. RAY and XLINE are one piece of work and always were. |
| `WIPEOUT` | deferred | `UNSUPPORTED_ENTITY` | Not a decision at all. Its geometry is entirely in the file, it needs no external resource, and it lowers to the `Polygon` record that already exists plus a way to say it masks. It stays on 100 because 100 is honest about it. | Nothing but somebody doing it, and the panel moved it up the queue: dropping a wipeout shows what the drawing meant to hide, so the failure direction is a plausible wrong drawing rather than an obviously incomplete one, and the blast radius is the area it covers rather than the one entity it is. |

The refusals are not symmetric with each other, and the codes say so. A consumer
that wants to draw a placeholder box can do it for IMAGE, PDFUNDERLAY and SHAPE
from the handle and its own copy of the drawing; there is nothing it can do for
3DSOLID; and for RAY and XLINE it is waiting on us rather than on physics.

## What is not decided

This is the part the next campaign inherits, and it is deliberately not
implemented here. Three reviewers out of four refused a wire 3 in this round,
because `acadsharp-rs` ships a wire-2 decoder that refuses any other version
and the ABI fingerprint is a hash over the header's bytes, so moving
`WIRE_VERSION` fails every consumer at `Decoder::new()` before a batch is
fetched. A bump is a two-repo campaign with a release in the middle. The design
survives here so the next one starts from it rather than from scratch.

A wire 3 worth cutting carries three records at once:

* **Point.** The biggest single gap on a real drawing and the cheapest: 40 of
  the 88 refusals in #92's census are POINT.
* **Unbounded.** Base point and direction, covering RAY and XLINE, with the
  clip left to the consumer that knows its viewport.
* **Placement.** An insertion point, two axis vectors, an optional clip
  boundary and a referenced name as an opaque string, covering IMAGE,
  PDFUNDERLAY and SHAPE without resolving anything. A renderer draws a frame
  and a label; a consumer with a policy of its own goes and fetches the file.

### The trap that comes with any of them

`WireFormat.IsGeometry` is the contiguous range **3 to 10**, spelled as
`FirstGeometryType`/`LastGeometryType` rather than as a list, with a comment
saying the numbering is frozen so the range is contiguous by construction. The
thirteen record types run 1 to 13 and 11, 12 and 13 are `Warning`, `ViewEnd`
and `DocumentEnd`, so a new geometry record gets 14 or higher.

That number is outside the range. `Flattener.Finite()` returns early for
anything `IsGeometry` says no to, so a `NaN` coordinate in a record numbered 14
crosses the wire untouched, and `WIRE.md`'s promise that no record of type 3 to
10 carries a non-finite value quietly stops covering the new record while
remaining literally true. Nothing fails to compile and no test of either half
notices.

Whoever adds a geometry record past 13 changes `IsGeometry` from a range to a
set, changes `WIRE.md`'s finiteness section from "3 to 10" to whatever the set
is, and adds a fixture carrying a non-finite value in the new record. This
paragraph is the single most useful thing in this document.

## The version floor, and what I actually tried (#91)

`AbiConstants.DwgVersionMin` is `1014u`, and the comment beside it said ADR 0001
"took these from upstream's own reader table for the pinned version". ADR 0001
contains no reader table, no `1014` and no `1012`. The citation pointed at
nothing, which is worse than no comment at all because it stops the next reader
looking. It now cites this document.

What I measured, in the pinned SDK container on arm64, writing a one-line
document at three versions with ACadSharp 3.7.1's own `DwgWriter` and reading
each one back:

```
WRITE AC1012: ACadSharp.Exceptions.CadNotSupportedException: File version not supported: AC1012
WRITE AC1014: OK, 7360 bytes
READ  AC1014: OK, version=AC1014, entities=1
WRITE AC1015: OK, 7582 bytes
READ  AC1015: OK, version=AC1015, entities=1
```

So the two halves of ACadSharp disagree about R13, and only one of them is the
one the fixture generator needs:

* **The reader takes it.** `DwgFileHeader.CreateFileHeader` sends `AC1012` to
  `DwgFileHeaderAC15`, and `DwgStreamReaderBase.GetStreamHandler` sends it to
  `DwgStreamReaderAC12`. Both refuse `AC1009` and everything below it
  explicitly, which is the floor `g13_ac1009.dwg` exists to prove.
* **The writer refuses it.** `DwgWriter.getFileHeaderWriter` lists `AC1012`
  in the same arm as `AC1009` and throws. It refuses `AC1021` from the same
  switch, which is why `tests/ac21_forge.py` exists at all: that file builds an
  AC1021 drawing byte by byte because the generator cannot write one.

**So the floor does not move in this round**, and the reason is not that R13
fails. It is that #91's cheap experiment does not exist: there is no way to
write the fixture that would prove R13 decodes, short of forging an R13 file by
hand the way `ac21_forge.py` forges AC1021, and R13's object stream differs from
R14's by considerably more than AC1021's page metadata differs from AC1018's.
Lowering the floor to `1012` without that fixture would be advertising a range
on the strength of two switch statements.

**AC1013 is not a thing to decide about.** #91's table has a row for it and
ACadSharp's `ACadVersion` enum has no such member: it goes `AC1009`, then
`AC1012 = 19`, then `AC1014 = 21`. The gaps in the numbering are unnamed. So
the row invents a version, and any floor discussion has exactly two candidates
below 1014, which are 1012 and nothing.

**The asymmetry is the reason it is safe to leave this alone.** Lowering the
floor is additive: a consumer that could not open an R13 drawing yesterday can
today, nothing it already did changes, and no version has to move for it.
Raising the floor is breaking, it cannot be expressed on this wire at all, and a
consumer finds out one drawing at a time. So the cost of waiting for real
evidence is nearly zero and the cost of guessing low and reversing is paid by
whoever is holding the drawing.

## The census (#92)

The differential comparison against acadrust 0.5.5 is the measurement this whole
lane is built on, and it lives in libviprs/libviprs-dep#92 with the harness in
spdrman/acadrust#2. Its headline numbers, so this document does not depend on
an issue staying open:

* 42 of 43 fixtures open in both readers. The one refusal, `g13_ac1009.dwg`, is
  refused by both.
* Of the 17 fixtures whose geometry can be compared without reimplementing the
  flattener, 15 match to 1e-6, including every OCS fixture, which is what says
  the arbitrary-axis lift is right.
* On `real_AC1032.dwg` this adapter emits 222 geometry records and refuses 88
  entities. acadrust reads all 88 with matching counts, so none of these gaps
  are things ACadSharp cannot expose. They are flattener and wire-format work.
* Where the two disagree on quality rather than coverage, this adapter is the
  correct one twice: acadrust returns un-normalized extrusion normals, and it
  passes NaN and Infinity straight through where `WIRE.md`'s finiteness
  guarantee makes this one refuse them.
* 16 of the 43 could not be compared at all, because they need block expansion,
  hatch lowering or dimension expansion. Those are the parts a renderer leans on
  hardest, so "15 of 17 agree" is not "the adapter is 88% right", and a second
  oracle taken **after** flattening is the natural next piece of work.

## What this ADR does not cover

`MESH` is in #88 and is not refused. Its faces are an explicit index list in the
file, it needs no evaluator, and it lowers to one `Polygon` per face with the
subdivision level ignored and said so. It landed in its own change in the same
campaign and deliberately has no row above, because a row here would say the
opposite of what is true about it.

`POLYFACE_MESH` and `POLYGON_MESH` are not here either, and for a sharper
reason: they were never refused. Both inherit `IPolyline`, so they matched that
arm and crossed as a `Polyline` threaded through their vertices with no warning
at all, which is #82 and the one real defect the comparison found.
