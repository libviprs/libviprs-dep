# The VACB batch protocol, wire version 2

`viprs_acad_decode_next_batch` writes one batch per call into a buffer the
caller owns. This document is the complete definition of what is in that
buffer. A consumer written from this file alone, with no access to the
library's source, must be able to read every stream the library produces and
must be able to refuse every stream it should not read.

Everything is little-endian. Nothing is a text format, nothing is
self-describing beyond what is written here, and nothing is compressed.

## Record types, frozen

The numbering is part of the contract. A consumer switches on the number,
never on a name, and these numbers have not moved since wire version 1.

| Number | Name | Carries |
| --- | --- | --- |
| 1 | `DocumentBegin` | How many views the document has, and the numeric AC10xx code it was read from. |
| 2 | `ViewBegin` | One view's index, kind, extents, an approximate item count, and its name as UTF-8. |
| 3 | `Line` | Two endpoints. |
| 4 | `Polyline` | A vertex run, open or closed, a normal, and a bulge per vertex. |
| 5 | `Arc` | Centre, radius, start and end angle, and a normal. |
| 6 | `Circle` | Centre, radius, and a normal. |
| 7 | `Ellipse` | Centre, major axis vector, minor-to-major ratio, parameter range, and a normal. |
| 8 | `Spline` | Degree, knots, control points and weights. |
| 9 | `Polygon` | A closed boundary, with record 4's payload and `closed` always 1. |
| 10 | `Text` | Position, height, rotation, and UTF-8 bytes with a length. |
| 11 | `Warning` | A numeric code, a UTF-8 message with a length, and an optional item handle. |
| 12 | `ViewEnd` | The view index it closes, and how many records it contained. |
| 13 | `DocumentEnd` | Totals for the whole stream. |

Numbers from 32512 (`0x7F00`) upward are the forward-probe range. They carry
no meaning in wire version 2 and a consumer must skip them by their length.
The library emits one on purpose, so that the skip path is exercised by a
real stream rather than only by a buffer a test assembled by hand.

## Batch framing

Twelve bytes, then the records.

| Offset | Size | Field | Value |
| --- | --- | --- | --- |
| 0 | 4 | magic | The ASCII bytes `VACB`, `0x56 0x41 0x43 0x42` |
| 4 | 2 | `wire_version` | 2 |
| 6 | 2 | `flags` | Bit 0 set on the last batch of a stream, every other bit zero |
| 8 | 4 | `payload_length` | Bytes of records that follow, not counting these twelve |

A batch is therefore `12 + payload_length` bytes, and that is what
`viprs_acad_decode_next_batch` reports through `written`.

Batches target 64 KiB and never exceed 1 MiB, with one exception: a single
record larger than 1 MiB is sent as a batch of its own, because a batch never
splits a record and the limits allow records that large. A `Polyline` at the
default `max_polyline_points`, carrying a bulge per vertex, is 32 MB on its
own, so this is not a corner nobody reaches.

A batch never spans two calls. A caller that hands over a buffer too small for
the next batch gets `VIPRS_ACAD_LIMIT_EXCEEDED` with the size it needs, which
is the only thing a caller has to handle to be correct at any buffer size.

An empty batch, `payload_length` zero, is legal. It is what a decode with
nothing left to say looks like when it still has to tell the caller the
stream ended.

## Record framing

Eight bytes, then the payload.

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 2 | `type` |
| 2 | 2 | `reserved`, zero in wire version 1 |
| 4 | 4 | `length`, the whole record including these eight bytes |

`length` includes the header. That is the one decision a parser has to get
right, and putting the header inside the number means a parser advances by
exactly `length` and never has to remember to add anything.

Four rules a parser enforces on every record, in this order:

1. At least eight bytes remain in the payload, or the batch is
   `VIPRS_ACAD_CORRUPT_INPUT`.
2. `length` is at least 8. A smaller value cannot advance the cursor past
   its own header, so accepting one turns a malformed batch into an infinite
   loop. Refuse it.
3. `length` is a multiple of 4. Producers pad payloads to keep the next
   record at a predictable offset.
4. `length` does not run past `payload_length`. A record that claims more
   than the batch holds is corrupt, whatever its type.

Anything that fails is `VIPRS_ACAD_CORRUPT_INPUT` for the whole batch. A
parser does not try to resynchronise; there is nothing to resynchronise on.

A parser should also carry a hard iteration bound, one per smallest possible
record plus one. Rule 2 is what keeps the cursor moving, and a bound is how
a mistake in rule 2 becomes a failed parse instead of a job somebody has to
kill.

### Unknown types are skipped

