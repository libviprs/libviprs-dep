#!/usr/bin/env python3
"""Build qpdf (library and command line tool) for Linux and macOS.

qpdf is shipped here as a TEST-TIME validator: libviprs uses
`qpdf --check` to confirm the PDFs it writes are well formed. Nothing in
the libviprs crate links it. Each archive carries the `qpdf` binary and
both a shared and a static libqpdf, laid out like the zstd archives.

The build follows zstd/build_zstd.py: each linux (platform, arch) combo builds
in a container *of that architecture* (arm64 runs under the daemon's QEMU
emulation), so nothing is cross-compiled and the smoke test executes the real
binary. macOS has no container image, so the mac slice builds natively on a
macOS host (the release workflow uses a macos-15 runner).

qpdf is Apache-2.0 since v11. Crypto is qpdf's own native provider, so no
OpenSSL is needed. libjpeg is linked statically; zlib stays dynamic (it is on
every Linux and macOS host) and is recorded in BUILDINFO.

Requirements:
    - Docker with buildx support (linux targets)
    - CMake, a C++20 compiler and libjpeg-turbo (mac target, built natively)
    - gh CLI (only when using --upload)

Usage:
    python3 qpdf/build_qpdf.py                        # linux amd64 + arm64, version in qpdf/VERSION
    python3 qpdf/build_qpdf.py 12.4.2                 # explicit version
    python3 qpdf/build_qpdf.py --arch arm64           # arm64 only
    python3 qpdf/build_qpdf.py --platform mac         # native macOS arm64 build
    python3 qpdf/build_qpdf.py --parallel             # fan out every combo at once
    python3 qpdf/build_qpdf.py --upload               # verify, then publish to GitHub Releases
"""

import argparse
import concurrent.futures
import hashlib
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

GITHUB_REPO = "libviprs/libviprs-dep"

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
VERSION_FILE = os.path.join(HERE, "VERSION")
VERIFY_ARCHIVE_SCRIPT = os.path.join(HERE, "scripts", "verify_archive.sh")
FIXTURES_DIR = os.path.join(HERE, "tests", "fixtures")

# linux builds in Docker, mac builds natively. There is no musl cell: nobody
# runs the validator on Alpine, and zstd's musl archives are for runtime links.
PLATFORMS = ["linux", "mac"]

TARGETS = {
    "amd64": {"cpu": "x64", "docker_platform": "linux/amd64", "apple_arch": "x86_64"},
    "arm64": {"cpu": "arm64", "docker_platform": "linux/arm64", "apple_arch": "arm64"},
}

ARCH_ALIASES = {
    "x86_64": "amd64",
    "x64": "amd64",
    "aarch64": "arm64",
}

# Same Debian bookworm base zstd and pdfium use, so glibc 2.36 is the oldest
# runtime the linux archives support. It ships GCC 12 and CMake 3.25, enough
# for qpdf's C++20.
BASE_IMAGES = {"linux": "debian:bookworm-slim"}

DEFAULT_JOBS = [("linux", "amd64"), ("linux", "arm64")]
SUPPORTED_JOBS = DEFAULT_JOBS + [("mac", "arm64")]

SOURCE_URL = "https://github.com/qpdf/qpdf/releases/download/v{version}/qpdf-{version}.tar.gz"

# SHA-256 of the upstream release tarball, pinned per version. Bumping VERSION
# without a matching entry here is a hard error, not an unverified download.
SOURCE_SHA256 = {
    "12.4.2": "8a58af5b6141319287c1883bec8bd1bd545b7567b7fc5e6ce5d25a1c85f36397",
}

MAC_DEPLOYMENT_TARGET = "11.0"


def read_version(path=VERSION_FILE):
    """Return the qpdf version this checkout ships, from `qpdf/VERSION`."""
    with open(path) as f:
        version = f.read().strip()
    if not version:
        raise ValueError(f"{path} is empty: it is the single source of truth for the version")
    return version


def normalize_arch(arch):
    """Return the TARGETS key for a user-supplied arch name, or raise."""
    if arch is None:
        return None
    canonical = ARCH_ALIASES.get(arch, arch)
    if canonical not in TARGETS:
        raise ValueError(f"Unknown arch '{arch}'. Accepted: amd64/x86_64, arm64/aarch64.")
    return canonical


