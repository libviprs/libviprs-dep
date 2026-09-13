# The VIPRS CAD C ABI, version 1

This is the prose half of `include/viprs_acadsharp.h`. The header is the
contract; this document says what the contract means, in enough detail that
somebody could write a second consumer, or a second implementation of the
library behind it, from these two files and nothing else.

I wrote it that way on purpose. One consumer exists today and it is the one
I would otherwise have described, which would have turned a contract into a
description of that consumer's habits. So nothing below names a language, a
build system or a package manager, and where a choice needs justifying I
give the failure it prevents rather than the tool that would have caught it.

The batch protocol the decode calls emit lives in [WIRE.md](WIRE.md). This
file stops at the boundary.

Getting the library onto a link line in the first place is a third document,
[LINKINFO.md](LINKINFO.md), which defines every field of the archive's
`metadata/LINKINFO.json` and the linking recipe measured for it. All three ship
inside the archive, so a consumer can be built from what is published without
the producer's source.

## What this boundary is not

It carries no type from the library that happens to back it. Every value
that crosses is a fixed-width scalar, an opaque handle this library made, or
a byte buffer the caller owns. The implementation behind the symbols is
replaceable, and a consumer that can tell what it is has found a leak.

## Versions and the fingerprint handshake

Three numbers describe a build, and a consumer should read all three before
it opens anything.

`viprs_acad_abi_version()` returns `VIPRS_ACAD_ABI_VERSION`, bumped whenever
the header changes in a way a compiled consumer would notice. It is the
coarse check: a consumer built for version 1 must refuse a library that
reports 2.

`viprs_acad_abi_fingerprint()` is the fine one. It returns the first eight
bytes of the sha256 of the published header, read big-endian, so a digest
beginning `aabbccddeeff0011...` becomes `0xAABBCCDDEEFF0011`. The value is
generated from `viprs_acadsharp.h` at build time and compiled in. It is never
assigned by hand, and that is the whole point: a constant somebody typed
drifts from the file it describes the first time the file changes, and it
drifts in the one direction that does damage, by continuing to report
agreement.

That digest is deliberately made up. An earlier draft of this paragraph
printed a real one, and it went stale the first time the header changed, which
is the exact failure the paragraph is warning about. For the live value read
`abi_fingerprint` in the archive's `metadata/LINKINFO.json`, or call
`viprs_acad_abi_fingerprint()`, or hash the header yourself. Never copy one out
of prose.

A consumer computes the same number the same way from the header it was
built against, and compares. They differ exactly when the header and the
library came from different commits, which is the failure this catches:
the library loads, every symbol resolves, and a struct field is four bytes
from where the consumer believes it is. A mismatch is
`VIPRS_ACAD_ABI_MISMATCH` and the consumer must stop there rather than call
anything else.

`viprs_acad_capabilities_v1()` is the third, below.

`VIPRS_ACAD_WIRE_VERSION` is a fourth number and it is not one of those three,
because it does not describe this header at all. It is the version of the
batch protocol in docs/WIRE.md, and it moves on its own: a record's payload
can gain a field without a single declaration here changing, so a consumer
that only calls the entry points carries on unaffected while a consumer that
parses the stream must be rebuilt. Wire version 2 is exactly that, giving
`Polyline` and `Polygon` a normal and a per-vertex bulge while
`VIPRS_ACAD_ABI_VERSION` stayed 1. A bump in the other direction, with the
wire standing still, is just as possible, which is why a consumer reads both
rather than inferring either from the other.

A stream whose `wire_version` is not one the consumer parses is
`VIPRS_ACAD_ABI_MISMATCH`, the same code as a failed fingerprint handshake,
because it is the same kind of failure: the two ends of this boundary were
built against different contracts. It is deliberately not
`VIPRS_ACAD_UNSUPPORTED_FORMAT`, which is about the drawing and tells a caller
to look at `dwg_version_min` and `dwg_version_max` and find another reader.
The remedy here is to rebuild, and the two ask for different things.

## Ownership

Everything on this boundary has exactly one owner, and the owner is almost
always the caller.

