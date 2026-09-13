#!/usr/bin/env python3
"""Mark sections in an ELF object as retained, so --gc-sections cannot drop them.

Usage: retain_sections.py <object.o> <section> [<section> ...]
       retain_sections.py --check <archive-or-object> <section> [<section> ...]

NativeAOT puts its module headers in a section called `__modules` and the
bootstrapper finds them through `__start___modules` and `__stop___modules`,
the symbols a linker synthesises for any section whose name is a C
identifier. Nothing relocates against the section itself, so its only
references are those two symbols, and since lld 13 `-z start-stop-gc` is
the default: a reference through an encapsulation symbol no longer counts
as a reason to keep the section alive. Under `--gc-sections`, which is
what rustc asks for, lld collects `__modules`, then has nothing to point
`__start___modules` at, and the link fails with an undefined symbol. GNU
ld still keeps it, which is why this only ever showed up on the one target
that links with lld.

Setting SHF_GNU_RETAIN (0x200000) on the section says "keep this whatever
your liveness analysis concludes", which is exactly the claim we want to
make, and it makes the archive carry its own requirement instead of asking
every consumer to pass `-z nostart-stop-gc`. A consumer cannot pass it for
us anyway: `cargo:rustc-link-arg` does not travel from a dependency's build
script to the binary that links it, which is the same limitation that put
the runtime initialiser in an archive of its own.

`objcopy` cannot do this. Its `--set-section-flags` has no spelling for
retain (binutils 2.40 lists alloc, load, noload, readonly, debug, code,
data, rom, exclude, share, contents, merge, strings and nothing else), so
the flag word gets set here by hand.

A section named on the command line and not found in the file is an error.
The names come from what ILC emits, so a rename upstream should stop the
build rather than quietly produce an archive that only links on some
platforms.

`--check` reads a finished `ar` archive, or a bare object, and reports
whether every section it finds by those names is retained. That is the
half that verifies the artifact rather than the recipe, and it is the half
that works everywhere: reading an ELF section header needs no linker and
no matching architecture, so an x64 archive can be checked from an arm64
box and a musl archive from a glibc one. The consumer link test cannot say
that, because it only runs when the host can build for the target, so the
two musl archives shipped a static recipe nothing had ever linked.
"""

import os
import struct
import sys

SHF_GNU_RETAIN = 0x200000

# `SHF_GNU_RETAIN` is in the OS-specific flag range, so binutils only reads
# it as "retain" when the object says which OS ABI it belongs to. On an
# ELFOSABI_NONE object readelf prints the section as `WAo`, some
# OS-specific flag, rather than `WAR`. GAS stamps ELFOSABI_GNU into the
# header whenever it assembles a section with the `R` flag, so setting it
# here is what a compiler would have produced rather than a trick. lld
# honours the flag either way, measured from lld 13 to 22.
EI_OSABI = 7
ELFOSABI_GNU = 3

# Offsets into ELF64: e_shoff at 0x28, then e_shentsize / e_shnum /
# e_shstrndx packed together at 0x3A. sh_flags is the second field of a
# section header, 8 bytes in at offset 8.
E_SHOFF = 0x28
E_SHPARAMS = 0x3A
SH_FLAGS_OFFSET = 8


def retain(path, wanted):
    """Set SHF_GNU_RETAIN on each named section of `path`. Returns a report."""
    with open(path, "rb") as handle:
        data = bytearray(handle.read())

    if data[:4] != b"\x7fELF":
        raise ValueError(f"{path} is not an ELF file")
    if data[4] != 2 or data[5] != 1:
        raise ValueError(f"{path} is not ELF64 little-endian, which is all this handles")

    (e_shoff,) = struct.unpack_from("<Q", data, E_SHOFF)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, E_SHPARAMS)

    def header(index):
        """(file offset of the header, sh_name, sh_flags) for section `index`."""
        at = e_shoff + index * e_shentsize
        name_index, _sh_type, flags = struct.unpack_from("<IIQ", data, at)
        return at, name_index, flags

    strtab_at, _, _ = header(e_shstrndx)
    (strtab_offset,) = struct.unpack_from("<Q", data, strtab_at + 24)

    def name_of(name_index):
        start = strtab_offset + name_index
        return data[start : data.index(b"\0", start)].decode()

    changed = {}
    for index in range(e_shnum):
        at, name_index, flags = header(index)
        name = name_of(name_index)
        if name not in wanted:
            continue
        struct.pack_into("<Q", data, at + SH_FLAGS_OFFSET, flags | SHF_GNU_RETAIN)
        changed[name] = (flags, flags | SHF_GNU_RETAIN)

    if changed:
        data[EI_OSABI] = ELFOSABI_GNU

    missing = [name for name in wanted if name not in changed]
    if missing:
        raise ValueError(
            f"{path} has no section named {', '.join(missing)}. "
            "These names come from what ILC emits, so either the object is not the "
            "one this was meant for or the runtime renamed a section."
        )

    with open(path, "wb") as handle:
        handle.write(data)
    return changed


AR_MAGIC = b"!<arch>\n"


