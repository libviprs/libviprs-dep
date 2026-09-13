#!/usr/bin/env python3
"""Bound the DWG reader's allocations by the length of the file it is reading.

    python3 allocation_ceiling.py /build/ACadSharp-3.7.1

`DwgReader` allocates from sizes the file declares, before any bound in
`viprs_acad_limits_v1` is consulted. `max_input_bytes` is applied to the input
before the read starts and nothing after it looks at a length again, so a small
file can ask for an arbitrarily large buffer:

  * `DwgLZ77AC18Decompressor.Decompress(stream, decompressedSize)` allocates
    the whole destination before a byte is decompressed. `decompressedSize` is
    one 4-byte field of the page header, read raw and checked against nothing.
    This is the earliest single-field amplifier in the reader and the one the
    fixtures reach.
  * `getSectionBuffer18` allocates `DecompressedSize * LocalSections.Count` and
    `getSectionBuffer21` the sum of every page's `DecompressedSize`, both
    through `HugeMemoryStream.Create`, which commits the whole length eagerly
    in 1 GiB chunks in its constructor.
  * `getPageBuffer` allocates three arrays straight from the AC21 page header.
  * the descriptor loop runs `PageCount` times and fills gaps by adding a page
    per `DecompressedSize` until it reaches a declared offset, so two fields
    drive an unbounded list before any buffer is asked for.

None of this can be bounded from outside. The types are `internal`, there is
no injection point, and the only process-wide lever is a GC hard limit, which
kills the process and breaks the promise that it stays up for the next file.

So the bound goes here, as a ceiling derived from the input: 64 times the
stream's length, or 16 MiB, whichever is larger. The largest single allocation
anything in the committed corpus asks for is 3.82 times its file's length, and
two of those files are drawings AutoCAD itself wrote, so 64 is sixteen times
the worst measured ratio. A declared size past the ceiling raises
`InvalidDataException`, which the shim's reader catch-all already reports as
`VIPRS_ACAD_CORRUPT_INPUT`: a file whose own header describes something the
file cannot contain is malformed, not a caller asking for too much.

An anchor this cannot find is a refusal, not a warning. A patch that reports
success without applying is an archive nobody can tell from a patched one.
"""

import argparse
import os
import sys

# Every file this edits, relative to the unpacked source root. The driver
# reads this to say what the patch touches without running it.
TARGETS = (
    "src/ACadSharp/IO/DwgReader.cs",
    "src/ACadSharp/IO/DWG/DwgStreamReaders/DwgLZ77AC18Decompressor.cs",
)

# The file the patch adds. Not in TARGETS: it is written, not edited, so it is
# not an anchor that can go missing.
GUARD_PATH = "src/ACadSharp/IO/ViprsAllocationCeiling.cs"

# Present in every file the patch has touched, which is how a second run tells
# "already applied" from "the anchors have moved".
MARKER = "ViprsAllocationCeiling"

