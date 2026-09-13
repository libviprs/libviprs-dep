#!/usr/bin/env python3
"""Build, package and verify the ACadSharp NativeAOT archives.

    python3 acadsharp/build_acadsharp.py                     # the four container cells
    python3 acadsharp/build_acadsharp.py --parallel           # all four at once
    python3 acadsharp/build_acadsharp.py --platform musl      # musl only, both cpus
    python3 acadsharp/build_acadsharp.py --platform mac --arch arm64   # on a Mac
    python3 acadsharp/build_acadsharp.py --plan               # print what it would run
    python3 acadsharp/build_acadsharp.py --upload             # verify, then publish

The shim is `acadsharp/native/Viprs.ACadSharp.Native.csproj`, a NativeAOT
library with `[UnmanagedCallersOnly]` exports over a vendored ACadSharp
checkout, and the C ABI it implements is `include/viprs_acadsharp.h`.
Everything the build depends on is pinned here: the upstream tarball and
its sha256, the upstream commit, the CSUtilities commit the tarball does
not contain, and the .NET SDK version.

The CSUtilities pin is not optional. `src/CSUtilities` is a git submodule,
GitHub-generated source tarballs never carry submodules, and
`ACadSharp.csproj` imports `..\\CSUtilities\\CSMath\\CSMath.projitems`
during project evaluation, so a tarball-only tree fails before restore
with a missing-import error that says nothing about submodules.

Two things this driver will not do, both measured in ADR 0001:

  - It does not emulate. .NET does not support QEMU, so each cell builds
    in a container pinned to its own architecture and a foreign-arch cell
    on a Linux host needs a runner of that architecture. On an Apple
    Silicon host the amd64 cells run under Rosetta, which is not QEMU.
  - It does not put `NativeAOT_StaticInitialization`, or any other
    symbol-forcing flag, in `static_link_args`. That symbol does not
    exist in .NET 10, and more importantly a link argument cannot carry
    a requirement to the binary that needs it: a dependency's build
    script emits `cargo:rustc-link-arg` for its own targets only. The
    runtime's initialiser therefore ships as `static_init_library`, an
    archive the consumer whole-archives ahead of the main one.
"""

import argparse
import concurrent.futures
import datetime
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

GITHUB_REPO = "libviprs/libviprs-dep"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
VERSION_FILE = os.path.join(HERE, "VERSION")
NATIVE_DIR = os.path.join(HERE, "native")
INCLUDE_DIR = os.path.join(HERE, "include")
HEADER_PATH = os.path.join(INCLUDE_DIR, "viprs_acadsharp.h")
DOCS_DIR = os.path.join(HERE, "docs")
SCRIPTS_DIR = os.path.join(HERE, "scripts")
VERIFY_ARCHIVE_SCRIPT = os.path.join(SCRIPTS_DIR, "verify_archive.sh")
PROJECT = os.path.join(NATIVE_DIR, "Viprs.ACadSharp.Native.csproj")
FIXTURE_DWG = os.path.join(HERE, "tests", "fixtures", "real_AC1032.dwg")

# Upstream source. The tag, not a branch: upstream main already carries a
# version ahead of the latest release.
SOURCE_URL = "https://github.com/DomCR/ACadSharp/archive/refs/tags/v{version}.tar.gz"

# Bumping VERSION without adding the matching digest here is a hard error
# rather than a download of whatever the host serves that day. Note the
# caveat ADR 0001 records: this is a *generated* tarball, and GitHub does
# not promise those are reproducible byte for byte forever.
SOURCE_SHA256 = {
    "3.7.1": "0c6b9de91b4f41f355b8caa665bb9904be627a9288fefc6716de8a204a7847a7",
}

# The upstream commit each tag points at. `acadsharp_commit` is a frozen
# LINKINFO field, so this is not decoration: a version with no commit
# here cannot produce a complete manifest.
SOURCE_COMMIT = {
    "3.7.1": "d7dc111023477d8a9fffc2153139459c95b4f345",
}

# The submodule the tarball leaves empty.
CSUTILITIES_URL = "https://github.com/DomCR/CSUtilities.git"
CSUTILITIES_COMMIT = "fdf1403ede6e3376a0baa1deac166cbe4d21262e"

# Must match native/global.json. The library is built on a base Microsoft
# does not test the SDK against, so the SDK that was proven there is part
# of what an archive records about itself.
DOTNET_SDK_VERSION = "10.0.401"
DOTNET_INSTALL_URL = "https://dot.net/v1/dotnet-install.sh"

# Base image for Linux builds. Not the stock .NET SDK image: that one is
# Ubuntu noble, and a library published there needs GLIBC_2.38, which will not
# load on this org's own floor. zstd and pdfium both build on bookworm, which
# pins glibc 2.36 as the oldest runtime we support, and one consumer links all
# three into the same binary. Built on bookworm the same shim tops out at
# GLIBC_2.32. ADR 0001 has the numbers and what the choice costs.
LINUX_BUILD_IMAGE = "debian:bookworm-slim"

# musl gets Alpine, the same base zstd's musl cells use. There is no
# glibc floor to hold here; the floor is musl's own.
MUSL_BUILD_IMAGE = "alpine:3.20"

PLATFORMS = ["linux", "musl", "mac"]

# The whole matrix, keyed by .NET runtime identifier. No Microsoft target
# anywhere: the org ships no such artifact for any dependency.
TARGETS = {
    "linux-x64": {
        "platform": "linux",
        "cpu": "x64",
        "arch": "amd64",
        "triple": "x86_64-unknown-linux-gnu",
        "docker_platform": "linux/amd64",
    },
    "linux-arm64": {
        "platform": "linux",
        "cpu": "arm64",
        "arch": "arm64",
        "triple": "aarch64-unknown-linux-gnu",
        "docker_platform": "linux/arm64",
    },
    "linux-musl-x64": {
        "platform": "musl",
        "cpu": "x64",
        "arch": "amd64",
        "triple": "x86_64-unknown-linux-musl",
        "docker_platform": "linux/amd64",
    },
    "linux-musl-arm64": {
        "platform": "musl",
        "cpu": "arm64",
        "arch": "arm64",
        "triple": "aarch64-unknown-linux-musl",
        "docker_platform": "linux/arm64",
    },
    "osx-arm64": {
        "platform": "mac",
        "cpu": "arm64",
        "arch": "arm64",
        "triple": "aarch64-apple-darwin",
        "docker_platform": None,
    },
}

# (platform, arch) for every cell, which is what the CLI and the archive
# names speak. Derived from TARGETS so the two cannot drift.
MATRIX = [(info["platform"], info["arch"]) for info in TARGETS.values()]

# Default matrix: the cells a container can build. `mac` is excluded
# because there is no macOS container image, the same reason it is out of
# build_pdfium.py's and build_zstd.py's defaults.
DEFAULT_JOBS = [(plat, arch) for plat, arch in MATRIX if plat != "mac"]

# ``--arch`` aliases, same set the sibling drivers accept.
ARCH_ALIASES = {"x86_64": "amd64", "x64": "amd64", "aarch64": "arm64"}

# Static libraries are attempted on Linux and musl only. Whether one
# actually certifies is measured per target and recorded in LINKINFO;
# mac is unmeasured for shared, let alone static, so it is not here.
STATIC_TARGETS = ("linux-x64", "linux-arm64", "linux-musl-x64", "linux-musl-arm64")

# Names inside the archive. The publish emits viprs_acadsharp.so (the
# assembly name); the archive ships a name `-lacadsharp_native` can take.
SHARED_LIBRARY_STEM = "libacadsharp_native"
STATIC_LIBRARY_NAME = "libacadsharp_native.a"
# The runtime's static initialiser, on its own, in its own archive.
#
# It cannot live in the merged archive. libbootstrapperdll.o carries the
# initialiser in .init_array and defines no global symbol, so nothing can
# ever resolve against it and the linker leaves it out; the link then
# succeeds and the binary aborts on the first managed call. Forcing it
# with a link argument works for a hand-written cc line and does not work
# for the consumer this ships to: `cargo:rustc-link-arg` binds only to the
# emitting package's own targets and never reaches a dependent's link
# line, while `cargo:rustc-link-lib` does. So the requirement is expressed
# as a library rather than as an argument, and the consumer whole-archives
# it ahead of the main one.
STATIC_INIT_LIBRARY_NAME = "libacadsharp_native_init.a"
PUBLISHED_ASSEMBLY = "viprs_acadsharp"

# The manifest field lists are frozen. `acadsharp-rs`'s build.rs parses
# LINKINFO.json by these names to emit cargo:rustc-link-* directives and
# checks the ABI fields at first use, so adding, renaming or reordering
# one is a downstream break.
LINKINFO_FIELDS = (
    "schema_version",
    "artifact_version",
    "acadsharp_version",
    "acadsharp_commit",
    "dotnet_sdk",
    "target",
    "platform",
    "cpu",
    "abi_version",
    "wire_version",
    "abi_header_sha256",
    "abi_fingerprint",
    "shared_library",
    "shared_system_libraries",
    "static_library",
    "static_init_library",
    "static_certified",
    "static_system_libraries",
    "static_link_args",
)

# The four that describe the static link appear together or not at all,
# and only when the static smoke linked and ran. A field whose meaning
# depends on another field's value is what a build.rs author gets wrong,
# so "measured" and "absent" are the only two states here rather than
# "present but describing the other linking mode".
STATIC_LINKINFO_FIELDS = (
    "static_library",
    "static_init_library",
    "static_system_libraries",
    "static_link_args",
)

BUILDINFO_FIELDS = (
    "driver_commit",
    "builder_image",
    "dotnet_version",
    "clang_version",
    "linker_version",
    "publish_aot",
    "invariant_globalization",
    "trimmer_roots",
    "trimmer_single_warn",
    "aot_warning_count",
    "built_utc",
)

LINKINFO_SCHEMA_VERSION = 1

# The frozen contracts, shipped inside the archive next to the header.
#
# The premise of this whole directory is that a consumer can be built from
# what we publish, without the producer's source. The header alone does
# not get anyone there: it says what the calls are and nothing about what
# the batch stream holds or how to put the library on a link line, and
# both of those were written down in files that stayed in the repository.
# Anyone writing a consumer then read `build_acadsharp.py` instead, which
# is the coupling freezing the ABI was supposed to remove.
#
# Every *.md directly under `acadsharp/docs/` ships, and
# `test_build_manifests.py` holds this tuple to exactly that set, so a
# fourth contract document ships the day it lands rather than the day
# somebody remembers this line. `docs/adr/` stays behind on purpose: a
# decision record is the producer's history, not the consumer's contract.
PUBLISHED_DOCS = ("ABI.md", "LINKINFO.md", "WIRE.md")