A `type` a consumer does not recognise is skipped by `length`, and parsing
continues with the record after it. This is the entire reason the length is
in the record header, and it is what lets a later wire version add a record
type without breaking a consumer compiled against this one.

Skipping an unknown type is not forward compatibility with an unknown
`wire_version`. A version this consumer does not parse is refused, because a
record type it does know may have changed shape underneath it: version 2's
`Polyline` is 32 bytes longer than version 1's and carries two fields version
1 never had, and a version 1 consumer reading one finds a plausible vertex
count and then walks off the end of the payload.

A consumer must not skip by a table of known sizes. The forward-probe record
the library emits has a payload length that matches no other record on
purpose, so a consumer that skips by size lands in the middle of the next
one and fails visibly.

### Alignment

No field inside a record is guaranteed to be naturally aligned. The batch
header is twelve bytes, so the first record starts at offset 12 and every
64-bit field in it sits at an offset that is a multiple of four and not of
eight.

That is deliberate. Padding the batch header to sixteen bytes would buy
aligned access on today's layout and lose it again the first time a record
payload has an odd number of 32-bit fields in front of a `double`. So the
rule is uniform and stated once: read every scalar with an unaligned load or
a byte copy. On the architectures this ships to that costs nothing
measurable, and it removes a class of fault that only appears on the one
platform nobody tested.

## Payload layouts

Offsets below are from the start of the payload, which is the record's ninth
byte. A size given for a record is its `length`, so the eight-byte record
header is included in it. `f64` is IEEE-754 binary64. Every geometry record
opens with the same sixteen-byte prologue:

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 8 | `item_handle`, the backing file's handle for what produced this record, or 0 |
| 8 | 4 | `flags`, bit 0 set when the record came from expanding a nested insertion |
| 12 | 4 | `reserved0` |

**1 `DocumentBegin`**: `uint32 view_count`, `uint32 drawing_version` (the
numeric AC10xx code, 0 when it is not known), `uint64 reserved0`. 24 bytes.

**2 `ViewBegin`**: `uint32 view_index`, `uint32 kind` (0 model, 1 layout, 2
unknown), `f64 min_x`, `f64 min_y`, `f64 max_x`, `f64 max_y`, `uint64
item_count`, `uint32 name_len`, `uint32 reserved0`, then `name_len` bytes of
UTF-8, padded with zeroes to a multiple of four. The name is not terminated.

**3 `Line`**: prologue, then `f64 x0, y0, z0, x1, y1, z1`. 72 bytes.

**4 `Polyline`**: prologue, then

| Offset | Size | Field | Rule |
| --- | --- | --- | --- |
| 16 | 4 | `point_count` | `n`, at most `max_polyline_points` |
| 20 | 4 | `closed` | 0 or 1 |
| 24 | 4 | `bulge_count` | `bc`, either 0 or equal to `point_count` |
| 28 | 4 | `reserved1` | 0 |
| 32 | 24 | `f64 nx, ny, nz` | the entity's normal |
| 56 | 24·n | `f64 x, y, z` × n | the vertices |
| 56 + 24·n | 8·bc | `f64 bulge` × bc | one per vertex, absent when `bc` is 0 |

`length` is `64 + 24n + 8bc`.

`bulge[i]` is the bulge of the span from vertex `i` to vertex `i + 1`. For a
closed polyline `bulge[n - 1]` is the closing span's, from the last vertex
back to the first; for an open one it has no span and a consumer ignores it.
`bulge_count` of 0 means every span is straight, which is the only other value
the field takes: a consumer never has to work out which spans a shorter array
covers. Record 8's `weight_count` is the same rule.

A bulge is `tan(θ / 4)` for the arc's included angle `θ`, which is what DWG
stores, and it is **positive when the arc runs counter-clockwise about the
normal**, matching the `Arc` record's angles. So a bulge of 1 is a half turn
counter-clockwise, `-1` is a half turn clockwise, and 0 is a straight span.

The arc a bulge names, without ever computing a centre: with `c` the chord
length, `d̂` the unit vector from the first vertex to the second, `N̂` the unit
normal and `mid` the chord's midpoint, the arc's midpoint is

    mid + (b · c / 2) · (d̂ × N̂)

That is the well-conditioned direction, and it is exact at every magnitude of
`b` a double can hold. Going the other way, to a centre and two angles, loses
accuracy as `b` gets small: at `b = 1e-12` a reconstructed endpoint is already
`2.6e-5` chord lengths from the vertex the record carries, and at `1e-17` the
two angles come out exactly equal. Every arc-to-polyline conversion in every
CAD tool leaves bulges of about `1e-15` on vertices it considers straight, so
this is not a corner case.