def resolve_jobs(platform_flag, arch_flag):
    """Resolve CLI --platform / --arch flags to a concrete (plat, arch) list.

    No flags: the two linux cells. `--platform mac` means mac/arm64, the only
    mac slice shipped; asking for mac/amd64 is an error rather than a quiet
    skip.
    """
    if platform_flag is None and arch_flag is None:
        return list(DEFAULT_JOBS)

    plats = list(platform_flag) if platform_flag is not None else ["linux"]
    jobs = []
    for plat in plats:
        if plat == "mac" and arch_flag in (None, "arm64"):
            jobs.append(("mac", "arm64"))
        elif plat == "mac":
            raise ValueError("mac is only built for arm64")
        else:
            for arch in [arch_flag] if arch_flag else ["amd64", "arm64"]:
                jobs.append((plat, arch))
    for job in jobs:
        if job not in SUPPORTED_JOBS:
            raise ValueError(f"{job[0]}/{job[1]} is not a supported qpdf target")
    return jobs


def source_url(version):
    """Upstream release tarball URL for a qpdf version."""
    return SOURCE_URL.format(version=version)


def source_sha256(version):
    """Pinned SHA-256 for a qpdf version's release tarball, or raise."""
    try:
        return SOURCE_SHA256[version]
    except KeyError:
        raise ValueError(
            f"No pinned source hash for qpdf {version}. Add its sha256 to "
            f"SOURCE_SHA256 in {os.path.relpath(__file__, REPO_ROOT)}: downloading "
            "an unverified tarball is not something this script will do."
        ) from None


# ---------------------------------------------------------------------------
# Build configuration
# ---------------------------------------------------------------------------


def cmake_configure_args(plat, arch, prefix, jpeg_prefix=None):
    """CMake configure arguments for one (platform, arch).

    Single source of truth: the Dockerfile generator and the native mac path
    both call this, so the two never drift on build flags.
    """
    args = [
        "-S",
        ".",
        "-B",
        "out",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        # GNUInstallDirs would use lib/<triple> on Debian multiarch hosts.
        "-DCMAKE_INSTALL_LIBDIR=lib",
        "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
        "-DBUILD_SHARED_LIBS=ON",
        "-DBUILD_STATIC_LIBS=ON",
        # qpdf's own crypto, so no OpenSSL or GnuTLS ends up in the link.
        "-DUSE_IMPLICIT_CRYPTO=OFF",
        "-DREQUIRE_CRYPTO_NATIVE=ON",
        # libjpeg goes into the binary and the shared library, leaving zlib as
        # the only runtime library besides libc and the C++ runtime.
        "-DSTATIC_JPEG=ON",
        "-DBUILD_DOC=OFF",
        "-DINSTALL_MANUAL=OFF",
        "-DINSTALL_EXAMPLES=OFF",
        # bin/qpdf finds ../lib/libqpdf once the archive is unpacked anywhere.
        "-DCMAKE_INSTALL_RPATH=" + ("@loader_path/../lib" if plat == "mac" else "$ORIGIN/../lib"),
    ]
    if plat == "mac":
        args += [
            f"-DCMAKE_OSX_ARCHITECTURES={TARGETS[arch]['apple_arch']}",
            f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MAC_DEPLOYMENT_TARGET}",
            "-DCMAKE_INSTALL_NAME_DIR=@rpath",
        ]
        if jpeg_prefix:
            args.append(f"-DCMAKE_PREFIX_PATH={jpeg_prefix}")
    return args


def cmake_args_text(plat, arch, prefix, jpeg_prefix=None):
    """The configure arguments as the `cmake-args.txt` shipped in the archive."""
    return "\n".join(cmake_configure_args(plat, arch, prefix, jpeg_prefix)) + "\n"


# ---------------------------------------------------------------------------
# Shell helpers shared by the Dockerfile and the native mac path
# ---------------------------------------------------------------------------