# The flag ADR 0001 measured out of existence, in both spellings. A
# manifest carrying either would break the consumer's link rather than
# harden it, so `make_linkinfo` refuses it rather than trusting whoever
# copied it out of the NativeAOT sample.
DEAD_STATIC_INIT_SYMBOL = "NativeAOT_StaticInitialization"

# Anything that forces a symbol is refused in `static_link_args`, whatever
# symbol it names. Not because forcing is wrong, but because a link
# argument cannot carry a requirement to the binary that needs it: a
# dependency's build script emits `cargo:rustc-link-arg` for its own
# targets only. A requirement that has to travel is a library, and
# `static_init_library` is where this one went.
SYMBOL_FORCING_FLAGS = ("-u", "--undefined", "--require-defined")


class SourceTreeError(RuntimeError):
    """The vendored source is not something that can be built."""


# ---------------------------------------------------------------------------
# Version and source pins
# ---------------------------------------------------------------------------


def read_version(path=VERSION_FILE):
    """Return the version this checkout ships, from ``acadsharp/VERSION``."""
    with open(path) as f:
        version = f.read().strip()
    if not version:
        raise ValueError(
            f"{path} is empty. acadsharp/VERSION is the single source of truth for what "
            "this dependency builds, so an empty file is an error and not a default."
        )
    return version


def split_version(version):
    """Split ``3.7.1-viprs.1`` into the upstream version and the shim revision.

    One file to bump, two numbers in it: upstream decides what source is
    fetched, the shim revision decides what an artifact is called when the
    shim changes without upstream moving.
    """
    upstream, _, suffix = version.partition("-viprs.")
    if not suffix or not suffix.isdigit() or not upstream:
        raise ValueError(
            f"{version!r} is not <upstream>-viprs.<revision>. Without the revision, two "
            "different shims over the same ACadSharp release produce the same artifact name."
        )
    return upstream, suffix


def source_url(version):
    return SOURCE_URL.format(version=version)


def source_sha256(version):
    try:
        return SOURCE_SHA256[version]
    except KeyError:
        raise KeyError(
            f"no sha256 for ACadSharp {version} in SOURCE_SHA256 in "
            f"{os.path.relpath(__file__, REPO_ROOT)}. Record the digest of the tarball "
            "before building against it"
        ) from None


def source_commit(version):
    try:
        return SOURCE_COMMIT[version]
    except KeyError:
        raise KeyError(
            f"no upstream commit for ACadSharp {version} in SOURCE_COMMIT in "
            f"{os.path.relpath(__file__, REPO_ROOT)}. LINKINFO.json records the commit "
            "the artifact was built from, so a tag with no commit cannot be packaged"
        ) from None


def verify_digest(path, expected):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        raise SourceTreeError(f"sha256 mismatch for {path}: expected {expected}, got {actual}")
    return actual


def check_source_tree(root):
    """Fail loudly on the tarball-only tree, before msbuild does it obscurely."""
    csproj = os.path.join(root, "src", "ACadSharp", "ACadSharp.csproj")
    if not os.path.isfile(csproj):
        raise SourceTreeError(f"{csproj} is missing, this is not an ACadSharp checkout")

    for shared in ("CSMath", "CSUtilities"):
        projitems = os.path.join(root, "src", "CSUtilities", shared, f"{shared}.projitems")
        if not os.path.isfile(projitems):
            raise SourceTreeError(
                f"{projitems} is missing. src/CSUtilities is a git submodule and the "
                f"GitHub source tarball does not contain it, so clone {CSUTILITIES_URL} "
                f"at {CSUTILITIES_COMMIT} into src/CSUtilities first. Without it "
                "ACadSharp.csproj fails on a missing Import during evaluation, before "
                "restore, and the error never mentions the submodule."
            )
    return True


def fetch_source(version, dest, downloader=None):
    """Download, verify, unpack and complete the source tree at ``dest``."""
    os.makedirs(dest, exist_ok=True)
    tarball = os.path.join(dest, f"acadsharp-{version}.tar.gz")
    if not os.path.exists(tarball):
        url = source_url(version)
        if downloader is None:
            import urllib.request

            urllib.request.urlretrieve(url, tarball)
        else:
            downloader(url, tarball)
    verify_digest(tarball, source_sha256(version))

    root = os.path.join(dest, f"ACadSharp-{version}")
    if not os.path.isdir(root):
        import tarfile

        with tarfile.open(tarball) as tf:
            tf.extractall(dest)

    submodule = os.path.join(root, "src", "CSUtilities")
    if not os.path.isfile(os.path.join(submodule, "CSMath", "CSMath.projitems")):
        if os.path.isdir(submodule) and not os.listdir(submodule):
            os.rmdir(submodule)
        subprocess.run(["git", "clone", "--quiet", CSUTILITIES_URL, submodule], check=True)
        subprocess.run(
            ["git", "-C", submodule, "checkout", "--quiet", CSUTILITIES_COMMIT], check=True
        )

    check_source_tree(root)
    return root


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


def normalize_arch(arch):
    """Return the canonical arch key for a user-supplied name, or raise."""
    if arch is None:
        return None
    canonical = ARCH_ALIASES.get(arch, arch)
    if canonical not in {info["arch"] for info in TARGETS.values()}:
        raise ValueError(f"Unknown arch '{arch}'. Accepted: amd64/x86_64, arm64/aarch64.")
    return canonical


def rid_for(plat, arch):
    """The .NET runtime identifier for one cell, or raise."""
    if plat not in PLATFORMS:
        raise ValueError(
            f"unknown platform '{plat}'. This dependency targets {', '.join(PLATFORMS)} "
            "and nothing else; there is no Windows artifact for any dependency here."
        )
    for rid, info in TARGETS.items():
        if info["platform"] == plat and info["arch"] == arch:
            return rid
    raise ValueError(
        f"{plat}/{arch} is not a target in this matrix. The whole matrix is "
        + ", ".join(f"{p}/{a}" for p, a in MATRIX)
    )


def target_info(plat, arch):
    return TARGETS[rid_for(plat, arch)]


def rust_triple(plat, arch):
    """The Rust target triple a consumer's build.rs matches against."""
    return target_info(plat, arch)["triple"]


def cpu_for(plat, arch):
    return target_info(plat, arch)["cpu"]


def resolve_jobs(platform_flag, arch_flag):
    """Resolve CLI --platform / --arch flags to a concrete (plat, arch) list.

    Mirrors ``build_zstd.resolve_jobs`` so the drivers behave identically
    at the CLI:

    - No flags: the default matrix.
    - ``--platform X``: filter the default matrix to X, falling back to
      every cell that platform has when the default does not cover it
      (i.e. mac).
    - ``--arch Y``: filter the default matrix by arch.
    - Both: the explicit cross-product, default matrix ignored.
    """
    if platform_flag is None and arch_flag is None:
        return list(DEFAULT_JOBS)

    if platform_flag is not None and arch_flag is not None:
        return [(p, arch_flag) for p in platform_flag]

    if platform_flag is not None:
        plat_set = set(platform_flag)
        filtered = [(p, a) for (p, a) in DEFAULT_JOBS if p in plat_set]
        covered = {p for p, _ in filtered}
        missing = plat_set - covered
        if missing:
            filtered += [(p, a) for (p, a) in MATRIX if p in missing]
        return filtered

    return [(p, a) for (p, a) in DEFAULT_JOBS if a == arch_flag]


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def archive_name(plat, arch):
    """Archive name: acadsharp-{platform}-{cpu}.tgz"""
    return f"acadsharp-{plat}-{cpu_for(plat, arch)}.tgz"


def staging_dir_name(plat, arch):
    """Top-level directory inside the archive: acadsharp-{platform}-{cpu}"""
    return f"acadsharp-{plat}-{cpu_for(plat, arch)}"


def release_tag(version):
    """Release tag derived from the version: acadsharp-3.7.1-viprs.1"""
    return f"acadsharp-{version}"


def builder_image_tag(version, plat, arch):
    """The image one cell builds in: acadsharp-builder-3.7.1-viprs.1-linux-arm64

    A function rather than an expression inside the build, because the image
    outlives the build that made it and something else wants it by name.
    `.github/workflows/acadsharp-conformance.yml` publishes the test
    configuration in it, which costs one publish instead of a second SDK, a
    second clang and a second package restore. A copy of this rule in that
    file is a copy that goes stale on the first version bump.
    """
    return f"acadsharp-builder-{version}-{plat}-{normalize_arch(arch)}".lower()


def shared_ext(plat):
    """Shared library extension for a platform."""
    return "dylib" if plat == "mac" else "so"


def shared_library_name(plat):
    return f"{SHARED_LIBRARY_STEM}.{shared_ext(plat)}"


# ---------------------------------------------------------------------------
# The ABI, read out of the header rather than repeated
# ---------------------------------------------------------------------------