A consumer that wants segments should subdivide rather than solve. If the
sagitta `(c / 2) · |b|` is inside its tolerance the span is the chord;
otherwise the midpoint above splits it into two spans of bulge
`b' = b / (1 + sqrt(1 + b²))`, and for `|b| > 1` the same value is
`sign(b) / (r + sqrt(r² + 1))` with `r = 1 / |b|`, which is the form that does
not overflow. Both were checked against `tan(atan(b) / 2)` from `1e-300` to
`1e308`: the first is exact below 1 and `NaN` above `1e154`, the second is
exact above 1 and 0 below `1e-300`, so the split at `|b| = 1` is load-bearing.
Endpoints are never recomputed, so the pieces meet exactly at every depth.
Thirty-two levels is a sensible cap. None of this paragraph is contractual;
the record is.

**5 `Arc`**: prologue, then `f64 cx, cy, cz`, `f64 radius`, `f64
start_angle`, `f64 end_angle`, `f64 nx, ny, nz`. Angles are radians,
counter-clockwise, measured in the plane the normal defines. 96 bytes.

**6 `Circle`**: prologue, then `f64 cx, cy, cz`, `f64 radius`, `f64 nx, ny,
nz`. 80 bytes.

**7 `Ellipse`**: prologue, then `f64 cx, cy, cz`, `f64 major_x, major_y,
major_z` (the vector from the centre to the end of the major axis), `f64
ratio` (minor over major), `f64 start_param`, `f64 end_param`, `f64 nx, ny,
nz`. 120 bytes.

**8 `Spline`**: prologue, `uint32 degree`, `uint32 flags` (bit 0 closed, bit
1 rational, bit 2 periodic), `uint32 knot_count`, `uint32 control_count`,
`uint32 weight_count`, `uint32 reserved1`, then `knot_count` values of `f64`,
then `control_count` triples of `f64 x, y, z`, then `weight_count` values of
`f64`. `weight_count` is either 0 or equal to `control_count`.

**9 `Polygon`**: record 4's payload exactly, with `closed` always 1. The
boundary is closed implicitly and the first vertex is not repeated, so the
closing span is the one `bulge[n - 1]` describes. One layout means one reader
serves both, and a hatch loop that turns out to carry a curved edge needs no
second shape.

**10 `Text`**: prologue, `f64 x, y, z`, `f64 height`, `f64 rotation`
(radians), `uint32 byte_len`, `uint32 reserved1`, then `byte_len` bytes of
UTF-8, padded with zeroes to a multiple of four. Not terminated.

**11 `Warning`**: `uint32 code`, `uint32 reserved0`, `uint64 item_handle`
(0 when the warning is about the document rather than one item), `uint32
message_len`, `uint32 reserved1`, then `message_len` bytes of UTF-8, padded
with zeroes to a multiple of four. This record does not carry the geometry
prologue, because it is not geometry.

**12 `ViewEnd`**: `uint32 view_index`, `uint32 reserved0`, `uint64
record_count`, the number of records emitted for the view, its own
`ViewBegin` and `ViewEnd` included. 24 bytes.

**13 `DocumentEnd`**: `uint64 total_records`, `uint64 warning_count`. 24
bytes.

## Warning codes

A `Warning` record's `code` is what a consumer branches on. The message
beside it is for a person reading a log, and nothing on this boundary parses
it for control flow, so the codes have to be written down here or a warning
is a thing a consumer can only count.

They are VIPRS-owned. The list below is the whole VIPRS-defined set, and it
does not depend on which backend read the drawing: a second implementation
that can tell an unsupported entity from an unresolved insertion emits 100
and 105 with these meanings, whatever it is built on.

| Code | Name | What it says |
| --- | --- | --- |
| 100 | `UNSUPPORTED_ENTITY` | An entity kind this build does not flatten. The message names the source format's type and `item_handle` is the entity's, which together are enough to find it in the drawing. |
| 101 | `READER_NOTIFICATION` | Something the backing reader had to say about the file, passed through. `item_handle` is 0: it is about the document. |
| 102 | `DIMENSION_WITHOUT_BLOCK` | A dimension with no geometry block to take its lines and text from, so nothing was emitted for it. |
| 103 | `HATCH_PATTERN_ONLY` | A hatch with no boundary loop that could become a `Polygon`. |
| 104 | `HATCH_LOOP_NOT_POLYGON` | A boundary loop carrying an elliptical or spline edge, which a closed polygon cannot express. The edges follow as their own records, so nothing is lost and nothing is approximated. |
| 105 | `UNRESOLVED_BLOCK` | An insertion whose block could not be resolved, which is what an unresolved external reference looks like from inside. Never a fetch, and never a read of anything outside the file being decoded. |
| 106 | `NON_UNIFORM_BLOCK_SCALE` | An insertion scale that is not a similarity, under which a circle is an ellipse and a bulge is an elliptical arc. The parameters still cross unchanged; this says they were measured in a frame the transform does not preserve. |