def ar_members(data):
    """Yield (name, offset, size) for every member of an `ar` archive.

    Both long-name conventions are handled, because a Linux archive uses
    the GNU `//` table and a mac archive uses BSD's `#1/<len>`, and a
    reader that knows only one sees garbage names in the other.
    """
    if data[: len(AR_MAGIC)] != AR_MAGIC:
        raise ValueError("not an ar archive")
    at = len(AR_MAGIC)
    longnames = b""
    while at + 60 <= len(data):
        header = data[at : at + 60]
        if header[58:60] != b"`\n":
            raise ValueError(f"member header at offset {at} has bad magic")
        name = header[0:16].decode("ascii", "replace").rstrip()
        size = int(header[48:58].decode("ascii").strip())
        body = at + 60
        if name == "//":
            longnames = data[body : body + size]
        elif name.startswith("#1/"):
            length = int(name[3:])
            name = data[body : body + length].split(b"\0")[0].decode("ascii", "replace")
            body += length
            size -= length
        elif name.startswith("/") and name[1:].isdigit():
            start = int(name[1:])
            name = longnames[start : longnames.index(b"/\n", start)].decode()
        yield name.rstrip("/"), body, size
        at = body + size
        if at % 2:
            at += 1


def osabi_of(blob):
    """The object's declared OS ABI, or None when it is not an ELF64 LE object."""
    if len(blob) < 64 or blob[:4] != b"\x7fELF" or blob[4] != 2 or blob[5] != 1:
        return None
    return blob[EI_OSABI]


def sections_of(blob, wanted):
    """{name: sh_flags} for the wanted sections of the ELF64 LE object in `blob`.

    Returns None when `blob` is not an ELF64 little-endian object, which is
    how the index members and any foreign object in an archive get skipped
    rather than misread.
    """
    if len(blob) < 64 or blob[:4] != b"\x7fELF" or blob[4] != 2 or blob[5] != 1:
        return None
    (e_shoff,) = struct.unpack_from("<Q", blob, E_SHOFF)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", blob, E_SHPARAMS)
    if e_shnum == 0 or e_shoff + e_shnum * e_shentsize > len(blob):
        return {}
    (strtab_offset,) = struct.unpack_from("<Q", blob, e_shoff + e_shstrndx * e_shentsize + 24)

    found = {}
    for index in range(e_shnum):
        at = e_shoff + index * e_shentsize
        name_index, _sh_type, flags = struct.unpack_from("<IIQ", blob, at)
        start = strtab_offset + name_index
        name = blob[start : blob.index(b"\0", start)].decode("ascii", "replace")
        if name in wanted:
            found[name] = flags
    return found


def check(path, wanted):
    """Report every wanted section in `path` and whether it is retained.

    `path` is an `ar` archive or a bare object. Returns a list of
    (member, section, flags, retained) and raises when the file carries no
    such section at all, because "I found nothing to check" and "everything
    I checked was fine" must not both look like success.
    """
    with open(path, "rb") as handle:
        data = handle.read()

    if data[: len(AR_MAGIC)] == AR_MAGIC:
        members = [(name, data[at : at + size]) for name, at, size in ar_members(data)]
    else:
        members = [(os.path.basename(path), data)]

    found = []
    for name, blob in members:
        sections = sections_of(blob, wanted)
        if not sections:
            continue
        gnu_abi = osabi_of(blob) == ELFOSABI_GNU
        for section, flags in sorted(sections.items()):
            # Both halves, because either one alone leaves a linker that
            # ignores the flag. lld needs only the flag; binutils reads it
            # as retain only on a GNU-ABI object.
            retained = bool(flags & SHF_GNU_RETAIN) and gnu_abi
            found.append((name, section, flags, retained))

    if not found:
        raise ValueError(
            f"{path} carries no section named {', '.join(sorted(wanted))}, so there was "
            "nothing to check. Either this is the wrong file or the runtime renamed a "
            "section, and both mean the flag this looks for is not being set."
        )
    return found


def _usage_line(which):
    """The `which`-th line of the docstring's Usage block, counting from zero.

    Indexing the docstring by absolute line number is how the --check
    usage came out blank: line 3 is the empty line under the first Usage
    line, so the program printed nothing and exited 2. Taking the block
    that starts at `Usage:` and ends at the next blank line cannot drift
    when a line is added above it.
    """
    lines = __doc__.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Usage:"))
    block = []
    for line in lines[start:]:
        if not line.strip():
            break
        block.append(line.replace("Usage:", "", 1).strip())
    return f"Usage: {block[which]}"


def main(argv):
    if len(argv) >= 2 and argv[1] == "--check":
        if len(argv) < 4:
            print(_usage_line(1), file=sys.stderr)
            return 2
        path, wanted = argv[2], argv[3:]
        try:
            found = check(path, wanted)
        except (ValueError, OSError, struct.error) as problem:
            print(f"retain_sections.py: {problem}", file=sys.stderr)
            return 1
        bad = 0
        for member, section, flags, retained in found:
            state = "retained" if retained else "NOT RETAINED"
            print(f"{path}({member}): {section} sh_flags {flags:#x} {state}")
            bad += not retained
        if bad:
            print(
                f"retain_sections.py: {bad} section(s) are not retained, so a linker with "
                "-z start-stop-gc will collect them and the encapsulation symbols will be "
                "undefined",
                file=sys.stderr,
            )
            return 1
        return 0

    if len(argv) < 3:
        print(_usage_line(0), file=sys.stderr)
        return 2
    path, wanted = argv[1], argv[2:]
    try:
        changed = retain(path, wanted)
    except (ValueError, OSError, struct.error) as problem:
        print(f"retain_sections.py: {problem}", file=sys.stderr)
        return 1
    for name, (before, after) in changed.items():
        print(f"{path}: {name} sh_flags {before:#x} -> {after:#x}")
    print(f"{path}: EI_OSABI -> {ELFOSABI_GNU} (ELFOSABI_GNU), so binutils reads the flag")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
