#!/usr/bin/env python3
"""Mark sections in an ELF object as retained, so --gc-sections cannot drop them.

Usage: retain_sections.py <object.o> <section> [<section> ...]

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
"""

import struct
import sys

SHF_GNU_RETAIN = 0x200000

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


def main(argv):
    if len(argv) < 3:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    path, wanted = argv[1], argv[2:]
    try:
        changed = retain(path, wanted)
    except (ValueError, OSError) as problem:
        print(f"retain_sections.py: {problem}", file=sys.stderr)
        return 1
    for name, (before, after) in changed.items():
        print(f"{path}: {name} sh_flags {before:#x} -> {after:#x}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
