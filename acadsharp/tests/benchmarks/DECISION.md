# Block expansion stays for v1, and here is the number that decided it

The issue that produced this directory says the amplification benchmark "is the
number that decides whether block-definition records are needed before
release". This is that decision, written next to the numbers it rests on.
Rerun them with `tests/fixtures/gen/regenerate.py`; they land in
`amplification.json` beside this file and `test_adapter_benchmarks.py` fails if
either grows past the factor recorded there.

**Decision: expand `INSERT` into transformed primitives for wire version 1. Do
not add block-definition and block-instance records before release.**

## What the hostile fixture costs

`g13_many_inserts.dwg` is the issue's hostile case: one block of three entities
(a line, a circle and an arc) inserted ten thousand times.

| | |
| --- | --- |
| Input | 179,150 bytes |
| Records out | 30,004 |
| Bytes out | 2,481,264 |
| Byte amplification | 13.85x the input |
| Batches at 64 KiB | 38 |
| Peak RSS | 94,536 KB |
| Managed bytes retained across the whole decode | 1 KB |

That last row is the one that settles it. Thirty thousand records cross the
boundary and the decoder is holding one kilobyte more at the end than it was
at the start, because the flattened stream is never materialised: it is an
iterator the batch writer pulls from, so a record exists between being produced
and being encoded and then it is garbage. Expansion costs output bytes. It does
not cost memory.

## What block-definition records would have saved here

A definition-plus-instance scheme would emit the block's three records once and
then ten thousand instance records, each carrying a block id and a transform.
An instance record on this wire is a header (8), a geometry prologue (12), a
block id (8) and twelve doubles (96), so 124 bytes.

| | Bytes out |
| --- | --- |
| Expansion, as shipped | 2,481,264 |
| Definition plus instances | about 1,240,000 |

Roughly half, on this fixture. That is not the order of magnitude a new wire
version and a second code path in every consumer should buy.

The saving is a function of block size, not instance count: expanding costs
`instances x entities x 83` bytes and referencing costs `entities x 83 +
instances x 124`. On a three-entity block they are within a factor of two. On a
hundred-entity block inserted ten thousand times, expansion is about 83 MB and
referencing is about 1.3 MB, which is a factor of sixty. The fixture here is
hostile in instance count, which is the cheap axis, and mild in block size,
which is the expensive one.

## When to revisit

Not on a hunch, and not on this fixture. The trigger is a real corpus showing
either of these:

* A single view whose expanded output exceeds the default `max_output_bytes`
  (4 GiB), or forces a host to raise it.
* Byte amplification above 50x input, which on the arithmetic above means
  blocks of roughly twenty entities or more being instanced heavily.

Either one is a wire version bump with `BlockDefinition` and `BlockInstance`
record types, and it goes through the ABI issue's fingerprint gate and both
conformance consumers, not through this lane. Until then the cost is bytes a
caller already bounds with `max_output_bytes` and consumes 64 KiB at a time,
and hiding that cost behind a scheme nothing downstream implements yet would
make the stream harder to consume rather than cheaper.

## The two measurements beside it

**Streaming.** Across 1x, 4x and 16x entity fixtures the decoder retains 1 KB
in every case, and `g13_many_inserts.dwg` at 30,004 records retains 1 KB too.
Peak RSS during a decode moves by a few hundred kilobytes to a few megabytes
and does not track the record count: 2,052 records grew RSS by 2,700 KB and
30,004 records grew it by 636 KB. That is a managed collector's allocation
churn, not the stream accumulating, and the retention number is the one that
says so.

**Path versus memory.** On the largest committed fixture the difference in peak
RSS between `open_path_utf8` and `open_memory` is a few hundred kilobytes
either way, because the fixture is 175 KB and the run-to-run spread of peak RSS
on a managed runtime is a couple of megabytes. The same pair on the same
drawing with 32 MiB appended, which the decode does not read (it produces the
same 2,481,264 bytes of records), separates cleanly: 32,908 KB of peak RSS on a
32,942 KB input, and an allocation difference of 33,716,648 bytes against a
33,733,582 byte file. The shortfall of about 250 KB is the path route's stream
buffering, which the memory route does not need because it wraps the caller's
array. The claim that a path-based open does not duplicate the input to cross
FFI is that measurement.

## What both Criticals had in common, which is worth more than either fix

Every bound this decoder had counted *output*. `max_entities` counted records
emitted, `max_output_bytes` counted bytes, `max_block_depth` counted one kind
of nesting. Nothing counted what the walk *did*, and a document can make a
walk do an unbounded amount of work while producing none of those three: a
chain of block records each holding a few insertions of the next expands
exponentially and emits nothing at all. Both Criticals the review found live
in exactly that gap, and the fix for both is the same shape, counting visits
rather than results.

The next limit anyone adds to this ABI should be checked against that
question before it is written down: does it bound the work, or only the thing
the work happens to produce?