# Executes the staged build: links a tiny C program against the shared and the
# static library, then runs bin/qpdf with no library path set (so the rpath is
# what is being tested), checks the version, and checks that --check passes a
# good PDF and fails a corrupted one.
SMOKE_TEST_SH = r"""#!/bin/sh
# Usage: smoke.sh <staging-dir> <expected-version> <fixtures-dir> [cflags] [static-libs] [run|norun]
set -eu

STAGING="${1:?usage: smoke.sh <staging-dir> <expected-version> <fixtures-dir> [cflags] ...}"
EXPECTED="${2:?missing expected version}"
FIX="${3:?missing fixtures dir}"
CFLAGS_EXTRA="${4:-}"
STATIC_LIBS="${5:--ljpeg -lz}"
RUN_MODE="${6:-run}"

for f in "$STAGING/bin/qpdf" "$STAGING/lib/libqpdf.a" "$STAGING/include/qpdf/qpdf-c.h" \
    "$FIX/good.pdf" "$FIX/corrupt.pdf"; do
    [ -e "$f" ] || { echo "smoke: missing $f" >&2; exit 1; }
done

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/smoke.c" <<'SMOKE_C'
#include <stdio.h>
#include <string.h>
#include <qpdf/qpdf-c.h>

/* Checks the version string of the library we just built, so a stale
   checkout cannot be packaged under the wrong version number. */
int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "smoke: expected version argument\n");
        return 2;
    }
    if (strcmp(qpdf_get_qpdf_version(), argv[1]) != 0) {
        fprintf(stderr, "smoke: linked qpdf is %s, expected %s\n",
                qpdf_get_qpdf_version(), argv[1]);
        return 1;
    }
    printf("smoke: libqpdf %s\n", qpdf_get_qpdf_version());
    return 0;
}
SMOKE_C

# shellcheck disable=SC2086  # CFLAGS_EXTRA and STATIC_LIBS are deliberate word-split lists
cc $CFLAGS_EXTRA -c -I"$STAGING/include" "$WORK/smoke.c" -o "$WORK/smoke.o"
# shellcheck disable=SC2086
c++ $CFLAGS_EXTRA "$WORK/smoke.o" "$STAGING/lib/libqpdf.a" $STATIC_LIBS -o "$WORK/smoke-static"
echo "smoke: linked against libqpdf.a"
# shellcheck disable=SC2086
c++ $CFLAGS_EXTRA "$WORK/smoke.o" -L"$STAGING/lib" -Wl,-rpath,"$STAGING/lib" -lqpdf \
    -o "$WORK/smoke-shared"
echo "smoke: linked against the shared libqpdf"

if [ "$RUN_MODE" != "run" ]; then
    echo "smoke: foreign architecture, link-only (no execution)"
    exit 0
fi

"$WORK/smoke-static" "$EXPECTED"
"$WORK/smoke-shared" "$EXPECTED"

# No library path anywhere: the install rpath has to be enough.
first=$(env -u LD_LIBRARY_PATH -u DYLD_LIBRARY_PATH "$STAGING/bin/qpdf" --version | head -n 1)
[ "$first" = "qpdf version $EXPECTED" ] || { echo "smoke: bin/qpdf says '$first'" >&2; exit 1; }
echo "smoke: $first"

QPDF="env -u LD_LIBRARY_PATH -u DYLD_LIBRARY_PATH $STAGING/bin/qpdf"
out=$($QPDF --check "$FIX/good.pdf" 2>&1) \
    || { echo "smoke: --check rejected the good PDF: $out" >&2; exit 1; }
case "$out" in
    *"No syntax or stream encoding errors found"*) ;;
    *) echo "smoke: --check on the good PDF was not clean: $out" >&2; exit 1 ;;
esac
if $QPDF --check "$FIX/corrupt.pdf" >/dev/null 2>&1; then
    echo "smoke: --check accepted the corrupted PDF" >&2
    exit 1
fi
echo "smoke: --check passes the good PDF and fails the corrupted one"
"""

# CMake bakes the absolute install prefix into libqpdf.pc, which means nothing
# on the consumer's machine; ${pcfiledir} is pkg-config's "directory this .pc
# lives in", so the archive works from wherever it is unpacked.
RELOCATE_PKGCONFIG_SH = r"""#!/bin/sh
# Usage: relocate-pc.sh <staging-dir>
set -eu

STAGING="${1:?usage: relocate-pc.sh <staging-dir>}"
PC="$STAGING/lib/pkgconfig/libqpdf.pc"

[ -f "$PC" ] || { echo "relocate: $PC not found" >&2; exit 1; }

sed 's|^prefix=.*|prefix=${pcfiledir}/../..|' "$PC" > "$PC.tmp"
mv "$PC.tmp" "$PC"

grep -q '^prefix=${pcfiledir}/\.\./\.\.$' "$PC" || {
    echo "relocate: prefix rewrite did not take in $PC" >&2
    exit 1
}
echo "relocate: libqpdf.pc prefix is now relative to its own directory"
"""

# Writes metadata/BUILDINFO.txt and then metadata/CHECKSUMS.txt, the sha256 of
# every other regular file in the archive. Must run last, after every file is
# in place, because the verifier compares the list against the archive.
FINALIZE_SH = r"""#!/bin/sh
# Usage: finalize.sh <staging-dir> <platform> <cpu> <version> <source-sha256> <crypto> <jpeg>
set -eu

STAGING="${1:?staging dir}"
mkdir -p "$STAGING/metadata"
cat > "$STAGING/metadata/BUILDINFO.txt" <<INFO
qpdf_version=${4:?version}
platform=${2:?platform}
cpu=${3:?cpu}
source_sha256=${5:?source sha256}
crypto=${6:-native}
jpeg=${7:-static}
runtime_libraries=libc libstdc++ libz
INFO

if command -v sha256sum >/dev/null 2>&1; then SHA="sha256sum"; else SHA="shasum -a 256"; fi
cd "$STAGING"
find . -type f ! -path ./metadata/CHECKSUMS.txt | sed 's|^\./||' | LC_ALL=C sort |
    while read -r f; do
    # shellcheck disable=SC2086
    $SHA "$f"
done > metadata/CHECKSUMS.txt
echo "finalize: $(wc -l < metadata/CHECKSUMS.txt | tr -d ' ') files checksummed"
"""