def header_sha256(path=HEADER_PATH):
    """sha256 of the shipped C header, which is what `abi_header_sha256` is."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def abi_fingerprint(path=HEADER_PATH):
    """The first eight bytes of that hash, hex.

    Same value `viprs_acad_abi_fingerprint()` returns, and the header says
    so. Generated from the file rather than assigned, so it cannot drift
    from the header it describes.
    """
    return header_sha256(path)[:16]


def header_versions(path=HEADER_PATH):
    """``(abi_version, wire_version)`` as the header defines them."""
    with open(path) as f:
        text = f.read()
    out = []
    for macro in ("VIPRS_ACAD_ABI_VERSION", "VIPRS_ACAD_WIRE_VERSION"):
        match = re.search(rf"^#define\s+{macro}\s+(\d+)u?\s*$", text, re.MULTILINE)
        if not match:
            raise ValueError(
                f"{path} does not define {macro}. The manifest reports what the header "
                "says rather than a number kept beside it, so a header without the "
                "define cannot be packaged."
            )
        out.append(int(match.group(1)))
    return tuple(out)


# A declaration is a return type at the start of a line, then the name,
# then an open paren. Deliberately not a search for `viprs_acad_*`: the
# header also names structs that way (`viprs_acad_limits_v1`), and those
# are types, never exports.
_ENTRY_POINT_RE = re.compile(
    r"^(?:uint32_t|uint64_t|void)\s+(viprs_acad_[a-z0-9_]+)\s*\(", re.MULTILINE
)


def header_entry_points(path=HEADER_PATH):
    """Every function the header declares, in declaration order."""
    with open(path) as f:
        names = _ENTRY_POINT_RE.findall(f.read())
    if not names:
        raise ValueError(
            f"{path} declares no entry points. The verifier compares the library's "
            "export table against this list, so an empty list would make that check "
            "pass over anything."
        )
    seen = []
    for name in names:
        if name not in seen:
            seen.append(name)
    return tuple(seen)


# ---------------------------------------------------------------------------
# The manifests
# ---------------------------------------------------------------------------


def linkinfo_skeleton(plat, arch, version=None):
    """Everything in LINKINFO.json that is known before the link runs.

    Deliberately without any of the measured link facts: those come off
    the target and are passed to `make_linkinfo`. The example in issue
    #48 is an example, not a default.
    """
    version = version or read_version()
    upstream, _shim = split_version(version)
    abi, wire = header_versions()
    return {
        "schema_version": LINKINFO_SCHEMA_VERSION,
        "artifact_version": version,
        "acadsharp_version": upstream,
        "acadsharp_commit": source_commit(upstream),
        "dotnet_sdk": DOTNET_SDK_VERSION,
        "target": rust_triple(plat, arch),
        "platform": plat,
        "cpu": cpu_for(plat, arch),
        "abi_version": abi,
        "wire_version": wire,
        "abi_header_sha256": header_sha256(),
        "abi_fingerprint": abi_fingerprint(),
        "shared_library": f"lib/{shared_library_name(plat)}",
    }


def make_linkinfo(
    plat,
    arch,
    *,
    shared_system_libraries,
    static_library=None,
    static_init_library=None,
    static_certified=False,
    static_system_libraries=None,
    static_link_args=None,
    version=None,
):
    """The complete manifest for one target.

    The four `static_*` link fields are present exactly when the static
    smoke linked and ran, and absent otherwise. Absent rather than empty,
    because an empty string is a path and an empty list is a measurement,
    and a consumer cannot tell either from "not measured".

    `static_certified` is only ever `True` when the caller has actually
    run that smoke; this function refuses the combinations that cannot be
    true rather than trusting whoever filled the dict in.
    """
    measured = {
        "static_library": static_library,
        "static_init_library": static_init_library,
        "static_system_libraries": static_system_libraries,
        "static_link_args": static_link_args,
    }
    given = {k: v for k, v in measured.items() if v is not None}

    if static_certified:
        missing = sorted(set(STATIC_LINKINFO_FIELDS) - set(given))
        if missing:
            raise ValueError(
                f"static_certified is true but {missing} were not measured. The flag "
                "records that the static smoke linked and ran, which means every part "
                "of that link was observed."
            )
    elif given:
        raise ValueError(
            f"{sorted(given)} were given without static_certified. A static link "
            "nobody ran is not something to describe; ship shared-only instead."
        )

    for field in ("static_library", "static_init_library"):
        value = measured[field]
        if value is not None and not value:
            raise ValueError(
                f"{field} must be absent, not empty, on a target that built none. "
                "An empty path is a path."
            )

    _check_bare_library_names(shared_system_libraries, "shared_system_libraries")
    if static_system_libraries is not None:
        _check_bare_library_names(static_system_libraries, "static_system_libraries")

    for arg in static_link_args or []:
        if DEAD_STATIC_INIT_SYMBOL in arg:
            raise ValueError(
                f"{arg!r} names {DEAD_STATIC_INIT_SYMBOL}, which does not exist in "
                ".NET 10. ADR 0001 measured it: --require-defined for that symbol "
                "fails the link outright, and without it the link succeeds and the "
                "binary matches the JIT oracle. Take static_link_args from the real link."
            )
        if any(flag in arg for flag in SYMBOL_FORCING_FLAGS):
            raise ValueError(
                f"{arg!r} forces a symbol, and a link argument cannot carry that "
                "requirement to the binary that needs it: a dependency's build script "
                "emits cargo:rustc-link-arg for its own targets only, so the flag "
                f"never reaches a dependent's link line. Ship it as "
                f"{STATIC_INIT_LIBRARY_NAME} and let the consumer whole-archive it."
            )

    info = linkinfo_skeleton(plat, arch, version=version)
    info["shared_system_libraries"] = list(shared_system_libraries)
    if static_library is not None:
        info["static_library"] = f"lib/{os.path.basename(static_library)}"
    if static_init_library is not None:
        info["static_init_library"] = f"lib/{os.path.basename(static_init_library)}"
    info["static_certified"] = bool(static_certified)
    if static_system_libraries is not None:
        info["static_system_libraries"] = list(static_system_libraries)
    if static_link_args is not None:
        info["static_link_args"] = list(static_link_args)

    expected = set(LINKINFO_FIELDS)
    if not static_certified:
        expected -= set(STATIC_LINKINFO_FIELDS)
    if set(info) != expected:
        missing = sorted(expected - set(info))
        extra = sorted(set(info) - expected)
        raise ValueError(f"LINKINFO fields drifted: missing {missing}, unexpected {extra}")
    return {k: info[k] for k in LINKINFO_FIELDS if k in info}


def _check_bare_library_names(names, field):
    for name in names:
        if name.startswith("-") or os.sep in name or name.endswith((".a", ".so", ".dylib")):
            raise ValueError(
                f"{field} entries are bare library names, not flags or paths: "
                f"{name!r}. build.rs emits cargo:rustc-link-lib={{}} for each, so a `-l` "
                "here becomes `-l-lm` downstream."
            )


def make_buildinfo(
    *,
    driver_commit,
    builder_image,
    dotnet_version,
    clang_version,
    linker_version,
    aot_warning_count,
    built_utc=None,
):
    """What produced the binaries, recorded next to them."""
    if not isinstance(aot_warning_count, int) or isinstance(aot_warning_count, bool):
        raise ValueError(
            "aot_warning_count is a measurement read out of the publish log, so it is "
            "an integer or the build has not been watched. ADR 0001's number was 16 "
            "for one configuration and is not a constant."
        )
    stamp = built_utc or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    info = {
        "driver_commit": driver_commit,
        "builder_image": builder_image,
        "dotnet_version": dotnet_version,
        "clang_version": clang_version,
        "linker_version": linker_version,
        # The two settings ADR 0001 found load-bearing at run time, and the
        # one that makes the warning count above mean anything.
        "publish_aot": True,
        "invariant_globalization": True,
        "trimmer_roots": ["ACadSharp"],
        "trimmer_single_warn": False,
        "aot_warning_count": aot_warning_count,
        "built_utc": stamp,
    }
    return {k: info[k] for k in BUILDINFO_FIELDS}


def stage_docs(root, docs_dir=DOCS_DIR):
    """Copy the frozen contract documents into ``docs/`` in a staged tree.

    Done here rather than in the staging script because none of these is
    a build input. The header goes through the container because the
    smoke programs compile against it; the documents are only ever read
    by a human or by whoever is writing the consumer, so they are staged
    on the host from the same checkout the header came from and the
    Docker context stays the size it was.

    Returns the archive-relative paths written, so a caller can say what
    it staged. `write_checksums` picks them up by walking the tree, the
    same way it picks up everything else.
    """
    dest = os.path.join(root, "docs")
    os.makedirs(dest, exist_ok=True)
    staged = []
    for name in PUBLISHED_DOCS:
        source = os.path.join(docs_dir, name)
        if not os.path.isfile(source):
            raise ValueError(
                f"{source} is missing, so the archive would ship a README pointing at "
                "a contract that is not in it. The documents are the half of the "
                "publish a consumer author actually reads; shipping the binaries "
                "without them is what sends them to the build driver's source."
            )
        shutil.copy2(source, os.path.join(dest, name))
        staged.append(f"docs/{name}")
    return staged


def write_checksums(root):
    """Write ``metadata/CHECKSUMS.txt`` covering every other file in the tree."""
    meta = os.path.join(root, "metadata")
    os.makedirs(meta, exist_ok=True)
    target = os.path.join(meta, "CHECKSUMS.txt")
    if os.path.exists(target):
        os.remove(target)

    entries = []
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
            entries.append(f"{digest.hexdigest()}  {rel}")

    with open(target, "w") as f:
        f.write("\n".join(sorted(entries, key=lambda line: line.split("  ", 1)[1])) + "\n")
    return target


# ---------------------------------------------------------------------------
# The publish command
# ---------------------------------------------------------------------------


def publish_command(rid, static=False, project=PROJECT, configuration="Release"):
    """The `dotnet publish` this driver runs for one target."""
    cmd = ["dotnet", "publish", project, "-r", rid, "-c", configuration]
    if static:
        cmd.append("-p:NativeLib=Static")
    return cmd


def require_dotnet():
    if shutil.which("dotnet") is None:
        raise SourceTreeError(
            "dotnet is not on PATH. This driver builds inside the .NET SDK container "
            "pinned by acadsharp/native/global.json, not against a host toolchain."
        )


# ---------------------------------------------------------------------------
# The in-container smoke, generated from the header
# ---------------------------------------------------------------------------


def archive_smoke_source(entry_points=None):
    """C source that opens the staged library and exercises the frozen ABI.

    Resolves every entry point the header declares by bare name, then
    calls `viprs_acad_abi_version()` and `viprs_acad_abi_fingerprint()`
    and compares the fingerprint against the hash of the header being
    shipped beside it. This is the "when the smoke runs" half of the
    fingerprint check; the verifier does the static half from the bytes.

    Then it asks the library what it is. `ABI.md` says
    `viprs_acad_capabilities_v1` answers with "the pinned version of the
    backing reader", and every archive published before this check answered
    with nothing: the build staged `native/` and `include/` and never
    `VERSION`, msbuild's `ReadLinesFromFile` returns nothing for a file that
    is not there rather than failing, and the generated constant came out as
    an empty string. Nothing looked, until the conformance consumer ran
    against an unpacked archive in CI for the first time. So the smoke looks
    now, in the container, before the archive is packed.

    It includes the shipped header rather than declaring the struct, for the
    same reason the conformance consumers do: a hand-written copy of a frozen
    struct agrees with whoever typed it and with nothing else.
    """
    names = list(entry_points or header_entry_points())
    listing = "\n".join(f'\t"{name}",' for name in names)
    return f"""\
/* Generated by build_acadsharp.py from include/viprs_acadsharp.h. */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "viprs_acadsharp.h"

static const char *ENTRY_POINTS[] = {{
{listing}
}};

