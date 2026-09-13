# The VACB batch protocol, wire version 1

`viprs_acad_decode_next_batch` writes one batch per call into a buffer the
caller owns. This document is the complete definition of what is in that
buffer. A consumer written from this file alone, with no access to the
library's source, must be able to read every stream the library produces and
must be able to refuse every stream it should not read.

Everything is little-endian. Nothing is a text format, nothing is
self-describing beyond what is written here, and nothing is compressed.

## Record types, frozen

The numbering is part of the contract. A consumer switches on the number,
never on a name, and these numbers do not move in wire version 1.

| Number | Name | Carries |
| --- | --- | --- |
| 1 | `DocumentBegin` | How many views the document has, and the numeric AC10xx code it was read from. |
| 2 | `ViewBegin` | One view's index, kind, extents, an approximate item count, and its name as UTF-8. |
| 3 | `Line` | Two endpoints. |
| 4 | `Polyline` | A vertex run, open or closed. |
| 5 | `Arc` | Centre, radius, start and end angle, and a normal. |
| 6 | `Circle` | Centre, radius, and a normal. |
| 7 | `Ellipse` | Centre, major axis vector, minor-to-major ratio, parameter range, and a normal. |
| 8 | `Spline` | Degree, knots, control points and weights. |
| 9 | `Polygon` | A closed boundary of vertices. |
| 10 | `Text` | Position, height, rotation, and UTF-8 bytes with a length. |
| 11 | `Warning` | A numeric code, a UTF-8 message with a length, and an optional item handle. |
| 12 | `ViewEnd` | The view index it closes, and how many records it contained. |
| 13 | `DocumentEnd` | Totals for the whole stream. |

Numbers from 32512 (`0x7F00`) upward are the forward-probe range. They carry
no meaning in wire version 1 and a consumer must skip them by their length.
The library emits one on purpose, so that the skip path is exercised by a
real stream rather than only by a buffer a test assembled by hand.

## Batch framing

Twelve bytes, then the records.

| Offset | Size | Field | Value |
| --- | --- | --- | --- |
| 0 | 4 | magic | The ASCII bytes `VACB`, `0x56 0x41 0x43 0x42` |
| 4 | 2 | `wire_version` | 1 |
| 6 | 2 | `flags` | Bit 0 set on the last batch of a stream, every other bit zero |
| 8 | 4 | `payload_length` | Bytes of records that follow, not counting these twelve |

A batch is therefore `12 + payload_length` bytes, and that is what
`viprs_acad_decode_next_batch` reports through `written`.

Batches target 64 KiB and never exceed 1 MiB, with one exception: a single
record larger than 1 MiB is sent as a batch of its own, because a batch never
splits a record and the limits allow records that large. A `Polyline` at the
default `max_polyline_points` is 24 MB on its own, so this is not a corner
nobody reaches.

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
in the record header, and it is what lets wire version 2 add a record type
without breaking a consumer compiled against version 1.

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

**4 `Polyline`**: prologue, `uint32 point_count`, `uint32 closed` (0 or 1),
then `point_count` triples of `f64 x, y, z`.

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

**9 `Polygon`**: prologue, `uint32 point_count`, `uint32 reserved1`, then
`point_count` triples of `f64 x, y, z`. The boundary is closed implicitly;
the first vertex is not repeated.

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

## Curves keep their parameters

`Arc`, `Circle`, `Ellipse` and `Spline` carry the parameters that define
them and are never tessellated into segments by the producer. Tessellation
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
| `wire_version` is not one this consumer parses | `VIPRS_ACAD_UNSUPPORTED_FORMAT` |
| Fewer than twelve bytes are available | `VIPRS_ACAD_CORRUPT_INPUT` |
| `12 + payload_length` exceeds the buffer | `VIPRS_ACAD_CORRUPT_INPUT` |
| Any of the four record rules above fails | `VIPRS_ACAD_CORRUPT_INPUT` |
| `reserved` in a record header is not zero | `VIPRS_ACAD_CORRUPT_INPUT` |

Refusing on an unknown `wire_version` rather than trying is the important
one. A consumer that parses a version it does not know is reading a layout
it is only assuming, and it will produce numbers rather than an error.
