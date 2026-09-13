#!/usr/bin/env python3
"""Build the ACadSharp NativeAOT shim.

    python3 acadsharp/build_acadsharp.py --plan            # print what it would run
    python3 acadsharp/build_acadsharp.py --target linux-arm64
    python3 acadsharp/build_acadsharp.py --target linux-x64 --static

The shim is `acadsharp/native/Viprs.ACadSharp.Native.csproj`, a NativeAOT
shared library with a handful of `[UnmanagedCallersOnly]` exports over a
vendored ACadSharp checkout. Everything the build depends on is pinned
here: the upstream tarball and its sha256, and the CSUtilities commit the
tarball does not contain.

That second pin is not optional. `src/CSUtilities` is a git submodule,
GitHub-generated source tarballs never carry submodules, and
`ACadSharp.csproj` imports `..\\CSUtilities\\CSMath\\CSMath.projitems`
during project evaluation, so a tarball-only tree fails before restore
with a missing-import error that says nothing about submodules.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
VERSION_FILE = os.path.join(HERE, "VERSION")
NATIVE_DIR = os.path.join(HERE, "native")
PROJECT = os.path.join(NATIVE_DIR, "Viprs.ACadSharp.Native.csproj")

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

# The submodule the tarball leaves empty.
CSUTILITIES_URL = "https://github.com/DomCR/CSUtilities.git"
CSUTILITIES_COMMIT = "fdf1403ede6e3376a0baa1deac166cbe4d21262e"

# Base image for Linux builds. Not the stock .NET SDK image: that one is
# Ubuntu noble, and a library published there needs GLIBC_2.38, which will not
# load on this org's own floor. zstd and pdfium both build on bookworm, which
# pins glibc 2.36 as the oldest runtime we support, and one consumer links all
# three into the same binary. Built on bookworm the same shim tops out at
# GLIBC_2.32. ADR 0001 has the numbers and what the choice costs.
LINUX_BUILD_IMAGE = "debian:bookworm-slim"

# Shared-library targets. No Microsoft target anywhere: the org ships no
# such artifact for any dependency.
TARGETS = ("linux-x64", "linux-arm64", "osx-arm64")

# Static libraries are attempted on Linux only, and only as a managed
# archive: the consumer still links the runtime archives out of the
# NativeAOT pack. ADR 0001 has the measurement.
STATIC_TARGETS = ("linux-x64",)


class SourceTreeError(RuntimeError):
    """The vendored source is not something that can be built."""


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


def build(rid, static=False, dry_run=False):
    cmd = publish_command(rid, static=static)
    print(" ".join(cmd))
    if dry_run:
        return 0
    require_dotnet()
    return subprocess.run(cmd, cwd=NATIVE_DIR, check=False).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--target",
        action="append",
        choices=sorted(set(TARGETS) | set(STATIC_TARGETS)),
        help="runtime identifier to build, repeatable. Defaults to every shared target.",
    )
    parser.add_argument(
        "--static",
        action="store_true",
        help="publish the managed static archive instead of the shared library",
    )
    parser.add_argument("--plan", action="store_true", help="print the commands and stop")
    args = parser.parse_args(argv)

    targets = args.target or list(STATIC_TARGETS if args.static else TARGETS)
    version = read_version()
    upstream, shim = split_version(version)
    print(f"ACadSharp {upstream}, shim revision {shim}, from {source_url(upstream)}")

    failed = 0
    for rid in targets:
        failed |= build(rid, static=args.static, dry_run=args.plan)
    return failed


if __name__ == "__main__":
    sys.exit(main())