int main(int argc, char **argv)
{{
\tif (argc < 3) {{
\t\tfprintf(stderr, "usage: archive_smoke <library> <expected-fingerprint-hex>\\n");
\t\treturn 2;
\t}}

\tvoid *h = dlopen(argv[1], RTLD_NOW);
\tif (!h) {{
\t\tfprintf(stderr, "DLOPEN_FAILED: %s\\n", dlerror());
\t\treturn 3;
\t}}

\tint missing = 0;
\tunsigned i;
\tfor (i = 0; i < sizeof(ENTRY_POINTS) / sizeof(ENTRY_POINTS[0]); i++) {{
\t\tif (!dlsym(h, ENTRY_POINTS[i])) {{
\t\t\tfprintf(stderr, "MISSING_EXPORT %s\\n", ENTRY_POINTS[i]);
\t\t\tmissing++;
\t\t}}
\t}}
\tif (missing) {{
\t\tfprintf(stderr, "smoke: %d of %u entry points are absent\\n", missing,
\t\t\t(unsigned)(sizeof(ENTRY_POINTS) / sizeof(ENTRY_POINTS[0])));
\t\treturn 4;
\t}}

\tuint32_t (*abi_version)(void) = (uint32_t (*)(void))dlsym(h, "viprs_acad_abi_version");
\tuint64_t (*fingerprint)(void) =
\t\t(uint64_t (*)(void))dlsym(h, "viprs_acad_abi_fingerprint");

\t/* Touch the heap through the library's own runtime before asking it
\t * anything, so a build whose runtime never came up cannot answer. */
\tchar *scratch = (char *)malloc(1 << 20);
\tif (!scratch) {{
\t\tfprintf(stderr, "smoke: out of memory\\n");
\t\treturn 6;
\t}}
\tmemset(scratch, 0x5a, 1 << 20);
\tfree(scratch);

\tchar got[32];
\tsnprintf(got, sizeof(got), "%016llx", (unsigned long long)fingerprint());
\tprintf("ABI_VERSION=%u\\n", abi_version());
\tprintf("ABI_FINGERPRINT=%s\\n", got);
\tif (strcmp(got, argv[2]) != 0) {{
\t\tfprintf(stderr, "smoke: library reports fingerprint %s, header hashes to %s\\n",
\t\t\tgot, argv[2]);
\t\treturn 5;
\t}}

\t/* And what does it say it is? An empty answer is not a version. */
\tuint32_t (*caps_of)(struct viprs_acad_capabilities_v1 *, uint8_t *, uint64_t,
\t\tuint64_t *) = (uint32_t (*)(struct viprs_acad_capabilities_v1 *, uint8_t *,
\t\tuint64_t, uint64_t *))dlsym(h, "viprs_acad_capabilities_v1");
\tif (!caps_of) {{
\t\tfprintf(stderr, "smoke: viprs_acad_capabilities_v1 did not resolve\\n");
\t\treturn 7;
\t}}

\tstruct viprs_acad_capabilities_v1 caps;
\tmemset(&caps, 0, sizeof caps);
\tcaps.struct_size = (uint32_t)sizeof caps;
\tcaps.struct_version = 1;

\tuint64_t needed = 0;
\tuint32_t rc = caps_of(&caps, NULL, 0, &needed);
\tif (rc != 0) {{
\t\tfprintf(stderr, "smoke: the capabilities sizing call returned %u\\n", (unsigned)rc);
\t\treturn 7;
\t}}
\tif (needed == 0) {{
\t\tfprintf(stderr, "smoke: the library reports an empty backing version. ABI.md says "
\t\t\t"this call answers with the pinned version of the backing reader, and a "
\t\t\t"build that never saw acadsharp/VERSION answers with nothing\\n");
\t\treturn 7;
\t}}

\tchar *backing = (char *)malloc((size_t)needed + 1);
\tif (!backing) {{
\t\tfprintf(stderr, "smoke: out of memory\\n");
\t\treturn 6;
\t}}
\trc = caps_of(&caps, (uint8_t *)backing, needed, &needed);
\tif (rc != 0) {{
\t\tfprintf(stderr, "smoke: reading the backing version returned %u\\n", (unsigned)rc);
\t\tfree(backing);
\t\treturn 7;
\t}}
\tbacking[needed] = '\\0';
\tprintf("BACKING_VERSION=%s\\n", backing);
\tfree(backing);
\treturn 0;
}}
"""


def static_smoke_source(entry_points=None):
    """The same check, linked statically rather than opened at run time."""
    names = list(entry_points or header_entry_points())
    externs = "\n".join(
        f"extern uint32_t {n}(void);" for n in names if n != "viprs_acad_abi_fingerprint"
    )
    calls = "\n".join(f"\tsink += (uintptr_t)(void *){n};" for n in names)
    return f"""\
/* Generated by build_acadsharp.py from include/viprs_acadsharp.h. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

{externs}
extern uint64_t viprs_acad_abi_fingerprint(void);

int main(int argc, char **argv)
{{
\tuintptr_t sink = 0;
{calls}
\t/* Touch the heap. A managed export that only returns a constant can be
\t * satisfied by a binary whose runtime never came up; allocating cannot. */
\tchar *scratch = (char *)malloc(1 << 20);
\tif (!scratch) {{
\t\tfprintf(stderr, "static smoke: out of memory\\n");
\t\treturn 6;
\t}}
\tmemset(scratch, 0x5a, 1 << 20);
\tsink += (uintptr_t)scratch[4095];
\tfree(scratch);

\tif (argc < 2) {{
\t\tfprintf(stderr, "usage: static_archive_smoke <expected-fingerprint-hex>\\n");
\t\treturn 2;
\t}}
\tchar got[32];
\tsnprintf(got, sizeof(got), "%016llx", (unsigned long long)viprs_acad_abi_fingerprint());
\tprintf("ABI_VERSION=%u\\n", viprs_acad_abi_version());
\tprintf("ABI_FINGERPRINT=%s\\n", got);
\tprintf("SINK=%d\\n", sink != 0);
\tif (strcmp(got, argv[1]) != 0) {{
\t\tfprintf(stderr, "static smoke: library reports %s, header hashes to %s\\n",
\t\t\tgot, argv[1]);
\t\treturn 5;
\t}}
\treturn 0;
}}
"""


# The runtime archives a statically-linked consumer needs, in the order
# `ar` merges them. Only one of each mutually-exclusive pair: adding both
# GC flavours, or both vxsort flavours, is a duplicate-symbol link error
# rather than a belt-and-braces win. Anything not present in the pack for
# a given architecture is skipped, which is how the x64-only vxsort
# archive stays out of the arm64 merge. ADR 0001 has the list it found by
# hand on linux-x64, vxsort included, and why missing it reads like a
# broken toolchain.
RUNTIME_ARCHIVES = (
    "libRuntime.WorkstationGC.a",
    "libRuntime.VxsortEnabled.a",
    "libeventpipe-disabled.a",
    "libstandalonegc-disabled.a",
    "libaotminipal.a",
    "libstdc++compat.a",
    "libSystem.Native.a",
    "libSystem.Globalization.Native.a",
    "libSystem.IO.Compression.Native.a",
    "libSystem.Net.Security.Native.a",
    "libSystem.Security.Cryptography.Native.OpenSsl.a",
    "libz.a",
    "libbrotlicommon.a",
    "libbrotlidec.a",
    "libbrotlienc.a",
)

# What a statically-linked consumer might still need from the system,
# narrowest first. The staging script walks this ladder and records the
# first rung the smoke both links and runs with, so `static_system_libraries`
# is the measured answer rather than a guess; most of the runtime's own
# dependencies (zlib, brotli, the libstdc++ shim) are merged into the
# archive and are not here. Bare names only: build.rs turns each into a
# cargo:rustc-link-lib, so a `-l` or a path would break downstream.
STATIC_SYSTEM_LIBRARY_LADDER = {
    "linux": ["m", "m rt dl pthread", "m rt dl pthread stdc++"],
    "musl": ["m", "m stdc++"],
}


# ---------------------------------------------------------------------------
# The container build
# ---------------------------------------------------------------------------

# Staging, smoking and fact-gathering, all in one script so the Dockerfile
# stays readable and so the mac path (which has no Dockerfile) runs the
# same code. It never aborts on a failed smoke: it records the outcome in
# facts.txt, the driver turns that into a hard failure after the archive
# has been written, and the verifier refuses the archive independently
# from its bytes. Two independent refusals, neither of them silent.
STAGE_SH = r"""#!/bin/sh
# Usage: stage.sh <rid> <platform> <source-root> <fingerprint> <publish-log>
set -eu

RID="${1:?rid}"
PLAT="${2:?platform}"
SRC="${3:?source root}"
FINGERPRINT="${4:?fingerprint}"
PUBLISH_LOG="${5:?publish log}"

# Both paths are overridable so the macOS host build, which has no
# container and no /staging, runs this same script.
WORK="${WORK:-/work}"
STAGING="${STAGING:-/staging}"
PUB="$WORK/native/bin/Release/net10.0/$RID/publish"
FACTS="$WORK/facts.txt"
: > "$FACTS"

if [ "$PLAT" = "mac" ]; then EXT=dylib; else EXT=so; fi

# One line per key: a later reading replaces an earlier one rather than
# leaving two rows for the same fact in the file the manifests are
# written from.
fact() {
    if [ -s "$FACTS" ]; then
        grep -v "^$1	" "$FACTS" > "$FACTS.tmp" || true
        mv "$FACTS.tmp" "$FACTS"
    fi
    printf '%s\t%s\n' "$1" "$2" >> "$FACTS"
}

# The bare names a consumer would pass to `-l`. libc and the loader are
# dropped: nothing links those by name, and build.rs would emit a
# cargo:rustc-link-lib for each one it is given.
read_needed() {
    if [ "$PLAT" = "mac" ]; then
        otool -L "$1" 2>/dev/null | sed -n 's|.*/lib\([A-Za-z0-9_+.-]*\)\.dylib.*|\1|p'
    else
        readelf -d "$1" 2>/dev/null | sed -n 's/.*NEEDED.*\[\(.*\)\]/\1/p' \
            | sed -e 's/^lib//' -e 's/\.so.*$//'
    fi | grep -vE '^(c|System|acadsharp_native|ld-linux.*|c\.musl.*|ld-musl.*)$' \
        | sort -u | tr '\n' ' '
}

mkdir -p "$STAGING/lib" "$STAGING/include" "$STAGING/LICENSES"