**Handles.** `viprs_cad_handle` and `viprs_decode_handle` are created by
`viprs_acad_open_path_utf8`, `viprs_acad_open_memory` and
`viprs_acad_decode_begin`, and released by `viprs_acad_close` and
`viprs_acad_decode_close`. They are opaque: the caller never dereferences
one, never does arithmetic on one, and never passes one to the platform
allocator's free function. They are not pointers to anything the caller
could usefully look at, and a future version may make them plain indices.

`viprs_acad_close` releases every decode handle still open on the document.
A decode handle used after the document it came from is closed is a use
after free, and the library refuses it with `VIPRS_ACAD_INVALID_ARGUMENT`
rather than faulting, but the caller should not rely on that as a design.
Close decodes first, then the document.

Both close calls accept a null handle as a no-op and neither can fail, which
is why they return `void`. A close that could fail leaves a caller holding
a handle it has no way to release and no code to look at.

**Input bytes.** `viprs_acad_open_memory` reads the caller's buffer during
the call and never keeps it. The caller may free it the moment the call
returns.

**Output buffers.** Every string and every batch is written into memory the
caller allocated, sized by the caller, with the capacity passed in. Nothing
this library allocates is ever handed out, so there is no free function on
this boundary and there never will be. That rules out the whole family of
bugs where two allocators meet.

**The cancel flag.** Caller-owned, and it must outlive the decode handle it
was given to.

## Threading

One decode handle is single threaded. Calls on one handle must not overlap,
and nothing here is re-entrant.

Two handles may be used from two threads, including two decode handles on
the same document. Opening and closing are independent.

`cancel_flag` is the one word two threads touch at once. It is a
`const uint32_t *` the caller owns; the decoder reads it between batches and
never writes it. Any non-zero value stops the decode with
`VIPRS_ACAD_CANCELED` on the next `viprs_acad_decode_next_batch`. The read
is a plain load, so a caller that wants the flag observed promptly should
write it with whatever release ordering its own language offers. There is no
callback in version 1, deliberately: a callback across this boundary would
put a caller's code on the library's stack, and every question about which
locks are held at that moment has an answer nobody wants to maintain.

## Error model

Every fallible call returns `uint32_t`. Zero is success. The complete set,
frozen by number:

| Code | Name | What it means |
| --- | --- | --- |
| 0 | `VIPRS_ACAD_OK` | The call did what it says. |
| 1 | `VIPRS_ACAD_INVALID_ARGUMENT` | A null pointer, a zero length where one is not allowed, an index out of range, a handle this library did not issue, a struct whose `struct_size` or `struct_version` it does not recognise, or a path that names nothing this process can read. Nothing was written and nothing was allocated. |
| 2 | `VIPRS_ACAD_UNSUPPORTED_FORMAT` | The input is a format, or a version of one, this build does not read. Check `dwg_version_min` and `dwg_version_max`. |
| 3 | `VIPRS_ACAD_CORRUPT_INPUT` | The input is the right format and is damaged. |
| 4 | `VIPRS_ACAD_UNSUPPORTED_ENTITY` | Reserved for a caller that asks for one specific thing this build cannot produce. A drawing containing shapes the decoder has no record type for does not fail: it emits a `Warning` record and carries on. |
| 5 | `VIPRS_ACAD_OUT_OF_MEMORY` | An allocation inside the library failed. |
| 6 | `VIPRS_ACAD_CANCELED` | `cancel_flag` was non-zero. |
| 7 | `VIPRS_ACAD_INTERNAL_ERROR` | A bug in the library. Everything the implementation can throw, in any language it happens to be written in, arrives here. |
| 8 | `VIPRS_ACAD_ABI_MISMATCH` | The fingerprint handshake failed, or a stream carries a `wire_version` this consumer does not parse. |
| 9 | `VIPRS_ACAD_LIMIT_EXCEEDED` | A bound in `viprs_acad_limits_v1` was reached, or the buffer handed to `viprs_acad_decode_next_batch` is too small for the next batch. |

There is no error string anywhere on this boundary, in either direction, and
no call that returns one. A consumer must never parse text to decide what to
do; it switches on the number. Human-readable detail about a particular
drawing arrives as `Warning` records inside the stream, which is data, not
control flow.

Failure is atomic per call. A call that returns non-zero has written nothing
through its out parameters and has allocated nothing the caller must
release. The one documented exception is `viprs_acad_decode_next_batch`
returning `VIPRS_ACAD_LIMIT_EXCEEDED` for a buffer that is too small, which
writes the required size through `written` precisely so the caller can act
on it.