HELPER_SCRIPTS = (
    ("smoke.sh", SMOKE_TEST_SH),
    ("relocate-pc.sh", RELOCATE_PKGCONFIG_SH),
    ("finalize.sh", FINALIZE_SH),
)


# ---------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------


def make_dockerfile(version, arch, plat):
    """Generate the Dockerfile that builds and stages qpdf for one linux combo.

    The container is pinned to the *target* architecture, so nothing is
    cross-compiled and the smoke test runs the real artifacts.
    """
    if plat not in BASE_IMAGES:
        raise ValueError(f"platform '{plat}' has no Docker base image (mac builds natively)")

    docker_platform = TARGETS[arch]["docker_platform"]
    base_image = BASE_IMAGES[plat]
    url = source_url(version)
    sha = source_sha256(version)
    cmake_args = " ".join(shlex.quote(a) for a in cmake_configure_args(plat, arch, "/staging"))
    args_text = cmake_args_text(plat, arch, "/staging")
    cpu = TARGETS[arch]["cpu"]

    return f"""\
FROM --platform={docker_platform} {base_image}

# Step 0: toolchain and the two libraries qpdf links: zlib and libjpeg.
RUN apt-get update && apt-get install -y --no-install-recommends \\
    build-essential cmake curl ca-certificates binutils file zlib1g-dev libjpeg-dev \\
    && rm -rf /var/lib/apt/lists/*

# Step 1: fetch the pinned upstream release tarball and check it against the
# hash recorded in build_qpdf.py before a single line is compiled.
RUN curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors \\
        --connect-timeout 30 -o /tmp/qpdf.tar.gz "{url}" \\
    && echo "{sha}  /tmp/qpdf.tar.gz" | sha256sum -c -

# Step 2: unpack
RUN mkdir -p /build && tar xzf /tmp/qpdf.tar.gz -C /build && rm /tmp/qpdf.tar.gz
WORKDIR /build/qpdf-{version}

# Step 3: configure. One pass emits the shared and the static library and the
# command line tool.
RUN cmake {cmake_args}

# Step 4: compile
RUN cmake --build out -j"$(nproc)"

# Step 5: install into the staging directory that becomes the archive
RUN cmake --install out

# Step 6: make libqpdf.pc relocatable
COPY relocate-pc.sh /tmp/relocate-pc.sh
RUN sh /tmp/relocate-pc.sh /staging

# Step 7: record the exact configure flags, and ship upstream's licence and
# notice plus the licence of the statically linked libjpeg
RUN cat > /staging/cmake-args.txt <<'ARGS'
{args_text}ARGS
RUN cp LICENSE.txt /staging/LICENSE && cp NOTICE.md /staging/NOTICE.md \\
    && mkdir -p /staging/LICENSES \\
    && cp /usr/share/doc/libjpeg62-turbo/copyright /staging/LICENSES/libjpeg-turbo.txt

# Step 8: BUILDINFO and CHECKSUMS, last, so they cover every other file
COPY finalize.sh /tmp/finalize.sh
RUN sh /tmp/finalize.sh /staging linux {cpu} {version} {sha} native static

# Step 9: run what was built. The container is the target architecture, so
# the binary executes for real, with no library path set.
COPY fixtures /tmp/fixtures
COPY smoke.sh /tmp/smoke.sh
RUN sh /tmp/smoke.sh /staging {version} /tmp/fixtures

# Step 10: leave a listing in the build log for post-mortems
RUN ls -lR /staging && file /staging/bin/qpdf && (ldd /staging/bin/qpdf || true)
"""


# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------