GUARD_SOURCE = """// Added to the pinned upstream tree by libviprs-dep:
// acadsharp/patches/allocation_ceiling.py. Not upstream code.
//
// The ceiling every declared size in the DWG reader is measured against. It is
// a function of the input stream's own length, which is the one quantity the
// caller already bounds (viprs_acad_limits_v1.max_input_bytes, applied to the
// input before the read begins), so bounding against it makes every allocation
// below a function of something the caller chose.
//
// Refusing raises InvalidDataException on purpose. The shim reports anything
// the reader throws as VIPRS_ACAD_CORRUPT_INPUT, and that is the right code: a
// file whose header describes a section larger than the file could hold is
// malformed. LIMIT_EXCEEDED would invite the caller to raise a limit and try
// again, and there is no field in the ABI that would change this answer.
using System;
using System.Globalization;
using System.IO;

namespace ACadSharp.IO
{
\tinternal static class ViprsAllocationCeiling
\t{
\t\t// Whichever is larger. The factor is what a big file gets; the floor is
\t\t// what stops a 10 KB drawing from being held to 640 KB, which is below
\t\t// the 29,696-byte section page the format uses by default.
\t\tinternal const long FloorBytes = 16L * 1024L * 1024L;
\t\tinternal const long Factor = 64L;

\t\t// A second bound, on count rather than bytes. The byte ceiling alone
\t\t// leaves one degenerate case: a section declaring a one-byte page size
\t\t// can name enough pages to fill the ceiling and each one is an object.
\t\t// The largest page count in the corpus is 79.
\t\tinternal const long MaxSectionPages = 1048576L;

\t\tinternal static long For(Stream input)
\t\t{
\t\t\tlong length = 0L;
\t\t\tif (input != null)
\t\t\t{
\t\t\t\ttry
\t\t\t\t{
\t\t\t\t\tif (input.CanSeek)
\t\t\t\t\t{
\t\t\t\t\t\tlength = input.Length;
\t\t\t\t\t}
\t\t\t\t}
\t\t\t\tcatch (Exception)
\t\t\t\t{
\t\t\t\t\tlength = 0L;
\t\t\t\t}
\t\t\t}

\t\t\tif (length <= 0L)
\t\t\t{
\t\t\t\treturn FloorBytes;
\t\t\t}

\t\t\tif (length > long.MaxValue / Factor)
\t\t\t{
\t\t\t\treturn long.MaxValue;
\t\t\t}

\t\t\tlong scaled = length * Factor;
\t\t\treturn scaled < FloorBytes ? FloorBytes : scaled;
\t\t}

\t\t// Returns what it was given, so a call site can wrap an expression
\t\t// without restating it. A negative value is refused as well as a large
\t\t// one: every site here reaches this through a cast or a multiplication
\t\t// that can wrap, and a wrapped length is not a small allocation, it is
\t\t// an exception thrown somewhere further away.
\t\tinternal static long Check(long requested, Stream input, string site)
\t\t{
\t\t\tlong ceiling = For(input);
\t\t\tif (requested < 0L || requested > ceiling)
\t\t\t{
\t\t\t\tthrow Refuse(Decimal(requested), ceiling, input, site);
\t\t\t}

\t\t\treturn requested;
\t\t}

\t\tinternal static void CheckUnsigned(ulong requested, Stream input, string site)
\t\t{
\t\t\tlong ceiling = For(input);
\t\t\tif (requested > (ulong)ceiling)
\t\t\t{
\t\t\t\tthrow Refuse(Decimal(requested), ceiling, input, site);
\t\t\t}
\t\t}

\t\t// PageCount and DecompressedSize, before either is used. The loop that
\t\t// follows them runs PageCount times and the buffer that follows that is
\t\t// their product, so this is where both stop being unbounded.
\t\tinternal static void CheckDescriptor(int pages, ulong pageSize, Stream input, string name)
\t\t{
\t\t\tlong ceiling = For(input);
\t\t\tstring site = "section descriptor '" + (name ?? string.Empty) + "'";
\t\t\tif (pageSize == 0UL || pageSize > (ulong)ceiling)
\t\t\t{
\t\t\t\tthrow Refuse(Decimal(pageSize), ceiling, input, site + " page size");
\t\t\t}

\t\t\tif (pages < 0 || pages > MaxSectionPages)
\t\t\t{
\t\t\t\tthrow Refuse(Decimal(pages), MaxSectionPages, input, site + " page count");
\t\t\t}

\t\t\tif ((ulong)pages > (ulong)ceiling / pageSize)
\t\t\t{
\t\t\t\tthrow Refuse(Decimal(pages) + " pages of " + Decimal(pageSize),
\t\t\t\t\tceiling, input, site);
\t\t\t}
\t\t}

\t\t// The gap fill adds one page per DecompressedSize until it reaches this
\t\t// offset, so an offset the file declares is a loop count the file
\t\t// declares.
\t\tinternal static void CheckPageOffset(ulong offset, ulong pageSize, Stream input, string name)
\t\t{
\t\t\tlong ceiling = For(input);
\t\t\tstring site = "section '" + (name ?? string.Empty) + "' page offset";
\t\t\tif (offset > (ulong)ceiling)
\t\t\t{
\t\t\t\tthrow Refuse(Decimal(offset), ceiling, input, site);
\t\t\t}

\t\t\tif (pageSize == 0UL || offset / pageSize > (ulong)MaxSectionPages)
\t\t\t{
\t\t\t\tthrow Refuse(Decimal(offset), MaxSectionPages, input, site + " gap");
\t\t\t}
\t\t}

\t\tprivate static string Decimal(long value)
\t\t{
\t\t\treturn value.ToString(CultureInfo.InvariantCulture);
\t\t}

\t\tprivate static string Decimal(ulong value)
\t\t{
\t\t\treturn value.ToString(CultureInfo.InvariantCulture);
\t\t}

\t\tprivate static string Decimal(int value)
\t\t{
\t\t\treturn value.ToString(CultureInfo.InvariantCulture);
\t\t}

\t\tprivate static long InputLength(Stream input)
\t\t{
\t\t\ttry
\t\t\t{
\t\t\t\treturn input != null && input.CanSeek ? input.Length : -1L;
\t\t\t}
\t\t\tcatch (Exception)
\t\t\t{
\t\t\t\treturn -1L;
\t\t\t}
\t\t}

\t\tprivate static InvalidDataException Refuse(
\t\t\tstring requested,
\t\t\tlong ceiling,
\t\t\tStream input,
\t\t\tstring site)
\t\t{
\t\t\treturn new InvalidDataException(
\t\t\t\t"this drawing declares "
\t\t\t\t\t+ requested
\t\t\t\t\t+ " for its "
\t\t\t\t\t+ site
\t\t\t\t\t+ ", past the ceiling of "
\t\t\t\t\t+ ceiling.ToString(CultureInfo.InvariantCulture)
\t\t\t\t\t+ " this build derives from an input of "
\t\t\t\t\t+ InputLength(input).ToString(CultureInfo.InvariantCulture)
\t\t\t\t\t+ " bytes");
\t\t}
\t}
}
"""