`VIPRS_ACAD_INTERNAL_ERROR` deserves one more sentence, because it is the
code that proves the boundary is real. The implementation is allowed to
throw internally; what it is not allowed to do is let that reach the caller.
Every entry point catches everything on the way out and turns it into this
code, and the caller's process keeps running. The library ships a test-only
export that throws on purpose so that promise is something somebody
observed rather than something the wrapper looks like it does.

## Capability metadata

`viprs_acad_capabilities_v1` answers what a build can do at run time instead
of leaving a consumer to infer it from the version it compiled against.

```c
uint32_t struct_size;      /* set by the caller, sizeof the struct it knows */
uint32_t struct_version;   /* set by the caller, 1 */
uint32_t abi_version;      /* VIPRS_ACAD_ABI_VERSION of this build */
uint32_t wire_version;     /* VIPRS_ACAD_WIRE_VERSION of this build */
uint32_t dwg_version_min;  /* inclusive AC10xx code, 1014 today */
uint32_t dwg_version_max;  /* inclusive AC10xx code, 1032 today */
uint8_t  supports_block_expansion;
uint8_t  supports_warnings;
uint8_t  reserved0;
uint8_t  reserved1;
uint32_t reserved2;
```

The drawing-format range is the numeric AC10xx codes, so `1014` and `1032`
are the oldest and newest this build reads. They come from the backing
reader and they move when it does, which is exactly why a consumer should
ask rather than hard-code them.

The two flags are `uint8_t`, 0 or 1, never a language's boolean.
`supports_block_expansion` is 1 when the decoder can flatten a nested
insertion into transformed primitives; when it is 0 the stream still
decodes, it just contains nothing from inside those insertions.
`supports_warnings` is 1 when reader notifications reach the stream as
`Warning` records.

The pinned version of the backing reader comes back as a UTF-8 string
through the same caller-buffer convention as everything else.

## Views

A document holds one or more views. A view is model space or one of the
paper-space layouts, and a decode runs over exactly one of them.

`viprs_acad_view_count()` reports how many there are. Indices run from zero
to that count minus one, and an index outside the range is
`VIPRS_ACAD_INVALID_ARGUMENT`.

`viprs_acad_view_info_v1()` fills a `viprs_view_info_v1` and writes the
view's name into a caller buffer using the convention below:

```c
uint32_t struct_size;      /* set by the caller */
uint32_t struct_version;   /* set by the caller, 1 */
uint32_t index;            /* echoed back, so a struct can be logged alone */
uint32_t kind;             /* 0 model, 1 layout, 2 unknown */
double   min_x, min_y;     /* drawing-unit extents, all zero when empty */
double   max_x, max_y;
uint64_t entity_count;     /* approximate, for progress reporting only */
```

`kind` is a `uint32_t` and not an enum, for the reason in the fixed-width
rules. `entity_count` counts items before any expansion of nested
insertions, so it is an upper bound on nothing and a lower bound on nothing.
A consumer may show it as progress and must not use it to size a buffer or
to decide the decode finished.

## The caller-buffer convention

Every call that produces text takes three things: a byte pointer, a `cap`
in bytes, and a `required` out parameter. There is no terminator, in either
direction, ever. Implicit termination is how a length gets lost, and once it
is lost nothing downstream can tell a truncated string from a short one.

A caller sizes a buffer by calling once with a null pointer and a capacity
of zero, reading `required`, allocating, and calling again. That first call
is cheap and is allowed to be made every time.

The sizing call returns `VIPRS_ACAD_OK`. It is not a failure, it is the
documented way to ask, and this is the second exception to the atomicity rule
above: the call fills whatever struct it was given as well as `required`. A
null buffer with a capacity that is *not* zero is `VIPRS_ACAD_INVALID_ARGUMENT`,
because that is a caller who has confused the two.

If `cap` is smaller than the required length, the call writes nothing,
sets `required`, and returns `VIPRS_ACAD_LIMIT_EXCEEDED`. It never writes a
partial string, because a partial UTF-8 string can end mid-sequence and a
consumer has no way to tell that from a complete one.

`required` never includes a terminator, because there is none.

## Fixed-width rules