def _install_hint(tool):
    """Platform-appropriate install instructions for a given tool."""
    is_mac = sys.platform == "darwin"
    hints = {
        "docker": {
            "mac": (
                "Install Docker Desktop: https://docs.docker.com/desktop/install/mac-install/ "
                "(or `brew install --cask docker`)."
            ),
            "linux": (
                "Install Docker Engine: https://docs.docker.com/engine/install/ "
                "(e.g. on Debian/Ubuntu: `curl -fsSL https://get.docker.com | sh` then "
                "`sudo usermod -aG docker $USER` and log out/in)."
            ),
        },
        "cmake": {
            "mac": "Install CMake: `brew install cmake`.",
            "linux": "Install CMake: `sudo apt install cmake` (Debian/Ubuntu) or similar.",
        },
        "cc": {
            "mac": "Install the Xcode command line tools: `xcode-select --install`.",
            "linux": "Install a C compiler: `sudo apt install build-essential`.",
        },
        "jpeg": {
            "mac": "Install libjpeg-turbo: `brew install jpeg-turbo`.",
            "linux": "Install libjpeg: `sudo apt install libjpeg-dev`.",
        },
        "gh": {
            "mac": "Install GitHub CLI: `brew install gh` (or see https://cli.github.com/).",
            "linux": (
                "Install GitHub CLI: https://github.com/cli/cli/blob/trunk/docs/install_linux.md"
            ),
        },
        "git": {
            "mac": "Install git: `brew install git` (or run `xcode-select --install`).",
            "linux": "Install git: `sudo apt install git` (Debian/Ubuntu) or distro equivalent.",
        },
    }
    return hints[tool]["mac" if is_mac else "linux"]


def jpeg_prefix_mac():
    """Homebrew prefix of libjpeg-turbo, or None when it is not installed."""
    if not shutil.which("brew"):
        return None
    result = subprocess.run(["brew", "--prefix", "jpeg-turbo"], capture_output=True, text=True)
    prefix = result.stdout.strip()
    return prefix if result.returncode == 0 and os.path.isdir(prefix) else None


def check_dependencies(upload, platforms):
    """Verify the tools each requested platform needs are present.

    linux needs a working Docker daemon with buildx; mac needs CMake, a C++
    compiler and libjpeg-turbo on the host, since there is no macOS container
    to build in. A mac-only invocation therefore does not require Docker.
    """
    errors = []
    plats = list(platforms)
    needs_docker = any(p != "mac" for p in plats) if plats else True
    needs_native = "mac" in plats

    if needs_docker:
        if not shutil.which("docker"):
            errors.append(f"docker not found. {_install_hint('docker')}")
        elif subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
            errors.append("Docker daemon is not running.")
        elif subprocess.run(["docker", "buildx", "version"], capture_output=True).returncode:
            errors.append(
                "docker buildx not available: it is what runs the foreign-architecture "
                "builds. Install from https://docs.docker.com/build/install-buildx/"
            )

    if needs_native:
        if sys.platform != "darwin":
            errors.append(
                "mac builds need a macOS host: there is no macOS container image. "
                "Run this job on a macos runner."
            )
        for tool in ("cmake", "cc"):
            if not shutil.which(tool):
                errors.append(f"{tool} not found (required for mac builds). {_install_hint(tool)}")
        if sys.platform == "darwin" and jpeg_prefix_mac() is None:
            errors.append(
                f"libjpeg-turbo not found (required for mac builds). {_install_hint('jpeg')}"
            )

    if upload:
        if not shutil.which("gh"):
            errors.append(f"gh CLI not found (required for --upload). {_install_hint('gh')}")
        elif subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
            errors.append("gh CLI is not authenticated with GitHub. Run `gh auth login`.")
        else:
            probe = subprocess.run(
                ["gh", "repo", "view", GITHUB_REPO, "--json", "viewerPermission"],
                capture_output=True,
                text=True,
            )
            if probe.returncode != 0:
                errors.append(f"gh cannot reach {GITHUB_REPO}. Check `gh auth status`.")
            elif (
                '"viewerPermission":"READ"' in probe.stdout
                or '"viewerPermission":null' in probe.stdout
            ):
                errors.append(f"Authenticated GitHub user lacks write access to {GITHUB_REPO}.")
        if not shutil.which("git"):
            errors.append(f"git not found (required for --upload). {_install_hint('git')}")

    if errors:
        print("Missing or misconfigured dependencies:\n")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def archive_name(plat, arch):
    """Archive name: qpdf-{platform}-{cpu}.tgz"""
    return f"qpdf-{plat}-{TARGETS[arch]['cpu']}.tgz"


def staging_dir_name(plat, arch):
    """Top-level directory inside the archive: qpdf-{platform}-{cpu}"""
    return f"qpdf-{plat}-{TARGETS[arch]['cpu']}"


def release_tag(version):
    """Release tag derived from the version: qpdf-12.4.2"""
    return f"qpdf-{version}"


def stream(cmd, log_file, prefix, env=None, cwd=None):
    """Run a command, tee its output to ``log_file`` and to stdout.

    Every line is prefixed with the job id so a ``--parallel`` run stays
    readable when several builds interleave. The log file keeps the full
    unprefixed stream and is the authoritative post-mortem record.
    """
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
    """``stream`` that raises RuntimeError when the command fails."""
    rc = stream(cmd, log_file, prefix, **kwargs)
    if rc != 0:
        raise RuntimeError(f"{cmd[0]} failed with exit {rc}: {' '.join(cmd)}")


