# ADR 0003: wire 3, and the three records that earn it

**PROPOSED, not decided.** Nothing below is implemented, no constant has moved
and no fixture has changed. This is the byte layout a wire 3 would carry, written
down so the campaign that cuts it starts from bytes rather than from prose. The
decision to cut it, and when, is the epic this document was filed with.

Date: 2026-09-13. Issues: libviprs/libviprs-dep#83, #85, #89, #103.

## Why this exists when ADR 0002 already said all this

ADR 0002's "What is not decided" section is the decision and it stands: three
reviewers out of four refused a wire 3 in that round, because `acadsharp-rs`
ships a wire-2 decoder that refuses any other version and the ABI fingerprint is
a hash over the header's bytes, so moving `VIPRS_ACAD_WIRE_VERSION` fails every
compiled consumer at `Decoder::new()` before a single batch is fetched. That is
still true today and it is still the right answer for a two-hour window.

What that section does not have is a layout. It names Point, Unbounded and
Placement and describes each of them in one sentence, which is enough to agree
that they are the right three and nowhere near enough to write a decoder
against. The next campaign spans two repositories with a release in the middle,
so the two halves get written weeks apart by people reading the same paragraph,
and a paragraph is exactly where two implementations disagree about whether a
payload has seven doubles in it or eight.

I am leaving ADR 0002's section where it is rather than moving it here.
`tests/test_refusal_decisions.py` and `tests/test_acadsharp_adr.py` both parse
that document in place, and a decision record that gets edited every time a
later one elaborates on it stops being a record of what was decided.

## Why the three go together

Each version bump costs both repositories a release and refuses every consumer
in between, which is the whole reason one was refused in the first place. Paying
that three times for three records that are all "the file holds this and wire 2
has nowhere to put it" would be three times the disruption for one piece of
work, so the three land in one version or they wait.

They are also the three that clear the largest part of the refusal census
between them. POINT alone is 40 of the 73 entity refusals on `real_AC1032.dwg`.
RAY and XLINE are the two kinds ADR 0002 files under "waiting on us rather than
on physics". IMAGE and PDFUNDERLAY are the two whose refusal is about a policy
(this decoder opens no path a drawing names) rather than about the geometry,
and a placement record is how a consumer gets the frame without anybody opening
anything.

## The layouts

Offsets are from the start of the payload, which is the record's ninth byte,
the same as every layout in `docs/WIRE.md`. A size given for a record is its
`length`, so the eight-byte record header is inside it. `f64` is IEEE-754
binary64. No field is guaranteed to be naturally aligned and every scalar is
read with an unaligned load or a byte copy, which is the rule that already
applies to every record on this wire and does not change.

All three are geometry records, so all three open with the same sixteen-byte
prologue wire 2 already defines:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 8 | `item_handle`, the backing file's handle for what produced this record, or 0 |
| 8 | 4 | `flags`, bit 0 set when the record came from expanding a nested insertion |
| 12 | 4 | `reserved0` |

Wire 3 defines one more bit and nothing else: **bit 1 of `flags` means this
record masks**, that is, it hides whatever the stream drew before it inside its
own boundary rather than adding to it. Wire 2 says this with warning code 112
beside the record, which is the only channel wire 2 has, and the two agree on
purpose: a wire-3 producer sets the bit and keeps emitting 112, and a consumer
reads whichever it understands. `reserved0` stays reserved and is written zero.
I am reserving the bit here rather than leaving it to the lane that needs it,
because a semantic prologue bit that nobody wrote a rule for is the change that
only fails on the platform nobody tested: at least one consumer in this tree
already asserts `flags == 0` on a probe stream, having read the prologue's
silence about the other bits as a promise.

**14 `Point`**: prologue, then `f64 x, y, z`, `f64 nx, ny, nz`, `f64 rotation`.
72 bytes of payload, so `length` is `80`.

`x, y, z` is the marker's position in world coordinates, lifted out of the
entity's own plane by the arbitrary-axis algorithm the way every other planar
entity's coordinates already are, so a consumer never applies an extrusion.
`nx, ny, nz` is the entity's normal, normalised, and it is in the record because
the marker is a glyph drawn in that plane and a consumer that draws anything
other than a dot needs to know which plane. `rotation` is the angle of the
marker's x-axis in that plane, in radians, which is DXF group 50 on a POINT.

PDMODE and PDSIZE are not here and will not be. They are header variables that
say which glyph AutoCAD draws and how big, which is a rendering decision made by
whoever is drawing, and this boundary's job is to say where the point is. A
consumer that wants AutoCAD's glyph reads the header itself; a consumer that
wants a dot draws one.

**15 `Unbounded`**: prologue, then `f64 bx, by, bz`, `f64 dx, dy, dz`,
`uint32 kind`, `uint32 reserved1`. 72 bytes of payload, so `length` is `80`.