Six rules, and each one is here because its absence has broken a C ABI
somewhere before. All six are checked mechanically against the header, so
they are enforced rather than requested.

1. **Every struct opens with `uint32_t struct_size; uint32_t struct_version;`
   and otherwise holds only fixed-width scalars.** The caller sets both. A
   callee handed a `struct_size` it does not recognise returns
   `VIPRS_ACAD_INVALID_ARGUMENT` instead of reading past what the caller
   allocated. This is what lets version 2 add a field without breaking a
   consumer compiled against version 1, and it only works if both sides
   actually check.
2. **Result codes are plain `uint32_t` constants, never an enum.** The width
   a C compiler picks for an enum is implementation defined, and it is not
   the width another language's compiler picks for its own enum type.
3. **No boolean type.** No two languages agree on the width of theirs, and
   most do not fix it in their own ABI. Flags are `uint8_t`, 0 or 1, and a
   consumer must treat any other value as 1 rather than as an error.
4. **No pointer-sized integers except opaque handles.** Lengths, counts and
   capacities are `uint64_t` on every target. A width that changes between
   two builds of the same consumer is a width nobody tests.
5. **No implicit null termination.** Every string is a byte pointer plus an
   explicit length, in both directions.
6. **No callbacks into the caller.** Cancellation is a flag the callee
   polls, not a function it calls.

Only four scalar types appear in the structs: `uint8_t`, `uint32_t`,
`uint64_t` and `double`. Each is naturally aligned, padding is declared as
reserved members rather than left implicit, and the three structs are
therefore 56, 32 and 56 bytes. A consumer should assert every size and every
offset rather than trust that, because a field in the wrong place does not
fail to compile and does not throw. It reads its neighbour's bytes, and a
64-bit integer read where a `double` lives comes back as a plausible number.

## Limits

Drawing files are untrusted input and this library is not a sandbox. Every
bound the decoder enforces is in `viprs_acad_limits_v1`, set by the caller,
so a host makes its own tradeoff instead of inheriting mine.

Pass a null pointer to either open call for the defaults. Leave a field zero
for the default for that field, so a caller can set one bound without
knowing the rest.

| Field | Default | What hitting it looks like |
| --- | --- | --- |
| `max_input_bytes` | 536870912 (512 MiB) | Refused by the open call before the input is read. |
| `max_entities` | 20000000 | Counted across the whole decode, expansion of nested insertions included. Both the entities the decoder walks and the records it emits are counted against it, and either one passing it is a refusal. |
| `max_string_bytes` | 65536 (64 KiB) | The longest UTF-8 string a `Text` or `Warning` record may carry. |
| `max_polyline_points` | 1000000 | Points in one record: a `Polyline`'s vertices, a `Polygon`'s, and a `Spline`'s control points and knots. Counted before the points are gathered, so an oversized one is refused rather than allocated and then refused. |
| `max_block_depth` | 64 | Nesting depth of an expansion: an insertion inside an insertion, and also a dimension's picture and a hatch's boundary, both of which are made of entities that can expand again. The only reason this field exists is that the alternative to a bounded refusal here is a stack overflow. |
| `max_output_bytes` | 4294967296 (4 GiB) | Total bytes the decode may emit across every batch. |

Two of those wordings are worth reading twice, because the obvious reading
of each leaves a hole an untrusted file walks through.

`max_entities` counts work, not only output. An insertion emits no record
of its own, so a bound that only counted records would let a chain of block
records each holding a few insertions of the next expand exponentially
while emitting nothing: no records to count, no bytes to count, and a
nesting depth that stays inside `max_block_depth` the whole way. Twenty-one
block records and forty-one entities is a file of a few kilobytes and 2^21
expansions. Counting what the decoder visits is what makes the bound bite.

`max_block_depth` counts every kind of expansion. A `DIMENSION` carries a
block of its own and that block can hold another dimension; a `HATCH`
boundary expands into edge entities that expand again. A bound that counted
only insertions would be a bound with a way round it, and the way round it
ends in the stack overflow this field exists to prevent.

Exceeding any of them is `VIPRS_ACAD_LIMIT_EXCEEDED`. It is never a crash
and never a silently truncated stream, which matters more than it sounds:
a consumer that cannot tell a complete decode from a truncated one will
render a drawing with pieces missing and no indication that anything went
wrong.