### Reserved ranges

| Range | Who allocates it |
| --- | --- |
| 1 to 999 | VIPRS. Every value in use is in the table above, and a new one is added to this document in the change that first emits it. |
| 1000 and up | The backing source. A number here means something to the implementation that produced the stream and is not part of this specification, so a consumer is entitled to know none of them. This library's own synthetic document uses 1100 for its probe warning. |

Zero is not a warning code. A `Warning` record carrying 0 is malformed.

### A code you do not know

A consumer **skips a code it does not know** and carries on: it counts the
warning, it may log the message, and it does not refuse the stream. This is
the same rule as an unknown record type and it exists for the same reason.
Adding a warning code is not a wire version bump, so a consumer that refused
on one would start failing the first time this library described something
new, on files it had read correctly the day before.

That is the opposite of the rule for `wire_version`, deliberately. An
unknown wire version means the layout is not the one this consumer parses,
and reading it produces numbers rather than an error. An unknown warning
code means a record whose layout is fully known is saying something this
consumer has no branch for, and the record after it is still exactly where
the length says it is.

## Curves keep their parameters

`Arc`, `Circle`, `Ellipse` and `Spline` carry the parameters that define
them and are never tessellated into segments by the producer. A polyline's
bulge crosses as a bulge, for the same reason: the arc it names is exact, and
turning it into a chord or into a run of segments is the tolerance decision
the producer is not in a position to make. Tessellation
is a rendering decision and it needs a tolerance, which depends on the zoom
level and the output device, neither of which the producer knows. A producer
that flattens a curve has thrown that choice away, and a consumer cannot
recover it: a polyline of a thousand points does not tell you it was a
circle.

A consumer that only draws segments should tessellate on its own side, at
its own tolerance, where it can do it again at a different one.

## Stream shape

```
DocumentBegin
  ViewBegin
    geometry, warnings, and any forward-probe records
  ViewEnd
  ... one pair per view decoded
DocumentEnd
```

`viprs_acad_decode_begin` takes a single view index, so one decode handle
produces one `ViewBegin` / `ViewEnd` pair.

Record boundaries do not line up with batch boundaries in any particular
way. A batch may hold one record or hundreds, and a consumer must parse the
concatenation of every batch's payload as one sequence rather than assume
anything begins or ends at a batch edge.

Warnings may appear anywhere between `ViewBegin` and `ViewEnd`. They are not
errors: a decode that emits a hundred of them and returns `VIPRS_ACAD_OK`
succeeded, and the warnings are what it has to say about the parts of the
drawing it could not fully represent.

## Refusing a stream

| Condition | Result |
| --- | --- |
| The first four bytes are not `VACB` | `VIPRS_ACAD_CORRUPT_INPUT` |
| `wire_version` is not one this consumer parses | `VIPRS_ACAD_ABI_MISMATCH` |
| Fewer than twelve bytes are available | `VIPRS_ACAD_CORRUPT_INPUT` |
| `12 + payload_length` exceeds the buffer | `VIPRS_ACAD_CORRUPT_INPUT` |
| Any of the four record rules above fails | `VIPRS_ACAD_CORRUPT_INPUT` |
| `reserved` in a record header is not zero | `VIPRS_ACAD_CORRUPT_INPUT` |
| A `Polyline` or `Polygon` whose `bulge_count` is neither 0 nor `point_count` | `VIPRS_ACAD_CORRUPT_INPUT` |
| A `Polyline` or `Polygon` whose `length` is not `64 + 24n + 8bc` | `VIPRS_ACAD_CORRUPT_INPUT` |
| An `f64` in a geometry record, types 3 to 10, that is `NaN` or infinite | `VIPRS_ACAD_CORRUPT_INPUT` |

Refusing on an unknown `wire_version` rather than trying is the important
one. A consumer that parses a version it does not know is reading a layout
it is only assuming, and it will produce numbers rather than an error. That
refusal is `VIPRS_ACAD_ABI_MISMATCH` and not `VIPRS_ACAD_UNSUPPORTED_FORMAT`:
the second is about the drawing, and means look at `dwg_version_min` and
`dwg_version_max` and hand the file to something else. A foreign wire version
is the two ends of this boundary disagreeing, and the remedy is to rebuild
one of them.

The last three rows are the payload rules, and they belong beside a record's
layout rather than inside the framing loop. A parser walks record headers
knowing nothing about what a payload means, which is what lets it skip an
unknown type at all; a layout rule smuggled into that loop makes the parser
wrong for every record type the day one of them changes.