cp "$PUB/viprs_acadsharp.$EXT" "$STAGING/lib/libacadsharp_native.$EXT"
cp "$WORK/include/viprs_acadsharp.h" "$STAGING/include/viprs_acadsharp.h"
cp "$SRC/LICENSE" "$STAGING/LICENSES/ACadSharp-LICENSE"

# Third-party notices: the runtime's own file when the pack ships one,
# plus the package list the publish actually restored. Generated rather
# than written, so it cannot describe a different build.
{
    echo "THIRD PARTY NOTICES"
    echo
    echo "This archive statically contains parts of the .NET runtime, published"
    echo "by Microsoft under the MIT licence, and the NuGet packages listed"
    echo "below. ACadSharp's own licence is in ACadSharp-LICENSE."
    echo
    echo "== NuGet packages restored for this publish =="
    (cd "$WORK/native" && dotnet list package --include-transitive 2>/dev/null) || true
    echo
    NOTICES=$(find "$HOME/.nuget/packages" -maxdepth 3 -iname 'THIRD-PARTY-NOTICES*' \
        2>/dev/null | sort -u)
    for notice in $NOTICES; do
        echo "== $notice =="
        cat "$notice"
        echo
    done
} > "$STAGING/LICENSES/THIRD_PARTY_NOTICES"

fact dotnet_version "$(dotnet --version)"
fact clang_version "$(clang --version 2>/dev/null | head -1)"
fact linker_version "$(ld --version 2>/dev/null | head -1)"
fact aot_warning_count "$(grep -cE 'warning IL[0-9]+' "$PUBLISH_LOG" || true)"

# What the shared library actually needs at load time. libc and the
# loader are dropped: nothing links those by name.
NEEDED=$(read_needed "$STAGING/lib/libacadsharp_native.$EXT")
fact shared_needed "$NEEDED"

# --- shared smoke -----------------------------------------------------
cc -O1 -I"$WORK/include" "$WORK/archive_smoke.c" -o /tmp/archive_smoke -ldl
if /tmp/archive_smoke "$STAGING/lib/libacadsharp_native.$EXT" "$FINGERPRINT" \
        > /tmp/smoke.out 2>&1; then
    fact shared_smoke_ok 1
else
    fact shared_smoke_ok 0
fi
MISSING=$(sed -n 's/^MISSING_EXPORT //p' /tmp/smoke.out | sort -u | tr '\n' ' ')
LIVE_FP=$(sed -n 's/^ABI_FINGERPRINT=//p' /tmp/smoke.out | head -1)
BACKING=$(sed -n 's/^BACKING_VERSION=//p' /tmp/smoke.out | head -1)
fact missing_exports "$MISSING"
fact live_fingerprint "$LIVE_FP"
fact backing_version "$BACKING"
echo "---- shared smoke ----"
cat /tmp/smoke.out

# --- static, best effort ---------------------------------------------
fact static_ok 0
if [ "${WANT_STATIC:-0}" = "1" ]; then
    echo "---- static publish ----"
    if (cd "$WORK/native" && dotnet publish Viprs.ACadSharp.Native.csproj -r "$RID" \
            -c Release -p:NativeLib=Static \
            -p:AcadSharpProject="$SRC/src/ACadSharp/ACadSharp.csproj" \
            > "$WORK/publish-static.log" 2>&1); then
        MANAGED=$(find "$WORK/native/bin" -name 'viprs_acadsharp.a' | head -1)
        # The runtime archives live next to libbootstrapperdll.o in the
        # NativeAOT runtime pack. Finding them by that file rather than by
        # a path pattern means a pack layout change is a missing-file
        # error here rather than a silent shared-only downgrade.
        BOOTSTRAP=$(find "$HOME/.nuget/packages" -name 'libbootstrapperdll.o' | head -1)
        PACK=$(dirname "$BOOTSTRAP")
        INIT_SYM=""
        if [ -n "$MANAGED" ] && [ -f "$BOOTSTRAP" ]; then
            # libbootstrapperdll.o carries the runtime's static
            # initialiser in .init_array and defines no global symbol at
            # all, so inside an archive nothing can ever pull it in and
            # the first managed call aborts. .NET 10 removed
            # NativeAOT_StaticInitialization, the symbol the sample says
            # to force, so there is nothing left to --require-defined.
            # Globalising the initialiser gives the link something to
            # reach, and it ships in an archive of its own that the
            # consumer whole-archives, because an argument that forces a
            # symbol does not travel from a dependency's build script to
            # the binary that needs it and a library does.
            INIT_SYM=$(nm "$BOOTSTRAP" \
                | awk '$2 == "t" && $3 ~ /^_GLOBAL__sub_I/ { print $3; exit }')
        fi
        if [ -z "$INIT_SYM" ]; then
            echo "no managed archive, runtime pack or static initialiser; shipping shared-only"
        else
            INIT_A="$STAGING/lib/libacadsharp_native_init.a"
            MERGED="$STAGING/lib/libacadsharp_native.a"
            rm -f "$INIT_A" "$MERGED"
            objcopy --globalize-symbol="$INIT_SYM" "$BOOTSTRAP" /tmp/bootstrapperdll.o
            ar rcs "$INIT_A" /tmp/bootstrapperdll.o

            # Merge by extracting rather than by `ar addlib`, because
            # addlib keeps each source's member names and two runtime
            # archives ship objects of the same name. The result was an
            # archive holding two members called entrypoints.c.o, which
            # links today only because the linker takes the first
            # definition it finds. Prefixing each member with the archive
            # it came from keeps every object and leaves no two sharing a
            # name.
            #
            # Order is load-bearing and this is the expensive half of the
            # lesson. Members go in the order each source archive lists
            # them, managed archive first, which is the order ILC linked
            # them in and the order addlib produced. Built from the
            # filesystem's order instead, the archive links perfectly and
            # the binary segfaults on the way out, every time: the
            # linker emits .init_array in the order it pulls members, so
            # the archive's own order decides what runs when. Only
            # running the smoke catches that, which is why it is run.
            MERGE_DIR=/tmp/merge
            rm -rf "$MERGE_DIR"
            mkdir -p "$MERGE_DIR"
            MERGE_OK=1
            echo "$MANAGED" > /tmp/merge-sources.txt
            for name in RUNTIME_ARCHIVE_LIST; do
                [ -f "$PACK/$name" ] && echo "$PACK/$name"
            done >> /tmp/merge-sources.txt
            : > /tmp/merge-members.txt
            while read -r src; do
                [ -n "$src" ] || continue
                stem=$(basename "$src" .a)
                d="$MERGE_DIR/$stem"
                mkdir -p "$d"
                (cd "$d" && ar x "$src")
                WANT=$(ar t "$src" | wc -l)
                GOT=$(find "$d" -maxdepth 1 -type f | wc -l)
                if [ "$WANT" -ne "$GOT" ]; then
                    echo "$src holds $WANT members but extracted $GOT, so a member was lost"
                    MERGE_OK=0
                fi
                ar t "$src" | while read -r member; do
                    [ -f "$d/$member" ] || continue
                    mv "$d/$member" "$MERGE_DIR/${stem}__$member"
                    echo "$MERGE_DIR/${stem}__$member"
                done >> /tmp/merge-members.txt
                rmdir "$d" 2>/dev/null || true
            done < /tmp/merge-sources.txt

            # The module table has to survive --gc-sections, and by
            # default under lld it does not.
            #
            # ILC puts the runtime's module headers in a section called
            # __modules and the bootstrapper walks it through
            # __start___modules and __stop___modules, the symbols a linker
            # synthesises around any section whose name is a C identifier.
            # Nothing relocates against the section, so those two symbols
            # are the only references to it, and lld has defaulted to
            # -z start-stop-gc since version 13, which says a reference
            # through an encapsulation symbol is not a reason to keep a
            # section. rustc asks for --gc-sections, so on every target
            # whose linker is lld the section goes and the link fails with
            # an undefined __start___modules. GNU ld keeps it, which is
            # why the C smoke below passes, why every arm64 job passed,
            # and why this only ever showed up on linux/x64 (#67).
            #
            # Three sections, not one. The bootstrapper references six
            # encapsulation symbols, around __modules, __managedcode and
            # __unbox. Only __modules dies today, because ILC emits each
            # of the other two as one monolithic section and any live
            # symbol in it keeps the whole thing: measured, 59471
            # relocations reach __managedcode and 1259 reach __unbox
            # against zero for __modules. That is an accident of how ILC
            # lays out sections, not a guarantee, so all three get the
            # flag. It is free: retaining all three produces a binary of
            # identical size with a byte-identical .init_array.
            #
            # Setting SHF_GNU_RETAIN on the section makes the archive
            # carry its own requirement. The alternative is asking every
            # consumer to pass -z nostart-stop-gc, and a consumer cannot:
            # cargo:rustc-link-arg does not travel from a dependency's
            # build script to the binary that links it, which is the same
            # limitation that put the initialiser in its own archive.
            RETAINED=0
            for OBJ in "$MERGE_DIR"/*.o; do
                [ -f "$OBJ" ] || continue
                readelf -S -W "$OBJ" 2>/dev/null \
                    | sed -n 's/^ *\[ *[0-9]*\] *\([^ ]*\) .*/\1/p' \
                    | grep -qx __modules || continue
                if python3 "$WORK/retain_sections.py" "$OBJ" \
                        __modules __managedcode __unbox; then
                    RETAINED=$((RETAINED + 1))
                else
                    MERGE_OK=0
                fi
            done
            fact retained_module_sections "$RETAINED"
            # At least one object has to carry it. There is no upper
            # bound on purpose: `__modules` is an encapsulation array and
            # N contributors is its designed shape, so an exact count
            # would redden a release for an archive that links perfectly
            # the day ILC splits its output or a second NativeAOT library
            # joins the merge.
            if [ "$RETAINED" -lt 1 ]; then
                echo "no object carries __modules, so the runtime renamed a section"
                MERGE_OK=0
            fi

            if [ "$MERGE_OK" != "1" ]; then
                echo "the merge would have dropped an object; shipping shared-only"
                rm -f "$INIT_A" "$MERGED"
            else
                # `q` appends without reordering, so a list too long for
                # one argv still goes in the order it was written.
                tr '\n' '\0' < /tmp/merge-members.txt | xargs -0 ar qc "$MERGED"
                ranlib "$MERGED"
                echo "---- static smoke ----"
                LADDER='STATIC_SYSTEM_LIBRARY_LADDER'
                OLDIFS=$IFS
                IFS=';'
                for CAND in $LADDER; do
                    IFS=$OLDIFS
                    SYSLIBS=""
                    for lib in $CAND; do SYSLIBS="$SYSLIBS -l$lib"; done
                    # The documented link: the initialiser archive whole,
                    # ahead of the main one. Reversing the two fails with
                    # an undefined reference to RhRegisterOSModule, and
                    # dropping the whole-archive links clean and aborts on
                    # the first managed call.
                    # shellcheck disable=SC2086
                    if cc -O1 "$WORK/static_archive_smoke.c" \
                            -Wl,--whole-archive "$INIT_A" -Wl,--no-whole-archive \
                            "$MERGED" $SYSLIBS -o /tmp/static_smoke \
                            > /tmp/static_link.log 2>&1 \
                            && /tmp/static_smoke "$FINGERPRINT" > /tmp/static_smoke.out 2>&1; then
                        fact static_ok 1
                        fact static_system_libraries "$CAND"
                        fact static_link_args ""
                        cat /tmp/static_smoke.out
                        break
                    fi
                    IFS=';'
                done
                IFS=$OLDIFS
                CERTIFIED=$(awk -F'\t' '$1 == "static_ok" { v = $2 } END { print v }' "$FACTS")
                if [ "$CERTIFIED" != "1" ]; then
                    echo "static smoke did not pass, shipping shared-only:"
                    tail -30 /tmp/static_link.log 2>/dev/null || true
                    tail -10 /tmp/static_smoke.out 2>/dev/null || true
                    rm -f "$MERGED" "$INIT_A"
                fi
            fi
        fi
    else
        echo "static publish failed, shipping shared-only:"
        tail -40 "$WORK/publish-static.log" || true
    fi