def download_source(version, dest, log_file, prefix):
    """Download the pinned release tarball to ``dest`` and check its hash."""
    url = source_url(version)
    expected = source_sha256(version)
    print(f"[{prefix}] downloading {url}", flush=True)
    log_file.write(f"\n# downloading {url}\n")
    with urllib.request.urlopen(url) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)

    digest = hashlib.sha256()
    with open(dest, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    log_file.write(f"# sha256 {actual}\n")
    if actual != expected:
        raise RuntimeError(f"sha256 mismatch for {url}: expected {expected}, got {actual}")
    print(f"[{prefix}] sha256 ok: {actual}", flush=True)


def verify_archive(path, log_file=None, prefix="verify"):
    """Run scripts/verify_archive.sh against a packaged .tgz.

    Called for every archive before it is uploaded, so a malformed
    tarball can never reach a Release — the same ordering the pdfium
    release workflow enforces, moved into the driver so it also holds
    for local ``--upload`` runs.
    """
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


def _write_helper_scripts(directory):
    """Drop the shared shell helpers into a build context / work dir."""
    paths = {}
    for name, text in HELPER_SCRIPTS:
        path = os.path.join(directory, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)
        paths[name] = path
    return paths


def write_checksum_sidecar(path):
    """Write `<archive>.sha256` next to an archive and return its path.

    The archive carries its own per-file checksums; this one is the digest of
    the archive itself, so a consumer can pin the download.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    sidecar = path + ".sha256"
    with open(sidecar, "w") as f:
        f.write(f"{digest.hexdigest()}  {os.path.basename(path)}\n")
    return sidecar


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_for_job(version, plat, arch, output_dir):
    """Build one (platform, arch) and return the path to its archive.

    Writes the full build output to `<output_dir>/logs/<plat>-<arch>.log`
    and prints the log path to stderr on failure.
    """
    job = f"{plat}/{arch}"
    log_dir = os.path.join(output_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{plat}-{arch}.log")

    with open(log_path, "w") as log_file:
        log_file.write(
            f"# qpdf build log\n"
            f"# job:     {job}\n"
            f"# version: {version}\n"
            f"# started: {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n\n"
        )
        log_file.flush()
        try:
            if plat == "mac":
                path = _build_mac_native(version, arch, output_dir, log_file, job)
            else:
                path = _build_docker(version, plat, arch, output_dir, log_file, job)
            verify_archive(path, log_file, job)
            write_checksum_sidecar(path)
            log_file.write(f"\n# finished: {time.strftime('%Y-%m-%d %H:%M:%S %z')} (success)\n")
            return path
        except BaseException as exc:
            log_file.write(
                f"\n# finished: {time.strftime('%Y-%m-%d %H:%M:%S %z')} "
                f"(FAILED: {type(exc).__name__}: {exc})\n"
            )
            print(f"\n[{job}] build failed: full log: {log_path}", file=sys.stderr, flush=True)
            raise


def _build_docker(version, plat, arch, output_dir, log_file, job):
    """Build inside a container pinned to the target architecture."""
    image_tag = f"qpdf-builder-{version}-{plat}-{arch}"
    container_name = f"qpdf-extract-{version}-{plat}-{arch}"
    dir_name = staging_dir_name(plat, arch)
    output_path = os.path.join(output_dir, archive_name(plat, arch))

    print(
        f"\n{'=' * 60}\n  Building qpdf {version} for {plat}/{TARGETS[arch]['cpu']}\n{'=' * 60}\n",
        flush=True,
    )

    with tempfile.TemporaryDirectory() as ctx:
        with open(os.path.join(ctx, "Dockerfile"), "w") as f:
            f.write(make_dockerfile(version, arch, plat))
        _write_helper_scripts(ctx)
        shutil.copytree(FIXTURES_DIR, os.path.join(ctx, "fixtures"))

        run_checked(
            [
                "docker",
                "build",
                f"--platform={TARGETS[arch]['docker_platform']}",
                "--no-cache",
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
        try:
            run_checked(
                [
                    "docker",
                    "create",
                    f"--platform={TARGETS[arch]['docker_platform']}",
                    "--name",
                    container_name,
                    image_tag,
                ],
                log_file,
                job,
            )
            run_checked(["docker", "cp", f"{container_name}:/staging", staging_dest], log_file, job)
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True)

        run_checked(["tar", "czf", output_path, "-C", extract_dir, dir_name], log_file, job)

    size_kb = os.path.getsize(output_path) / 1024
    print(f"[{job}] -> {output_path} ({size_kb:.0f} KB)", flush=True)
    return output_path


def _build_mac_native(version, arch, output_dir, log_file, job):
    """Build on the macOS host with CMake: no container involved."""
    if sys.platform != "darwin":
        raise RuntimeError(
            "mac builds require a macOS host (there is no macOS container image); "
            "run this job on a macos runner."
        )
    jpeg = jpeg_prefix_mac()
    if jpeg is None:
        raise RuntimeError(f"libjpeg-turbo not found. {_install_hint('jpeg')}")

    dir_name = staging_dir_name("mac", arch)
    output_path = os.path.join(output_dir, archive_name("mac", arch))
    workspace = os.path.join(output_dir, f"workspace-mac-{arch}")
    staging = os.path.join(workspace, dir_name)

    print(
        f"\n{'=' * 60}\n  Building qpdf {version} for mac/{TARGETS[arch]['cpu']} (native)"
        f"\n{'=' * 60}\n",
        flush=True,
    )

    if os.path.isdir(workspace):
        shutil.rmtree(workspace)
    os.makedirs(workspace)

    tarball = os.path.join(workspace, f"qpdf-{version}.tar.gz")
    download_source(version, tarball, log_file, job)
    run_checked(["tar", "xzf", tarball, "-C", workspace], log_file, job)
    src = os.path.join(workspace, f"qpdf-{version}")

    run_checked(
        ["cmake"] + cmake_configure_args("mac", arch, staging, jpeg), log_file, job, cwd=src
    )
    run_checked(["cmake", "--build", "out", "-j", str(os.cpu_count() or 4)], log_file, job, cwd=src)
    run_checked(["cmake", "--install", "out"], log_file, job, cwd=src)

    helpers = _write_helper_scripts(workspace)
    run_checked(["sh", helpers["relocate-pc.sh"], staging], log_file, job)

    with open(os.path.join(staging, "cmake-args.txt"), "w") as f:
        f.write(cmake_args_text("mac", arch, staging, jpeg))
    shutil.copy2(os.path.join(src, "LICENSE.txt"), os.path.join(staging, "LICENSE"))
    shutil.copy2(os.path.join(src, "NOTICE.md"), os.path.join(staging, "NOTICE.md"))
    os.makedirs(os.path.join(staging, "LICENSES"))
    for name in ("LICENSE.md", "README.ijg"):
        if os.path.exists(os.path.join(jpeg, name)):
            shutil.copy2(
                os.path.join(jpeg, name), os.path.join(staging, "LICENSES", f"libjpeg-turbo-{name}")
            )
    if not os.listdir(os.path.join(staging, "LICENSES")):
        raise RuntimeError(f"no libjpeg-turbo licence file found under {jpeg}")

    run_checked(
        [
            "sh",
            helpers["finalize.sh"],
            staging,
            "mac",
            TARGETS[arch]["cpu"],
            version,
            source_sha256(version),
            "native",
            "static",
        ],
        log_file,
        job,
    )

    # An x86_64 slice cannot run on an arm64 host without Rosetta, so the run
    # step only happens when the slice matches the machine. Only arm64 is
    # shipped today, so this is the normal path.
    run_mode = "run" if TARGETS[arch]["apple_arch"] == platform.machine() else "norun"
    run_checked(
        [
            "sh",
            helpers["smoke.sh"],
            staging,
            version,
            FIXTURES_DIR,
            f"-arch {TARGETS[arch]['apple_arch']}",
            f"-L{jpeg}/lib -ljpeg -lz",
            run_mode,
        ],
        log_file,
        job,
    )

    env = dict(os.environ, COPYFILE_DISABLE="1")
    run_checked(["tar", "czf", output_path, "-C", workspace, dir_name], log_file, job, env=env)

    size_kb = os.path.getsize(output_path) / 1024
    print(f"[{job}] -> {output_path} ({size_kb:.0f} KB)", flush=True)
    return output_path


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def release_notes(version):
    """Body of the `qpdf-<version>` release.

    Kept here rather than in the workflow so a release cut by CI and one cut
    by a local `--upload` read the same, digest included. Raises `ValueError`
    for a version with no pinned source hash, the same refusal the build makes.
    """
    return (
        f"qpdf {version} built from source, shipped as a test-time PDF validator "
        "(`qpdf --check`). It is not a runtime dependency of libviprs.\n\n"
        "Each archive contains:\n"
        "- `bin/qpdf`: the command line tool, finds ../lib on its own\n"
        "- `lib/libqpdf.so` (`.dylib` on mac) and `lib/libqpdf.a`\n"
        "- `lib/pkgconfig/libqpdf.pc`: relocatable pkg-config file\n"
        "- `include/qpdf/`: public headers\n"
        "- `cmake-args.txt`: the exact CMake configure flags used\n"
        "- `LICENSE` and `NOTICE.md`: qpdf is Apache-2.0 since v11\n"
        "- `LICENSES/`: the licence of the statically linked libjpeg\n"
        "- `metadata/BUILDINFO.txt` and `metadata/CHECKSUMS.txt` (sha256 of every other file)\n\n"
        "Every archive has a `.sha256` sidecar with the digest of the archive itself. "
        "Runtime libraries: libc, libstdc++ and zlib.\n\n"
        f"Source: {source_url(version)}\n"
        f"sha256: `{source_sha256(version)}`"
    )


def upload_release(version, built_files):
    """Create or update the qpdf-<version> Release with the built assets.

    Assets already on the release whose names don't collide are left
    alone, so a partial run can be combined with an earlier one.
    """
    tag = release_tag(version)
    exists = (
        subprocess.run(
            ["gh", "release", "view", tag, "-R", GITHUB_REPO], capture_output=True
        ).returncode
        == 0
    )

    if not exists:
        print(f"Release '{tag}' doesn't exist, creating...", flush=True)
        notes = release_notes(version)
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag,
                "-R",
                GITHUB_REPO,
                "--title",
                f"qpdf {version}",
                "--notes",
                notes,
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


# ---------------------------------------------------------------------------
# Summary banner
# ---------------------------------------------------------------------------


def _print_summary(version, built_files, failures, output_dir, uploaded):
    """Print a final success / partial / failure banner."""
    width = min(shutil.get_terminal_size().columns if sys.stdout.isatty() else 72, 72)
    bar = "=" * width
    print()
    print(bar)
    if uploaded:
        tag = release_tag(version)
        header = f"Published to {tag}" if not failures else f"Partial publish to {tag}"
        print(f"  {header}")
        print(f"  https://github.com/{GITHUB_REPO}/releases/tag/{tag}")
    elif built_files:
        print(f"  Build complete{'' if not failures else ' (with failures)'}")
        print(f"  Archives in: {output_dir}/")
    else:
        print("  No archives built.")

    if built_files:
        print()
        verb = "published" if uploaded else "built"
        for path in built_files:
            size_kb = os.path.getsize(path) / 1024
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


def main():
    parser = argparse.ArgumentParser(
        description="Build qpdf (library and CLI) as a test-time PDF validator",
    )
    parser.add_argument(
        "version",
        nargs="?",
        default=None,
        help="qpdf version to build (e.g. 12.4.2). Defaults to the contents of qpdf/VERSION.",
    )
    parser.add_argument(
        "--arch",
        choices=["amd64", "x86_64", "x64", "arm64", "aarch64"],
        metavar="ARCH",
        help=(
            "build for a single architecture (default: both linux arches). "
            "`x86_64`/`x64` are aliases for `amd64`, `aarch64` for `arm64`."
        ),
    )
    parser.add_argument(
        "--platform",
        choices=PLATFORMS,
        nargs="+",
        default=None,
        help=(
            "target platform(s). Default matrix: linux/amd64 and linux/arm64. `mac` "
            "builds mac/arm64 and needs a macOS host."
        ),
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="fan out every (platform, arch) combo at once (default: sequential)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="verify every archive, then publish them to GitHub Releases",
    )
    parser.add_argument(
        "--output-dir",
        default="./bin",
        help="output directory (default: ./bin)",
    )
    args = parser.parse_args()

    try:
        args.arch = normalize_arch(args.arch)
        jobs = resolve_jobs(args.platform, args.arch)
    except ValueError as exc:
        parser.error(str(exc))

    version = args.version or read_version()
    try:
        source_sha256(version)
    except ValueError as exc:
        parser.error(str(exc))

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    check_dependencies(upload=args.upload, platforms={plat for plat, _ in jobs})

    host_arch = ARCH_ALIASES.get(platform.machine(), platform.machine())
    foreign = [f"{p}/{a}" for p, a in jobs if p != "mac" and a != host_arch]
    if foreign:
        print(
            f"Host CPU arch is '{platform.machine()}'; {', '.join(foreign)} will build in an "
            "emulated container (QEMU). Expect that cell to take noticeably longer.",
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

        built_files.sort()

        if args.upload and built_files:
            upload_release(version, built_files + [f + ".sha256" for f in built_files])
        elif args.upload:
            print("\nNo archives built, skipping upload.", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)

    _print_summary(version, built_files, failures, output_dir, args.upload and bool(built_files))

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