# (file, anchor, replacement). Every anchor must appear exactly once.
EDITS = (
    (
        "src/ACadSharp/IO/DWG/DwgStreamReaders/DwgLZ77AC18Decompressor.cs",
        "\t\t//Create a new stream\n"
        "\t\tMemoryStream memoryStream = HugeMemoryStream.Create(decompressedSize);",
        '\t\tViprsAllocationCeiling.Check(decompressedSize, compressed, "LZ77 page");\n'
        "\t\t//Create a new stream\n"
        "\t\tMemoryStream memoryStream = HugeMemoryStream.Create(decompressedSize);",
    ),
    (
        "src/ACadSharp/IO/DwgReader.cs",
        "\t\tMemoryStream memoryStream = HugeMemoryStream.Create("
        "(long)descriptor.DecompressedSize * descriptor.LocalSections.Count);",
        "\t\tMemoryStream memoryStream = HugeMemoryStream.Create("
        "ViprsAllocationCeiling.Check(\n"
        "\t\t\t(long)descriptor.DecompressedSize * descriptor.LocalSections.Count,\n"
        "\t\t\tthis._fileStream.Stream,\n"
        '\t\t\t"AC18 section buffer"));',
    ),
    (
        "src/ACadSharp/IO/DwgReader.cs",
        "\t\tMemoryStream memoryStream = HugeMemoryStream.Create((long)totalLength);",
        "\t\tViprsAllocationCeiling.CheckUnsigned(\n"
        '\t\t\ttotalLength, this._fileStream.Stream, "AC21 section buffer");\n'
        "\t\tMemoryStream memoryStream = HugeMemoryStream.Create((long)totalLength);",
    ),
    (
        "src/ACadSharp/IO/DwgReader.cs",
        "\t\tbyte[] buffer = new byte[lenght];",
        '\t\tViprsAllocationCeiling.Check(lenght, stream, "AC21 page block buffer");\n'
        "\t\tbyte[] buffer = new byte[lenght];",
    ),
    (
        "src/ACadSharp/IO/DwgReader.cs",
        "\t\tbyte[] compressedData = new byte[(int)totalSize];",
        '\t\tViprsAllocationCeiling.Check(totalSize, stream, "AC21 compressed page");\n'
        "\t\tbyte[] compressedData = new byte[(int)totalSize];",
    ),
    (
        "src/ACadSharp/IO/DwgReader.cs",
        "\t\tbyte[] decompressedData = new byte[uncompressedSize];",
        "\t\tViprsAllocationCeiling.CheckUnsigned("
        'uncompressedSize, stream, "AC21 decompressed page");\n'
        "\t\tbyte[] decompressedData = new byte[uncompressedSize];",
    ),
    (
        "src/ACadSharp/IO/DwgReader.cs",
        "\t\t\tdescriptor.Name = decompressedStream.ReadString(64).Split('\\0')[0];",
        "\t\t\tdescriptor.Name = decompressedStream.ReadString(64).Split('\\0')[0];\n"
        "\t\t\tViprsAllocationCeiling.CheckDescriptor(\n"
        "\t\t\t\tdescriptor.PageCount,\n"
        "\t\t\t\tdescriptor.DecompressedSize,\n"
        "\t\t\t\tsreader.Stream,\n"
        "\t\t\t\tdescriptor.Name);",
    ),
    (
        "src/ACadSharp/IO/DwgReader.cs",
        "\t\t\t\tlocalmap.Offset = decompressedStream.ReadULong();",
        "\t\t\t\tlocalmap.Offset = decompressedStream.ReadULong();\n"
        "\t\t\t\tViprsAllocationCeiling.CheckPageOffset(\n"
        "\t\t\t\t\tlocalmap.Offset,\n"
        "\t\t\t\t\tdescriptor.DecompressedSize,\n"
        "\t\t\t\t\tsreader.Stream,\n"
        "\t\t\t\t\tdescriptor.Name);",
    ),
)