`struct_size` is checked. A `viprs_acad_limits_v1` whose `struct_size` is
not a size this build knows returns `VIPRS_ACAD_INVALID_ARGUMENT`, and so
does a `struct_version` it does not recognise.

## Decoding

```
open_path_utf8 / open_memory  ->  viprs_cad_handle
  view_count, view_info_v1
  decode_begin(view)          ->  viprs_decode_handle
    decode_next_batch ... until done is 1
  decode_close
close
```

`viprs_acad_decode_next_batch` writes one complete batch per call and sets
`done` to 1 when the stream is finished. A batch never spans two calls. If
`cap` is too small for the next one, the call returns
`VIPRS_ACAD_LIMIT_EXCEEDED` and writes the size needed into `written`, so a
caller that guessed low grows its buffer and retries rather than reasoning
about a half-written record. Batches target 64 KiB and stay under 1 MiB
unless a single record is larger than that, in which case it becomes a batch
of its own, because a batch never splits a record. So a 1 MiB buffer almost
never sees that code, and a caller still has to handle it.

The final batch carries a flag in its own header as well as setting `done`,
so a consumer that streams batches to something else can tell the last one
without tracking the call that produced it. [WIRE.md](WIRE.md) has the
framing.

A cancel flag that is already non-zero before the first call to
`viprs_acad_decode_next_batch` returns `VIPRS_ACAD_CANCELED` immediately,
having written nothing. Cancellation is checked before work, not after.

## The synthetic document

**Both** `viprs_acad_open_memory` and `viprs_acad_open_path_utf8` recognise one
magic byte sequence and open a document this library generates instead of
parsing the bytes as a drawing: the eight ASCII bytes `VIPRSSYN`, optionally
followed by two little-endian `uint32` values giving the number of views and
the number of primitives per view.

Both, not just the one that takes bytes. The sniff happens where an input's
first bytes are read, which is the same place on either route, and a second
implementation written from a document that named only `open_memory` would
have got that wrong in the direction hardest to notice: it would work on
every real drawing. On the path route the magic is the file's first eight
bytes and the two optional counts are the eight after them, exactly as they
are in a buffer.

It exists because a conformance consumer has to be able to reach every entry
point and every record type without a drawing file, and because I would
rather the parser paths were exercised by the library that actually writes
the stream than by a buffer a test assembled by hand. The synthetic document
emits at least one of every record type, and it deliberately emits one
record from the forward-probe range described in WIRE.md, so a consumer's
skip-the-unknown path runs on a real stream on every conformance run.

### Its numbers are contractual, and they are probes

Every scalar in this document is chosen to make a misplaced field visible,
and a consumer is expected to assert on them. That is the opposite of what I
first wrote here, and the reason is worth stating: against a document full of
zeroes and repeated values, swapping an arc's `radius` with its `start_angle`
in the encoder changes nothing any test can see. A field-layout document
nobody reads a field out of is a field-layout document that drifts.

The rule is one sentence. **The k-th `double` in a record's payload, counting
from zero after the geometry prologue, is `100 * type + k + 0.25`.** So a
`Line`, which is type 3, carries `300.25` through `305.25`, and an `Arc`,
type 5, carries `500.25` through `508.25` in the order WIRE.md lists them. No
two fields of a record hold the same number, none holds zero, and none holds
a value that would look right in its neighbour's place.

The rest, in the same spirit:

- `item_handle` is `1000000 + type`, and `flags` and every reserved field are
  zero.
- A `Spline`'s knots, control points and weights are numbered as one run, so
  a consumer tells the three apart by where they start rather than by their
  values.
- `Polyline` and `Polygon` change vertex count as they repeat, and `Text`
  changes length, so the padding and the length invariants are exercised at
  every residue rather than only at the one that happens to be zero. The
  `Text` probe is 19 bytes before the repeat adds up to three more.
- A view's extents are four different numbers, none of them round and none
  symmetric with another.

These are placement probes and not drawable geometry: an `Ellipse` probe has
a ratio above one, which no real ellipse does. Anything reading this document
as a drawing is reading the wrong thing.

What is still not contractual is how much of it there is. The view count and
the primitive count come from the caller's magic bytes, and a build may
choose different defaults, so a consumer asserts the values of the fields it
reads and the shape of the stream, never how many records arrived.