`bx, by, bz` is the base point and `dx, dy, dz` is the direction, normalised and
never the zero vector. `kind` is 0 for a line that runs one way from the base
point, which is a RAY, and 1 for one that runs both ways, which is an XLINE.
`reserved1` is zero. One record covers both kinds because they are one piece of
work and always were, and because a consumer's arm for them is the same arm with
one branch in it.

The clip is the consumer's, deliberately. Clipping to the drawing's extents here
and emitting a `Line` is the trap #85 exists to name: it is the easiest thing to
implement and the hardest to notice being wrong, and `g13_bad_extents.dwg` is in
the corpus precisely because a drawing's stated extents cannot be trusted. The
consumer knows its viewport and we do not.

**16 `Placement`**: prologue, then `f64 ix, iy, iz`, `f64 ux, uy, uz`,
`f64 vx, vy, vz`, `f64 size_u, size_v`, `uint32 kind`, `uint32 clip_count`,
`uint32 name_len`, `uint32 reserved1`, then `clip_count` pairs of `f64 x, y`,
then `name_len` bytes of UTF-8 padded with zeroes to a multiple of four. So
`length` is `128 + 16·clip_count + 4·ceil(name_len / 4)`.

`ix, iy, iz` is the insertion point in world coordinates. `ux, uy, uz` and
`vx, vy, vz` are the two axis vectors, which carry the frame's size and its
rotation and its skew together, so a consumer draws the frame as the
parallelogram from the insertion point along `u` and `v` and never has to
reconstruct an angle. `size_u` and `size_v` are the referenced resource's own
size in its own units, which is pixels for a raster and points for a PDF page,
and they are there so a consumer that does go and fetch the file can tell
whether it got the one the drawing meant. `kind` is 0 for IMAGE and 1 for
PDFUNDERLAY. `reserved1` is zero.

The `clip_count` pairs are the clip boundary, in the frame's own coordinates:
`x` runs along `u` and `y` along `v`, both as fractions, so the world point is
`insert + u·x + v·y` and that is the whole of what a consumer does with them.
The producer has already mapped them out of the image's pixel space, which for a
raster means `x = (px + 0.5) / size_u` and `y = (size_v − py − 0.5) / size_v`,
because pixel space puts its origin half a pixel outside the first pixel and
runs its rows the other way from `v`. Nobody downstream should have to know
that. The boundary closes implicitly and the first vertex is not repeated, the
same convention record 9 already uses, and `clip_count` is 0 when the frame is
not clipped.

Two coordinates rather than three is a decision and I want it read as one. A
clip boundary that did not lie in the frame's own plane would not be a clip of
anything, so three world coordinates per vertex would be a record that can
express a state the entity cannot be in, and a consumer would have to decide
what to do when it saw one. The cost is that a consumer has to do two multiplies
and two adds per vertex. If Phase 1 finds a real file whose clip is not in the
frame's plane, this is the field to revisit and the length formula moves with it.

`name_len` bytes of UTF-8 are the referenced name exactly as the drawing spells
it, and the rule that comes with it is the whole reason this record can exist:
**the name is carried as an opaque string and nothing on this boundary opens
it**. Not to stat it, not to read a header out of it, not to work out whether it
is there. A consumer with a policy of its own goes and fetches the file; a
renderer draws the frame and the label. That is the same boundary ADR 0002 drew
when it refused IMAGE and PDFUNDERLAY, and the record does not move it, it just
stops the drawing's own geometry being lost along with the resource.

SHAPE is not in this round even though ADR 0002 names it as a placement
candidate. Its outline is a glyph in an external SHX file, so its frame is the
extent of a glyph nobody has measured rather than a rectangle the file states,
and `size_u`/`size_v` would be a number this side invented. `kind` is a `uint32`
with two values used, so a later round adds one without moving anything.

## The trap that comes with any of them

This is the paragraph ADR 0002 called the single most useful thing in that
document, and the reason it is repeated here rather than cross-referenced is
that the person who ships the hole is the person who read this document and not
that one.

`WireFormat.IsGeometry` is the contiguous range **3 to 10**, spelled as
`FirstGeometryType`/`LastGeometryType` rather than as a list, with a comment
saying the numbering is frozen so the range is contiguous by construction. Wire
2's thirteen record types run 1 to 13, and 11, 12 and 13 are `Warning`,
`ViewEnd` and `DocumentEnd`, so every new geometry record gets 14 or higher and
every one of them is outside that range.

`Flattener.Finite()` returns early for anything `IsGeometry` says no to. So a
`NaN` or an infinite coordinate in a record numbered 14 crosses the wire
untouched, `docs/WIRE.md`'s promise that no record of type 3 to 10 carries a
non-finite value quietly stops covering the new records while staying literally
true, nothing fails to compile, and no test of either half notices.

So, in the imperative, as the first item of Phase 1 and before any of the three
records exists:

1. Change `IsGeometry` from a range to an explicit set, and delete
   `FirstGeometryType` and `LastGeometryType` so there is no second spelling of
   the answer left for somebody to extend instead.
2. Change `docs/WIRE.md`'s finiteness section and its error table from "3 to 10"
   to whatever the set is, in both places.
3. Add a fixture carrying a non-finite value in a record numbered past 13 and
   assert it produces warning 107, `NON_FINITE_GEOMETRY`, and no record. Without
   that fixture the first two are a refactor nobody watched work.

Do that first, with its own commit, so the three records land on a guard that
already covers them.

## What a wire 3 touches in this repository

In dependency order, because most of these will not compile until the one above
them has moved.

* `include/viprs_acadsharp.h`: `VIPRS_ACAD_WIRE_VERSION` goes to `3u`, and the
  header's record-type list gains 14, 15 and 16. The ABI fingerprint is a hash
  over this file's bytes, so this is the line that refuses every consumer.
* `native/Wire/WireFormat.cs`: the three type constants, and `IsGeometry` as a
  set per the section above.
* `native/Adapter/RecordName.cs`: three names, because the dump spells records by
  name and an unnamed type is an unreadable expectation.
* `native/Adapter/RefusedKinds.cs`: POINT was never in the table, and RAY, XLINE,
  IMAGE and PDFUNDERLAY leave it. `docs/adr/0002-what-this-decoder-refuses.md`
  loses the same four rows and its opening count moves with them.
* `native/Adapter/`: the arms, one new partial-class file each, and the entity
  switch in `Flattener.cs`.
* `tests/fixtures/gen/CanonicalDump.cs`: three new record kinds in the dump, and
  the dump is the expectation format, so this is what makes the fixtures
  readable.
* `tests/g13_support.py`: `RECORD_KINDS`.
* `tests/conformance/c/vacb.h` and `vacb.c`, and
  `tests/conformance/rust/src/wire.rs` and `payload.rs`: the two independent
  consumers, which are the only readers in this repository that nobody wrote the
  producer for.
* `tests/test_conformance_consumers.py`: it counts the names, and the count is
  currently spelled "thirteen" in prose.
* `tests/test_wire_protocol.py`, `tests/test_wire_conventions.py` and every
  table test that enumerates the record types.
* `docs/WIRE.md`: the record table, the three layouts, the finiteness section,
  the prologue's bit 1, and the error table.

Then the release: `VERSION` and the `SHIM_DIGESTS` row in `build_acadsharp.py`,
and a tag, because the header a consumer vendors comes out of the published
archive and not out of this tree.

## What it touches in acadsharp-rs

* `COMPAT.toml`: `wire_version = 3`, and the vendored copy of the header under
  `native/` refreshed from the published archive.
* `build.rs`: it derives `EXPECTED_WIRE_VERSION` from the vendored header, so
  this one follows on its own once the header lands.
* `src/batch.rs`: three `Record` variants and their readers, each by length so an
  unknown type is still skipped the way it is today.
* `src/item.rs`: the `Item` variants a consumer actually sees, and `Origin`,
  which today drops every prologue bit above bit 0. Bit 1 lands here as
  `Origin::masks`, and `Origin` is `#[non_exhaustive]` so adding it is not a
  breaking change on that side.
* Its own release, then the pin bump in libviprs.

## The order, and why the middle of it looks broken

1. `IsGeometry` and its fixture, in this repository, alone.
2. The three records, the arms, the fixtures and both conformance consumers,
   with `VIPRS_ACAD_WIRE_VERSION` moving in the same change as the last of them.
3. Release the archive.
4. Vendor the header into `acadsharp-rs`, add the variants, release that.
5. Bump the libviprs pin.

Between 3 and 4 every compiled consumer of this library refuses to start, at
`Decoder::new()`, with a fingerprint mismatch. That is not a regression to be
minimised, it is the feature the fingerprint exists to provide: the alternative
is a consumer that starts, reads a batch whose records it half understands, and
renders a drawing that is wrong in a way nobody can see. A loud refusal on a
version boundary is the failure direction this whole design picked.

So the sequencing rule is that nothing between 2 and 5 is partially merged. A
half-landed wire 3 is worse than no wire 3, and the epic's phases are drawn
where they are for that reason.

## What this does not decide

MULTILEADER, MLINE, TOLERANCE, 3DSOLID and REGION are not here and none of them
is a wire question. The first three lower onto records wire 2 already has and
are flattener work; the last two need a B-rep evaluator, which is a project.

`reserved0` in the prologue stays reserved, and I am not defining it. The
consumer this repository ships never reads it, and a field that has never been
read is a field nobody knows the value of in the wild.

Nothing here is a promise about wire 4. The record numbering stays frozen, so 17
is the next one and 14, 15 and 16 mean what this document says they mean for as
long as the format exists.