def fail(message):
    sys.stderr.write(f"allocation_ceiling.py: {message}\n")
    raise SystemExit(1)


def read(path):
    with open(path, encoding="utf-8-sig") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", help="the unpacked ACadSharp source root")
    args = parser.parse_args(argv)
    root = args.source

    missing = [rel for rel in TARGETS if not os.path.isfile(os.path.join(root, rel))]
    if missing:
        fail(
            f"{root} does not look like an ACadSharp checkout: {', '.join(missing)} "
            "is not there. Nothing was changed."
        )

    already = [rel for rel in TARGETS if MARKER in read(os.path.join(root, rel))]
    if len(already) == len(TARGETS):
        print(f"allocation_ceiling.py: already applied to {root}")
        return 0
    if already:
        fail(
            f"half applied: {', '.join(already)} already carries the ceiling and the "
            "rest of the tree does not. A tree in this state was patched and then "
            "partly overwritten; unpack it again."
        )

    # Every edit is checked before a byte is written, so a tree is either
    # fully patched or untouched.
    texts = {}
    for rel, anchor, replacement in EDITS:
        path = os.path.join(root, rel)
        text = texts.get(rel) or read(path)
        found = text.count(anchor)
        if found != 1:
            fail(
                f"{rel}: the anchor\n    {anchor.splitlines()[-1].strip()}\n"
                f"appears {found} times, not once. Upstream has moved and this patch "
                "would leave the allocation unbounded. Nothing was changed."
            )
        texts[rel] = text.replace(anchor, replacement)

    for rel, text in texts.items():
        write(os.path.join(root, rel), text)
    write(os.path.join(root, GUARD_PATH), GUARD_SOURCE)
    print(
        f"allocation_ceiling.py: applied {len(EDITS)} guards to "
        f"{len(texts)} files and added {GUARD_PATH}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