fi

echo "---- staged ----"
ls -lR "$STAGING"
cat "$FACTS"
"""


def stage_script(plat):
    """`STAGE_SH` with the two per-platform lists substituted in."""
    return STAGE_SH.replace("RUNTIME_ARCHIVE_LIST", " ".join(RUNTIME_ARCHIVES)).replace(
        "STATIC_SYSTEM_LIBRARY_LADDER", ";".join(STATIC_SYSTEM_LIBRARY_LADDER.get(plat, [""]))
    )


def make_dockerfile(version, plat, arch):
    """Generate the Dockerfile that builds and stages one cell.

    The container is pinned to the *target* architecture: .NET does not
    support QEMU and cross-architecture publishing needs a cross
    toolchain the SDK does not ship, so there is nothing to cross-compile
    here. ADR 0001 measured both.
    """
    if plat == "mac":
        raise ValueError("platform 'mac' has no Docker base image (NativeAOT cannot cross-OS)")

    rid = rid_for(plat, arch)
    info = TARGETS[rid]
    base_image = MUSL_BUILD_IMAGE if plat == "musl" else LINUX_BUILD_IMAGE
    # The artifact version carries the shim revision; upstream's tarball,
    # digest and directory name do not know about it. Accepts either form
    # so a caller holding only the upstream number still gets a build.
    upstream = split_version(version)[0] if "-viprs." in version else version
    url = source_url(upstream)
    sha = source_sha256(upstream)
    src_root = f"/build/ACadSharp-{upstream}"
    want_static = "1" if rid in STATIC_TARGETS else "0"

    if plat == "musl":
        install_deps = (
            "RUN apk add --no-cache bash curl ca-certificates git clang build-base \\\n"
            "    zlib-dev zlib-static openssl-dev openssl-libs-static binutils file \\\n"
            "    libstdc++ libgcc icu-libs krb5-libs lld python3"
        )
    else:
        install_deps = (
            "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
            "    curl ca-certificates git clang zlib1g-dev libssl-dev binutils \\\n"
            "    build-essential file libicu72 python3-minimal \\\n"
            "    && rm -rf /var/lib/apt/lists/*"
        )

    return f"""\
FROM --platform={info["docker_platform"]} {base_image}

# Step 0: toolchain. ILC links with clang, so clang and zlib headers are
# not optional; binutils supplies the `ar` the static merge drives.
{install_deps}

ENV DOTNET_CLI_TELEMETRY_OPTOUT=1 \\
    DOTNET_NOLOGO=1 \\
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 \\
    PATH=/usr/share/dotnet:$PATH

# Step 1: the SDK, installed rather than inherited from an image. There is
# no bookworm SDK image, and the stock one is Ubuntu noble, whose output
# needs GLIBC_2.38 and will not load on this org's floor. ADR 0001 has the
# numbers.
RUN curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 \\
        -o /tmp/dotnet-install.sh "{DOTNET_INSTALL_URL}" \\
    && bash /tmp/dotnet-install.sh --version {DOTNET_SDK_VERSION} \\
        --install-dir /usr/share/dotnet \\
    && dotnet --version

# Step 2: the pinned upstream tarball, checked against the digest recorded
# in build_acadsharp.py before a line of it is compiled.
RUN curl -fsSL --retry 3 --retry-delay 5 --connect-timeout 30 \\
        -o /tmp/acadsharp.tar.gz "{url}" \\
    && echo "{sha}  /tmp/acadsharp.tar.gz" | sha256sum -c - \\
    && mkdir -p /build && tar xzf /tmp/acadsharp.tar.gz -C /build \\
    && rm /tmp/acadsharp.tar.gz

# Step 3: the submodule the tarball does not contain. Without it
# ACadSharp.csproj fails on a missing Import during evaluation, before
# restore, with an error that never mentions submodules.
RUN rmdir {src_root}/src/CSUtilities 2>/dev/null || true; \\
    git clone --quiet {CSUTILITIES_URL} {src_root}/src/CSUtilities \\
    && git -C {src_root}/src/CSUtilities checkout --quiet {CSUTILITIES_COMMIT}

# Step 4: the shim and the frozen header
COPY native /work/native
COPY include /work/include
COPY VERSION /work/VERSION
COPY archive_smoke.c static_archive_smoke.c stage.sh retain_sections.py /work/

# Step 5: publish the shared library. The log is kept because the AOT
# warning count in BUILDINFO.json is read out of it.
RUN cd /work/native \\
    && dotnet publish Viprs.ACadSharp.Native.csproj -r {rid} -c Release \\
        -p:AcadSharpProject={src_root}/src/ACadSharp/ACadSharp.csproj \\
        > /work/publish-shared.log 2>&1 \\
    || (tail -60 /work/publish-shared.log; exit 1)

# Step 6: stage the archive contents, run the smokes and record the facts
# the manifests are written from.
ENV WANT_STATIC={want_static}
RUN sh /work/stage.sh {rid} {plat} {src_root} {abi_fingerprint()} /work/publish-shared.log
"""


# ---------------------------------------------------------------------------
# Running things
# ---------------------------------------------------------------------------


def stream(cmd, log_file, prefix, env=None, cwd=None):
    """Run a command, tee its output to ``log_file`` and to stdout."""
    display = " ".join(cmd)
    print(f"[{prefix}] $ {display}", flush=True)
    log_file.write(f"\n$ {display}\n")
    log_file.flush()
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
        cwd=cwd,
    )
    for line in process.stdout:
        line = line.rstrip("\n")
        log_file.write(f"{line}\n")
        log_file.flush()
        print(f"[{prefix}] {line}", flush=True)
    process.wait()
    return process.returncode


def run_checked(cmd, log_file, prefix, **kwargs):
    rc = stream(cmd, log_file, prefix, **kwargs)
    if rc != 0:
        raise RuntimeError(f"{cmd[0]} failed with exit {rc}: {' '.join(cmd)}")


def driver_commit():
    """The commit this driver was run from, for BUILDINFO.json."""
    result = subprocess.run(
        ["git", "-C", REPO_ROOT, "rev-parse", "HEAD"], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def read_facts(path):
    """Parse the tab-separated facts the staging script recorded."""
    facts = {}
    with open(path) as f:
        for line in f:
            key, _, value = line.rstrip("\n").partition("\t")
            if key:
                facts[key] = value
    return facts


def archive_readme(plat, arch, version):
    """The README.md that ships inside the archive."""
    name = staging_dir_name(plat, arch)
    static_note = (
        "`lib/libacadsharp_native.a` and `lib/libacadsharp_native_init.a` are "
        "present only when the static smoke linked and ran on this target; "
        "`metadata/LINKINFO.json` records that as `static_certified`. Link the "
        "init archive whole and ahead of the main one: it carries the runtime's "
        "static initialiser and nothing references it, so without "
        "`--whole-archive` the link succeeds and the first call aborts."
    )
    return f"""\
# {name}

ACadSharp {split_version(version)[0]} behind the VIPRS CAD C ABI, built with
.NET NativeAOT. No .NET runtime is needed to use it.

    lib/       the shared library, and the static archive where one certified
    include/   viprs_acadsharp.h, the frozen ABI this library implements
    docs/      ABI.md, WIRE.md and LINKINFO.md, the three frozen contracts
    metadata/  LINKINFO.json, BUILDINFO.json, CHECKSUMS.txt
    LICENSES/  ACadSharp's MIT licence and the third-party notices

Everything needed to write a consumer is in here. `docs/ABI.md` is the
prose half of the header: ownership, threading, the error model and the
fingerprint handshake. `docs/WIRE.md` is the complete definition of the
batch stream the decode calls emit, which is not visible in the header at
all. `docs/LINKINFO.md` defines every field of `metadata/LINKINFO.json`,
and carries the static linking recipe, which is the part that is subtle
enough to get wrong twice.

Read `metadata/LINKINFO.json` rather than guessing: it carries the Rust
target triple, the ABI and wire versions, the header's sha256 and
fingerprint, `shared_system_libraries` measured from the shared library's
own NEEDED list, and, when the static smoke certified this target, the
`static_*` fields measured from that link. {static_note}

Verify the archive before using it:

    acadsharp/scripts/verify_archive.sh {name}.tgz

Built from the pinned upstream tarball recorded in `BUILDINFO.json`.
Artifact version {version}.
"""


def finish_archive(staging_root, plat, arch, facts, *, builder_image, version=None):
    """Write the manifests, the README and CHECKSUMS.txt into a staged tree."""
    version = version or read_version()
    static_path = os.path.join(staging_root, "lib", STATIC_LIBRARY_NAME)
    init_path = os.path.join(staging_root, "lib", STATIC_INIT_LIBRARY_NAME)
    certified = (
        facts.get("static_ok") == "1" and os.path.isfile(static_path) and os.path.isfile(init_path)
    )

    # Measured on every target, from the shared library's own NEEDED list
    # with libc and the loader dropped, because nothing links those by
    # name. This is the list a consumer linking the shared library needs,
    # and it is not the static link's list; they used to share a field.
    shared_system_libraries = facts.get("shared_needed", "").split()

    if certified:
        linkinfo = make_linkinfo(
            plat,
            arch,
            shared_system_libraries=shared_system_libraries,
            static_library=STATIC_LIBRARY_NAME,
            static_init_library=STATIC_INIT_LIBRARY_NAME,
            static_certified=True,
            static_system_libraries=facts.get("static_system_libraries", "").split(),
            static_link_args=[a for a in facts.get("static_link_args", "").split() if a],
            version=version,
        )
    else:
        linkinfo = make_linkinfo(
            plat, arch, shared_system_libraries=shared_system_libraries, version=version
        )

    warnings = facts.get("aot_warning_count", "").strip()
    buildinfo = make_buildinfo(
        driver_commit=driver_commit(),
        builder_image=builder_image,
        dotnet_version=facts.get("dotnet_version", DOTNET_SDK_VERSION),
        clang_version=facts.get("clang_version", "unknown"),
        linker_version=facts.get("linker_version", "unknown"),
        aot_warning_count=int(warnings) if warnings.isdigit() else 0,
    )

    meta = os.path.join(staging_root, "metadata")
    os.makedirs(meta, exist_ok=True)
    for name, doc in (("LINKINFO.json", linkinfo), ("BUILDINFO.json", buildinfo)):
        with open(os.path.join(meta, name), "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")

    with open(os.path.join(staging_root, "README.md"), "w") as f:
        f.write(archive_readme(plat, arch, version))

    stage_docs(staging_root)

    # An uncertified target must not ship either static archive. Shared-only
    # is a recorded outcome; a `.a` nobody has linked is an invitation.
    if not certified:
        for path in (static_path, init_path):
            if os.path.isfile(path):
                os.remove(path)

    write_checksums(staging_root)
    return linkinfo


def verify_archive(path, log_file=None, prefix="verify"):
    """Run scripts/verify_archive.sh against a packaged .tgz."""
    cmd = ["bash", VERIFY_ARCHIVE_SCRIPT, path]
    if log_file is None:
        result = subprocess.run(cmd, capture_output=True, text=True)
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        rc = result.returncode
    else:
        rc = stream(cmd, log_file, prefix)
    if rc != 0:
        raise RuntimeError(f"verify_archive.sh rejected {os.path.basename(path)} (exit {rc})")


def _write_build_context(ctx, plat):
    """Drop the generated helpers into a docker build context or work dir."""
    # bin/ and obj/ are whatever a local `dotnet publish` left behind.
    # Copying them bloats the build context and puts a stale intermediate
    # tree in front of the container's own restore.
    shutil.copytree(
        NATIVE_DIR,
        os.path.join(ctx, "native"),
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("bin", "obj"),
    )
    os.makedirs(os.path.join(ctx, "include"), exist_ok=True)
    shutil.copy2(HEADER_PATH, os.path.join(ctx, "include", "viprs_acadsharp.h"))
    # The csproj reads ../VERSION during the build and generates the string
    # viprs_acad_capabilities_v1 reports. Without the file here, msbuild's
    # ReadLinesFromFile returns nothing rather than failing, the generated
    # constant comes out as "", and every archive published so far shipped a
    # library that answers the version question with an empty string.
    shutil.copy2(VERSION_FILE, os.path.join(ctx, "VERSION"))
    # Copied rather than generated: it is a real script with its own
    # tests, and a second copy inside a Python string is a second thing to
    # keep right.
    shutil.copy2(
        os.path.join(SCRIPTS_DIR, "retain_sections.py"),
        os.path.join(ctx, "retain_sections.py"),
    )
    for name, text in (
        ("archive_smoke.c", archive_smoke_source()),
        ("static_archive_smoke.c", static_smoke_source()),
        ("stage.sh", stage_script(plat)),
    ):
        path = os.path.join(ctx, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)
    return ctx


def build_for_job(version, plat, arch, output_dir):
    """Build one cell and return the path to its archive.

    It raises on failure and never returns None. It used to take a keep_going
    flag that no caller ever passed, so the branch behind it was a documented
    behaviour the driver did not have: a reader of this signature would think a
    failed cell could come back as None and write a caller that handled it.
    """
    job = f"{plat}/{arch}"
    log_dir = os.path.join(output_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{plat}-{arch}.log")

    with open(log_path, "w") as log_file:
        log_file.write(
            f"# acadsharp build log\n# job:     {job}\n# version: {version}\n"
            f"# started: {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n\n"
        )
        log_file.flush()
        try:
            if plat == "mac":
                path, facts = _build_mac_native(version, arch, output_dir, log_file, job)
            else:
                path, facts = _build_docker(version, plat, arch, output_dir, log_file, job)

            if facts.get("shared_smoke_ok") != "1":
                missing = facts.get("missing_exports", "").split()
                if missing:
                    detail = f"the library does not export {', '.join(missing)}"
                elif not facts.get("backing_version"):
                    detail = (
                        "the library reports an empty backing version, so "
                        "viprs_acad_capabilities_v1 cannot answer what it is"
                    )
                else:
                    detail = "see the smoke output in the log"
                raise RuntimeError(
                    f"the ABI smoke failed against the staged library: {detail}. "
                    f"The archive is at {path} for inspection; it is not shippable."
                )

            # A target that is supposed to carry a static half and does not.
            #
            # Every step in the static branch of the staging script
            # degrades the same way: it drops the two archives and lets
            # the cell finish, so `static_certified` comes out false, the
            # manifest legally omits the static fields, the verifier is
            # happy because shipping shared-only is a recorded outcome,
            # and the release page shows `false` in a column nobody reads.
            # That is the right behaviour for a target that was never
            # meant to have one and the wrong behaviour for these four,
            # and there was nothing holding them to it.
            #
            # It matters more now than it did. Retaining `__modules` is a
            # new way for the static branch to give up, and it can give up
            # on every ELF target at once (no python3 in the image, a
            # section ILC renamed, an object that will not rewrite).
            # Before, a failure there reddened the cell.
            if rid_for(plat, arch) in STATIC_TARGETS and facts.get("static_ok") != "1":
                raise RuntimeError(
                    f"{job} is a static target and the static half did not certify, so this "
                    f"archive would have published shared-only and green. The build log says "
                    f"why; the archive is at {path} for inspection."
                )

            verify_archive(path, log_file, job)
            log_file.write(f"\n# finished: {time.strftime('%Y-%m-%d %H:%M:%S %z')} (success)\n")
            return path
        except BaseException as exc:
            log_file.write(
                f"\n# finished: {time.strftime('%Y-%m-%d %H:%M:%S %z')} "
                f"(FAILED: {type(exc).__name__}: {exc})\n"
            )
            print(f"\n[{job}] build failed, full log: {log_path}", file=sys.stderr, flush=True)
            raise


def _build_docker(version, plat, arch, output_dir, log_file, job):
    """Build inside a container pinned to the target architecture."""
    rid = rid_for(plat, arch)
    info = TARGETS[rid]
    image_tag = builder_image_tag(version, plat, arch)
    container_name = f"acadsharp-extract-{version}-{plat}-{arch}".lower()
    dir_name = staging_dir_name(plat, arch)
    output_path = os.path.join(output_dir, archive_name(plat, arch))
    builder_image = MUSL_BUILD_IMAGE if plat == "musl" else LINUX_BUILD_IMAGE

    print(
        f"\n{'=' * 60}\n  Building ACadSharp {version} for {plat}/{info['cpu']} ({rid})"
        f"\n{'=' * 60}\n",
        flush=True,
    )

    with tempfile.TemporaryDirectory() as ctx:
        with open(os.path.join(ctx, "Dockerfile"), "w") as f:
            f.write(make_dockerfile(version, plat, arch))
        _write_build_context(ctx, plat)
        run_checked(
            [
                "docker",
                "build",
                f"--platform={info['docker_platform']}",
                "--progress=plain",
                "-t",
                image_tag,
                ctx,
            ],
            log_file,
            job,
        )

    with tempfile.TemporaryDirectory() as extract_dir:
        staging_dest = os.path.join(extract_dir, dir_name)
        facts_path = os.path.join(extract_dir, "facts.txt")
        try:
            run_checked(
                [
                    "docker",
                    "create",
                    f"--platform={info['docker_platform']}",
                    "--name",
                    container_name,
                    image_tag,
                ],
                log_file,
                job,
            )
            run_checked(["docker", "cp", f"{container_name}:/staging", staging_dest], log_file, job)
            run_checked(
                ["docker", "cp", f"{container_name}:/work/facts.txt", facts_path], log_file, job
            )
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

        facts = read_facts(facts_path)
        finish_archive(
            staging_dest, plat, arch, facts, builder_image=builder_image, version=version
        )
        run_checked(["tar", "czf", output_path, "-C", extract_dir, dir_name], log_file, job)

    size_kb = os.path.getsize(output_path) / 1024
    print(f"[{job}] -> {output_path} ({size_kb:.0f} KB)", flush=True)
    return output_path, facts


def _build_mac_native(version, arch, output_dir, log_file, job):
    """Build on the macOS host with the SDK, with no container involved.

    NativeAOT does not support cross-OS compilation, so there is no way
    to produce this from a Linux container and no way to test it from
    one either. `macos-15` is the runner, which is what release.yml
    already uses for pdfium.
    """
    if sys.platform != "darwin":
        raise RuntimeError(
            "the mac cell needs a macOS host: NativeAOT cannot cross-compile to macOS "
            "and there is no macOS container image. Run this job on macos-15."
        )

    dir_name = staging_dir_name("mac", arch)
    output_path = os.path.join(output_dir, archive_name("mac", arch))
    workspace = os.path.join(output_dir, f"workspace-mac-{arch}")
    staging = os.path.join(workspace, dir_name)
    rid = rid_for("mac", arch)

    if os.path.isdir(workspace):
        shutil.rmtree(workspace)
    os.makedirs(workspace)

    require_dotnet()
    src = fetch_source(split_version(version)[0], os.path.join(workspace, "src"))
    work = os.path.join(workspace, "work")
    os.makedirs(work, exist_ok=True)
    _write_build_context(work, "mac")

    publish_log = os.path.join(workspace, "publish-shared.log")
    with open(publish_log, "w") as log:
        rc = subprocess.run(
            publish_command(rid, project=os.path.join(work, "native", os.path.basename(PROJECT)))
            + [f"-p:AcadSharpProject={os.path.join(src, 'src', 'ACadSharp', 'ACadSharp.csproj')}"],
            cwd=os.path.join(work, "native"),
            stdout=log,
            stderr=subprocess.STDOUT,
        ).returncode
    if rc != 0:
        raise RuntimeError(f"dotnet publish failed for {rid}; see {publish_log}")

    env = dict(os.environ, WANT_STATIC="0", WORK=work, STAGING=staging)
    run_checked(
        ["sh", os.path.join(work, "stage.sh"), rid, "mac", src, abi_fingerprint(), publish_log],
        log_file,
        job,
        env=env,
        cwd=work,
    )
    facts = read_facts(os.path.join(work, "facts.txt"))
    finish_archive(
        staging, "mac", arch, facts, builder_image=f"macos {platform.mac_ver()[0]}", version=version
    )

    tar_env = dict(os.environ, COPYFILE_DISABLE="1")
    run_checked(["tar", "czf", output_path, "-C", workspace, dir_name], log_file, job, env=tar_env)
    return output_path, facts


# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------


def _install_hint(tool):
    is_mac = sys.platform == "darwin"
    hints = {
        "docker": {
            "mac": "Install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/",
            "linux": "Install Docker Engine: https://docs.docker.com/engine/install/",
        },
        "dotnet": {
            "mac": f"Install the .NET {DOTNET_SDK_VERSION} SDK: https://dot.net/download",
            "linux": f"Install the .NET {DOTNET_SDK_VERSION} SDK: https://dot.net/download",
        },
        "gh": {
            "mac": "Install GitHub CLI: `brew install gh` (or see https://cli.github.com/).",
            "linux": "Install GitHub CLI: https://cli.github.com/",
        },
        "git": {
            "mac": "Install git: `brew install git` (or run `xcode-select --install`).",
            "linux": "Install git: `sudo apt install git` or the distro equivalent.",
        },
    }
    return hints[tool]["mac" if is_mac else "linux"]


def check_dependencies(upload, platforms):
    """Verify the tools each requested platform needs are present."""
    errors = []
    plats = list(platforms)
    needs_docker = any(p != "mac" for p in plats) if plats else True
    needs_native = "mac" in plats

    if needs_docker:
        if not shutil.which("docker"):
            errors.append(f"docker not found. {_install_hint('docker')}")
        elif subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            errors.append("Docker daemon is not running.")

    if needs_native:
        if sys.platform != "darwin":
            errors.append(
                "the mac cell needs a macOS host: NativeAOT cannot cross-compile to "
                "macOS and there is no macOS container image. Run it on macos-15."
            )
        elif not shutil.which("dotnet"):
            errors.append(
                f"dotnet not found (required for the mac cell). {_install_hint('dotnet')}"
            )

    if upload:
        if not shutil.which("gh"):
            errors.append(f"gh CLI not found (required for --upload). {_install_hint('gh')}")
        elif subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
            errors.append("gh CLI is not authenticated with GitHub. Run `gh auth login`.")
        if not shutil.which("git"):
            errors.append(f"git not found (required for --upload). {_install_hint('git')}")

    if errors:
        print("Missing or misconfigured dependencies:\n")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Release
# ---------------------------------------------------------------------------


def release_notes(version):
    """Body of the ``acadsharp-<version>`` release."""
    upstream, shim = split_version(version)
    return (
        f"ACadSharp {upstream} behind the VIPRS CAD C ABI, built with .NET NativeAOT "
        f"(shim revision {shim}). No .NET runtime is needed to use these.\n\n"
        "Each archive contains:\n"
        "- `lib/libacadsharp_native.so` (`.dylib` on mac), the shared library\n"
        "- `lib/libacadsharp_native.a` and `lib/libacadsharp_native_init.a`, the "
        "static archives, where the static smoke certified them\n"
        "- `include/viprs_acadsharp.h`, the frozen C ABI\n"
        "- `docs/ABI.md`, `docs/WIRE.md` and `docs/LINKINFO.md`, the three frozen "
        "contracts: what the calls mean, what the batch stream holds, and how to link "
        "this\n"
        "- `metadata/LINKINFO.json`: Rust triple, ABI fields and the measured link facts\n"
        "- `metadata/BUILDINFO.json`: what produced the binaries\n"
        "- `metadata/CHECKSUMS.txt`: sha256 of every other file\n"
        "- `LICENSES/`: ACadSharp's MIT licence and the third-party notices\n\n"
        f"Source: {source_url(upstream)}\n"
        f"sha256: `{source_sha256(upstream)}`\n"
        f"Upstream commit: `{source_commit(upstream)}`\n"
        f".NET SDK: `{DOTNET_SDK_VERSION}`"
    )


def upload_release(version, built_files):
    """Create or update the acadsharp-<version> Release with the built assets."""
    tag = release_tag(version)
    exists = (
        subprocess.run(
            ["gh", "release", "view", tag, "-R", GITHUB_REPO], capture_output=True
        ).returncode
        == 0
    )
    if not exists:
        print(f"Release '{tag}' doesn't exist, creating...", flush=True)
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag,
                "-R",
                GITHUB_REPO,
                "--title",
                f"ACadSharp {version}",
                "--notes",
                release_notes(version),
            ],
            check=True,
        )
    else:
        print(f"Release '{tag}' exists, appending/replacing assets...", flush=True)

    subprocess.run(
        ["gh", "release", "upload", tag, "-R", GITHUB_REPO, "--clobber", *built_files],
        check=True,
    )
    print(f"\nRelease: https://github.com/{GITHUB_REPO}/releases/tag/{tag}", flush=True)


def _print_summary(version, built_files, failures, output_dir, uploaded):
    width = min(shutil.get_terminal_size().columns if sys.stdout.isatty() else 72, 72)
    bar = "=" * width
    print()
    print(bar)
    if uploaded:
        tag = release_tag(version)
        print(f"  {'Published to' if not failures else 'Partial publish to'} {tag}")
        print(f"  https://github.com/{GITHUB_REPO}/releases/tag/{tag}")
    elif built_files:
        print(f"  Build complete{'' if not failures else ' (with failures)'}")
        print(f"  Archives in: {output_dir}/")
    else:
        print("  No archives built.")

    for path in built_files:
        size_kb = os.path.getsize(path) / 1024
        verb = "published" if uploaded else "built"
        print(f"  ok  {os.path.basename(path)}  ({size_kb:.0f} KB)  [{verb}]")

    if failures:
        print()
        for job_id, exc in failures:
            print(f"  FAILED  {job_id}  ({exc})")
        print()
        print(f"  Logs: {os.path.join(output_dir, 'logs')}/")
    print(bar)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--arch",
        choices=["amd64", "x86_64", "x64", "arm64", "aarch64"],
        metavar="ARCH",
        help="build for a single cpu (default: both). x86_64/x64 alias amd64.",
    )
    parser.add_argument(
        "--platform",
        choices=PLATFORMS,
        nargs="+",
        default=None,
        help=(
            "target platform(s). Default matrix: linux/amd64, linux/arm64, musl/amd64, "
            "musl/arm64. `mac` needs a macOS host and is excluded from the default."
        ),
    )
    parser.add_argument(
        "--target",
        action="append",
        choices=sorted(TARGETS),
        help="a .NET runtime identifier to build, repeatable. An alias for the cell.",
    )
    parser.add_argument(
        "--version",
        default=None,
        metavar="VERSION",
        help=(
            "artifact version to build, e.g. 3.7.1-viprs.1. Defaults to the contents "
            "of acadsharp/VERSION. The release workflow passes its dispatch override "
            "here, so the archives, the tag and LINKINFO.json agree on one number."
        ),
    )
    parser.add_argument("--parallel", action="store_true", help="fan out every cell at once")
    parser.add_argument("--plan", action="store_true", help="print the commands and stop")
    parser.add_argument("--upload", action="store_true", help="verify, then publish to Releases")
    parser.add_argument("--output-dir", default="./bin", help="output directory (default: ./bin)")
    args = parser.parse_args(argv)

    try:
        args.arch = normalize_arch(args.arch)
    except ValueError as exc:
        parser.error(str(exc))

    version = args.version or read_version()
    try:
        upstream, shim = split_version(version)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        source_sha256(upstream)
        source_commit(upstream)
    except KeyError as exc:
        parser.error(str(exc))

    if args.target:
        jobs = [(TARGETS[rid]["platform"], TARGETS[rid]["arch"]) for rid in args.target]
    else:
        jobs = resolve_jobs(args.platform, args.arch)

    print(
        f"ACadSharp {upstream}, shim revision {shim}, from {source_url(upstream)}\n"
        f"ABI version {header_versions()[0]}, fingerprint {abi_fingerprint()}",
        flush=True,
    )

    if args.plan:
        for plat, arch in jobs:
            rid = rid_for(plat, arch)
            print(
                f"{plat}/{arch}  rid={rid}  triple={rust_triple(plat, arch)}  "
                f"-> {archive_name(plat, arch)}"
            )
            print("  " + " ".join(publish_command(rid)))
            if rid in STATIC_TARGETS:
                print("  " + " ".join(publish_command(rid, static=True)))
        return 0

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)
    check_dependencies(upload=args.upload, platforms={plat for plat, _ in jobs})

    host_arch = ARCH_ALIASES.get(platform.machine(), platform.machine())
    foreign = [f"{p}/{a}" for p, a in jobs if p != "mac" and a != host_arch]
    if foreign and sys.platform != "darwin":
        print(
            f"Host CPU arch is '{platform.machine()}', so {', '.join(foreign)} needs a "
            "runner of that architecture. .NET does not support QEMU and the SDK ships "
            "no cross toolchain; ADR 0001 measured both.",
            file=sys.stderr,
            flush=True,
        )

    built_files = []
    failures = []
    try:
        if args.parallel and len(jobs) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                futures = {
                    pool.submit(build_for_job, version, plat, arch, output_dir): (plat, arch)
                    for plat, arch in jobs
                }
                for future in concurrent.futures.as_completed(futures):
                    plat, arch = futures[future]
                    try:
                        built_files.append(future.result())
                    except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
                        failures.append((f"{plat}/{arch}", exc))
        else:
            for plat, arch in jobs:
                try:
                    built_files.append(build_for_job(version, plat, arch, output_dir))
                except (RuntimeError, subprocess.CalledProcessError, OSError) as exc:
                    failures.append((f"{plat}/{arch}", exc))

        built_files = sorted(p for p in built_files if p)

        if args.upload and built_files:
            upload_release(version, built_files)
        elif args.upload:
            print("\nNo archives built, so nothing to upload.", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    _print_summary(version, built_files, failures, output_dir, args.upload and bool(built_files))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
