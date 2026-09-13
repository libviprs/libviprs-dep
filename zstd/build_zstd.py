#!/usr/bin/env python3
"""Build zstd static archives and shared libraries for Linux, musl and macOS.

Every archive published from here ships both ``libzstd.a`` and
``libzstd.so`` (``libzstd.dylib`` on mac) so downstream consumers can
pick either linking strategy, matching what ``pdfium/`` does.

Unlike PDFium, zstd is a small C library with a plain CMake build, so
there is no source checkout to orchestrate, no toolchain to patch and
nothing to cross-compile by hand: each ``(platform, arch)`` combo builds
inside a container *of that architecture* (``--platform=linux/arm64``
runs under the daemon's QEMU emulation), which keeps the Dockerfiles
short and lets the smoke test actually execute the library it just
built. Total build time is a couple of minutes per combo, so the memory
scheduler / ETA machinery that ``build_pdfium.py`` needs has nothing to
do here and is deliberately absent.

Requirements:
    - Docker with buildx support (linux + musl targets)
    - CMake and a C compiler (mac target, which builds natively)
    - gh CLI (only when using --upload)

Usage:
    python3 zstd/build_zstd.py                        # default matrix, version from zstd/VERSION
    python3 zstd/build_zstd.py 1.5.7                  # explicit version
    python3 zstd/build_zstd.py --platform musl        # musl only, both arches
    python3 zstd/build_zstd.py --arch arm64           # arm64 only, both platforms
    python3 zstd/build_zstd.py --platform mac         # native macOS build (needs a mac host)
    python3 zstd/build_zstd.py --parallel             # fan out every combo at once
    python3 zstd/build_zstd.py --upload               # verify, then publish to GitHub Releases
"""

import argparse
import concurrent.futures
import hashlib
import os
import platform
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

# Supported platforms. ``linux`` and ``musl`` build in Docker; ``mac``
# builds natively because there is no macOS container image to run.
PLATFORMS = ["linux", "musl", "mac"]

# Target architectures. Each combo builds in a container pinned to the
# *target* arch, so foreign-arch builds run under QEMU rather than being
# cross-compiled. zstd is small enough that emulation costs minutes, and
# in exchange the staged library can be executed by the smoke test on
# the same machine that built it.
TARGETS = {
    "amd64": {"cpu": "x64", "docker_platform": "linux/amd64", "apple_arch": "x86_64"},
    "arm64": {"cpu": "arm64", "docker_platform": "linux/arm64", "apple_arch": "arm64"},
}

# ``--arch`` aliases, same set ``build_pdfium.py`` accepts.
ARCH_ALIASES = {
    "x86_64": "amd64",
    "x64": "amd64",
    "aarch64": "arm64",
}

# Base image per Docker platform. musl gets Alpine so the library links
# against musl libc; linux gets the same Debian bookworm base pdfium
# uses, which pins glibc 2.36 as the oldest runtime we support.
BASE_IMAGES = {
    "linux": "debian:bookworm-slim",
    "musl": "alpine:3.20",
}

# Default matrix: the four Docker-buildable combos. ``mac`` is excluded
# because it needs a macOS host (no container image exists for it), the
# same reason build_pdfium.py leaves mac out of its default matrix.
DEFAULT_JOBS = [
    ("linux", "amd64"),
    ("linux", "arm64"),
    ("musl", "amd64"),
    ("musl", "arm64"),
]

SOURCE_URL = "https://github.com/facebook/zstd/releases/download/v{version}/zstd-{version}.tar.gz"

# SHA-256 of the upstream release tarball, pinned per version. Bumping
# VERSION without adding the matching hash here is a hard error rather
# than a silently unverified download — tests/test_zstd_version.py fails
# the build if the two ever drift apart.
SOURCE_SHA256 = {
    "1.5.7": "eb33e51f49a15e023950cd7825ca74a4a2b43db8354825ac24fc1b7ee09e6fa3",
}

# Oldest macOS the dylib is allowed to require. 11.0 is the first
# release on Apple Silicon, so this is the floor for a universal-capable
# build and matches what pdfium's mac archives target.
MAC_DEPLOYMENT_TARGET = "11.0"


def read_version(path=VERSION_FILE):
    """Return the zstd version this checkout ships, from ``zstd/VERSION``."""
    with open(path) as f:
        version = f.read().strip()
    if not version:
        raise ValueError(f"{path} is empty — it is the single source of truth for the version")
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

    Mirrors ``build_pdfium.resolve_jobs`` so the two drivers behave
    identically at the CLI:

    - No flags: the default matrix.
    - ``--platform X``: filter the default matrix to X, falling back to
      both arches for a platform the default doesn't cover (i.e. mac).
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
            filtered += [(p, a) for p in sorted(missing) for a in ("amd64", "arm64")]
        return filtered

    return [(p, a) for (p, a) in DEFAULT_JOBS if a == arch_flag]


def source_url(version):
    """Upstream release tarball URL for a zstd version."""
    return SOURCE_URL.format(version=version)


def source_sha256(version):
    """Pinned SHA-256 for a zstd version's release tarball, or raise."""
    try:
        return SOURCE_SHA256[version]
    except KeyError:
        raise ValueError(
            f"No pinned source hash for zstd {version}. Add its sha256 to "
            f"SOURCE_SHA256 in {os.path.relpath(__file__, REPO_ROOT)} — downloading "
            "an unverified tarball is not something this script will do."
        ) from None


# ---------------------------------------------------------------------------
# Build configuration
# ---------------------------------------------------------------------------


def cmake_configure_args(plat, arch, prefix):
    """CMake configure arguments for one (platform, arch).

    Single source of truth: the Dockerfile generator and the native mac
    path both call this, so the two never drift on build flags.
    """
    args = [
        "-S",
        "build/cmake",
        "-B",
        "out",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        # GNUInstallDirs resolves libdir to lib/<triple> on Debian-family
        # multiarch hosts, which would put libzstd.a somewhere the
        # documented archive layout doesn't have it. Pin plain lib/.
        "-DCMAKE_INSTALL_LIBDIR=lib",
        # The static archive gets linked into Rust cdylibs and PIEs, so
        # every object in it has to be position-independent.
        "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
        "-DZSTD_BUILD_STATIC=ON",
        "-DZSTD_BUILD_SHARED=ON",
        # We publish a library, not the zstd CLI; skipping programs and
        # tests cuts the build roughly in half.
        "-DZSTD_BUILD_PROGRAMS=OFF",
        "-DZSTD_BUILD_TESTS=OFF",
        "-DZSTD_BUILD_CONTRIB=OFF",
        # zstd-sys builds its vendored copy without the `legacy` feature,
        # so decoding v0.4-v0.7 frames is not part of what libviprs gets
        # today. Keep the published library on the same footing.
        "-DZSTD_LEGACY_SUPPORT=OFF",
        # Enabled, matching every distro package and zstd's own release
        # binaries. Consumers linking the static archive need -lpthread;
        # the shipped libzstd.pc records that in Libs.private.
        "-DZSTD_MULTITHREAD_SUPPORT=ON",
    ]
    if plat == "mac":
        args += [
            f"-DCMAKE_OSX_ARCHITECTURES={TARGETS[arch]['apple_arch']}",
            f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MAC_DEPLOYMENT_TARGET}",
            # Without this the dylib records an absolute install_name
            # pointing at the build machine's staging directory, and
            # anything that links it fails to load anywhere else.
            "-DCMAKE_INSTALL_NAME_DIR=@rpath",
        ]
    return args


def cmake_args_text(plat, arch, prefix):
    """The configure arguments as the ``cmake-args.txt`` shipped in the archive.

    The zstd analogue of pdfium's ``args.gn``: a consumer debugging a
    link error can see exactly which flags produced the binaries.
    """
    return "\n".join(cmake_configure_args(plat, arch, prefix)) + "\n"


# ---------------------------------------------------------------------------
# Staged-artifact smoke test
# ---------------------------------------------------------------------------

# Compiles a compress/decompress round-trip against the freshly-staged
# libraries and runs it. This is the check only the build host can do:
# verify_archive.sh inspects the packaged tarball's shape, but nothing
# except actually executing the code proves the library works.
#
# Both consumers (the Dockerfile and the native mac path) run this exact
# script with different arguments, so there is one implementation of
# "does the thing we built actually compress".
SMOKE_TEST_SH = r"""#!/bin/sh
# Usage: smoke.sh <staging-dir> <expected-version> <shared-ext> [cflags] [run|norun]
set -eu

STAGING="${1:?usage: smoke.sh <staging-dir> <expected-version> <shared-ext> [cflags] [run]}"
EXPECTED="${2:?missing expected version}"
EXT="${3:-so}"
CFLAGS_EXTRA="${4:-}"
RUN_MODE="${5:-run}"

LIB_A="$STAGING/lib/libzstd.a"
LIB_SO="$STAGING/lib/libzstd.$EXT"

for f in "$LIB_A" "$LIB_SO" "$STAGING/include/zstd.h"; do
    [ -e "$f" ] || { echo "smoke: missing $f" >&2; exit 1; }
done

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

cat > "$WORK/smoke.c" <<'SMOKE_C'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zstd.h>

/* Round-trips a buffer through the library we just built and checks the
   version string, so a stale or mismatched checkout cannot be packaged
   under the wrong version number. */
int main(int argc, char **argv) {
    static char src[64 * 1024];
    char *cbuf, *dbuf;
    size_t bound, csize, dsize;
    unsigned i;

    if (argc < 2) {
        fprintf(stderr, "smoke: expected version argument\n");
        return 2;
    }
    if (strcmp(ZSTD_versionString(), argv[1]) != 0) {
        fprintf(stderr, "smoke: linked zstd is %s, expected %s\n",
                ZSTD_versionString(), argv[1]);
        return 1;
    }

    for (i = 0; i < sizeof(src); i++) {
        src[i] = (char)('a' + (i % 23));
    }

    bound = ZSTD_compressBound(sizeof(src));
    cbuf = (char *)malloc(bound);
    dbuf = (char *)malloc(sizeof(src));
    if (!cbuf || !dbuf) {
        fprintf(stderr, "smoke: out of memory\n");
        return 1;
    }

    csize = ZSTD_compress(cbuf, bound, src, sizeof(src), 3);
    if (ZSTD_isError(csize)) {
        fprintf(stderr, "smoke: compress failed: %s\n", ZSTD_getErrorName(csize));
        return 1;
    }
    if (csize >= sizeof(src)) {
        fprintf(stderr, "smoke: compressed %lu bytes into %lu — no compression happened\n",
                (unsigned long)sizeof(src), (unsigned long)csize);
        return 1;
    }

    dsize = ZSTD_decompress(dbuf, sizeof(src), cbuf, csize);
    if (ZSTD_isError(dsize)) {
        fprintf(stderr, "smoke: decompress failed: %s\n", ZSTD_getErrorName(dsize));
        return 1;
    }
    if (dsize != sizeof(src) || memcmp(dbuf, src, sizeof(src)) != 0) {
        fprintf(stderr, "smoke: round-trip mismatch (%lu bytes back)\n", (unsigned long)dsize);
        return 1;
    }

    printf("smoke: zstd %s round-tripped %lu -> %lu -> %lu bytes\n",
           ZSTD_versionString(), (unsigned long)sizeof(src),
           (unsigned long)csize, (unsigned long)dsize);
    return 0;
}
SMOKE_C

# shellcheck disable=SC2086  # CFLAGS_EXTRA is a deliberate word-split arg list
cc $CFLAGS_EXTRA -I"$STAGING/include" "$WORK/smoke.c" "$LIB_A" -pthread -o "$WORK/smoke-static"
echo "smoke: linked against $LIB_A"

# shellcheck disable=SC2086
cc $CFLAGS_EXTRA -I"$STAGING/include" "$WORK/smoke.c" \
    -L"$STAGING/lib" -lzstd -o "$WORK/smoke-shared"
echo "smoke: linked against $LIB_SO"

if [ "$RUN_MODE" != "run" ]; then
    echo "smoke: cross-built for a foreign architecture, link-only (no execution)"
    exit 0
fi

"$WORK/smoke-static" "$EXPECTED"
DYLD_LIBRARY_PATH="$STAGING/lib" LD_LIBRARY_PATH="$STAGING/lib" "$WORK/smoke-shared" "$EXPECTED"
"""

# Makes the shipped libzstd.pc usable from wherever the archive is
# unpacked. CMake bakes the absolute install prefix into the file, which
# is meaningless on the consumer's machine; ${pcfiledir} is pkg-config's
# own "directory this .pc lives in" variable, so rewriting the prefix
# relative to it turns the archive into something ZSTD_SYS_USE_PKG_CONFIG
# can point at directly.
RELOCATE_PKGCONFIG_SH = r"""#!/bin/sh
# Usage: relocate-pc.sh <staging-dir>
set -eu

STAGING="${1:?usage: relocate-pc.sh <staging-dir>}"
PC="$STAGING/lib/pkgconfig/libzstd.pc"

[ -f "$PC" ] || { echo "relocate: $PC not found" >&2; exit 1; }

sed 's|^prefix=.*|prefix=${pcfiledir}/../..|' "$PC" > "$PC.tmp"
mv "$PC.tmp" "$PC"

grep -q '^prefix=${pcfiledir}/\.\./\.\.$' "$PC" || {
    echo "relocate: prefix rewrite did not take in $PC" >&2
    exit 1
}
echo "relocate: libzstd.pc prefix is now relative to its own directory"
"""


# ---------------------------------------------------------------------------
# Dockerfile
# ---------------------------------------------------------------------------


def make_dockerfile(version, arch, plat):
    """Generate the Dockerfile that builds and stages zstd for one combo.

    The container is pinned to the *target* architecture, so nothing is
    cross-compiled and the smoke test runs the real artifacts.
    """
    if plat not in BASE_IMAGES:
        raise ValueError(f"platform '{plat}' has no Docker base image (mac builds natively)")

    docker_platform = TARGETS[arch]["docker_platform"]
    base_image = BASE_IMAGES[plat]
    url = source_url(version)
    sha = source_sha256(version)
    cmake_args = " ".join(cmake_configure_args(plat, arch, "/staging"))
    args_text = cmake_args_text(plat, arch, "/staging")

    if plat == "musl":
        install_deps = "RUN apk add --no-cache build-base cmake curl ca-certificates binutils file"
    else:
        install_deps = (
            "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
            "    build-essential cmake curl ca-certificates binutils file \\\n"
            "    && rm -rf /var/lib/apt/lists/*"
        )

    return f"""\
FROM --platform={docker_platform} {base_image}

# Step 0: toolchain. zstd needs a C compiler and CMake, nothing else —
# no source checkout tooling, no sysroot, no vendored clang.
{install_deps}

# Step 1: fetch the pinned upstream release tarball and check it against
# the hash recorded in build_zstd.py. A tarball that fails the checksum
# fails the build here, before a single line is compiled.
RUN curl -fsSL --retry 3 --retry-delay 5 --retry-all-errors \\
        --connect-timeout 30 -o /tmp/zstd.tar.gz "{url}" \\
    && echo "{sha}  /tmp/zstd.tar.gz" | sha256sum -c -

# Step 2: unpack
RUN mkdir -p /build && tar xzf /tmp/zstd.tar.gz -C /build && rm /tmp/zstd.tar.gz
WORKDIR /build/zstd-{version}

# Step 3: configure. Both ZSTD_BUILD_STATIC and ZSTD_BUILD_SHARED are on,
# so one configure/build pass emits libzstd.a and libzstd.so together —
# no second pass or BUILD file rewrite needed.
RUN cmake {cmake_args}

# Step 4: compile
RUN cmake --build out -j"$(nproc)"

# Step 5: install into the staging directory that becomes the archive
RUN cmake --install out

# Step 6: make libzstd.pc relocatable
COPY relocate-pc.sh /tmp/relocate-pc.sh
RUN sh /tmp/relocate-pc.sh /staging

# Step 7: record the exact configure flags next to the binaries, and ship
# upstream's own licence with them
RUN cat > /staging/cmake-args.txt <<'ARGS'
{args_text}ARGS
RUN cp LICENSE /staging/LICENSE

# Step 8: prove the staged libraries work. The container runs on the
# target architecture, so both the static and the shared link are
# executed, not merely linked.
COPY smoke.sh /tmp/smoke.sh
RUN sh /tmp/smoke.sh /staging {version} so

# Step 9: leave a listing in the build log for post-mortems
RUN ls -lR /staging && file /staging/lib/libzstd.a /staging/lib/libzstd.so.{version}
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
            "linux": (
                "Install CMake: `sudo apt install cmake` (Debian/Ubuntu) or distro equivalent."
            ),
        },
        "cc": {
            "mac": "Install the Xcode command line tools: `xcode-select --install`.",
            "linux": "Install a C compiler: `sudo apt install build-essential`.",
        },
        "gh": {
            "mac": "Install GitHub CLI: `brew install gh` (or see https://cli.github.com/).",
            "linux": (
                "Install GitHub CLI: https://github.com/cli/cli/blob/trunk/docs/install_linux.md "
                "(e.g. on Debian/Ubuntu: `sudo apt install gh` after adding the gh apt repo)."
            ),
        },
        "git": {
            "mac": "Install git: `brew install git` (or run `xcode-select --install`).",
            "linux": "Install git: `sudo apt install git` (Debian/Ubuntu) or distro equivalent.",
        },
    }
    return hints[tool]["mac" if is_mac else "linux"]


def check_dependencies(upload, platforms):
    """Verify the tools each requested platform needs are present.

    linux/musl need a working Docker daemon with buildx; mac needs CMake
    and a C compiler on the host, since there is no macOS container to
    build in. A mac-only invocation therefore does not require Docker at
    all, which matters because GitHub's macos runners don't ship it.
    """
    errors = []
    plats = list(platforms)
    needs_docker = any(p != "mac" for p in plats) if plats else True
    needs_native = "mac" in plats

    if needs_docker:
        if not shutil.which("docker"):
            errors.append(f"docker not found. {_install_hint('docker')}")
        else:
            if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
                errors.append(
                    "Docker daemon is not running. "
                    + (
                        "Start Docker Desktop from the menu bar."
                        if sys.platform == "darwin"
                        else "Start it with `sudo systemctl start docker` (or add yourself to "
                        "the `docker` group to run without sudo)."
                    )
                )
            elif subprocess.run(["docker", "buildx", "version"], capture_output=True).returncode:
                errors.append(
                    "docker buildx not available — it is what runs the foreign-architecture "
                    "builds. Install from https://docs.docker.com/build/install-buildx/"
                )

    if needs_native:
        if sys.platform != "darwin":
            errors.append(
                "mac builds need a macOS host — there is no macOS container image. "
                "Run this job on a macos runner."
            )
        for tool in ("cmake", "cc"):
            if not shutil.which(tool):
                errors.append(f"{tool} not found (required for mac builds). {_install_hint(tool)}")

    if upload:
        if not shutil.which("gh"):
            errors.append(f"gh CLI not found (required for --upload). {_install_hint('gh')}")
        else:
            if subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
                errors.append(
                    "gh CLI is not authenticated with GitHub. Run `gh auth login` "
                    "(GitHub.com, HTTPS, token with `repo` scope)."
                )
            else:
                probe = subprocess.run(
                    ["gh", "repo", "view", GITHUB_REPO, "--json", "viewerPermission"],
                    capture_output=True,
                    text=True,
                )
                if probe.returncode != 0:
                    errors.append(
                        f"gh cannot reach {GITHUB_REPO}. Check the authenticated account "
                        "has access (`gh auth status`, `gh auth switch`)."
                    )
                elif (
                    '"viewerPermission":"READ"' in probe.stdout
                    or '"viewerPermission":null' in probe.stdout
                ):
                    errors.append(
                        f"Authenticated GitHub user lacks write access to {GITHUB_REPO}. "
                        "Switch to an account with maintainer/admin permission."
                    )
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
    """Archive name: zstd-{platform}-{cpu}.tgz"""
    return f"zstd-{plat}-{TARGETS[arch]['cpu']}.tgz"


def staging_dir_name(plat, arch):
    """Top-level directory inside the archive: zstd-{platform}-{cpu}"""
    return f"zstd-{plat}-{TARGETS[arch]['cpu']}"


def release_tag(version):
    """Release tag derived from the version: zstd-1.5.7"""
    return f"zstd-{version}"


def shared_ext(plat):
    """Shared library extension for a platform."""
    return "dylib" if plat == "mac" else "so"


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
    for name, text in (("smoke.sh", SMOKE_TEST_SH), ("relocate-pc.sh", RELOCATE_PKGCONFIG_SH)):
        path = os.path.join(directory, name)
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, 0o755)
        paths[name] = path
    return paths


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_for_job(version, plat, arch, output_dir):
    """Build one (platform, arch) and return the path to its archive.

    Writes the full build output to ``<output_dir>/logs/<plat>-<arch>.log``
    and prints the log path to stderr on failure.
    """
    job = f"{plat}/{arch}"
    log_dir = os.path.join(output_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{plat}-{arch}.log")

    with open(log_path, "w") as log_file:
        log_file.write(
            f"# zstd build log\n"
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
            log_file.write(f"\n# finished: {time.strftime('%Y-%m-%d %H:%M:%S %z')} (success)\n")
            return path
        except BaseException as exc:
            log_file.write(
                f"\n# finished: {time.strftime('%Y-%m-%d %H:%M:%S %z')} "
                f"(FAILED: {type(exc).__name__}: {exc})\n"
            )
            print(f"\n[{job}] build failed — full log: {log_path}", file=sys.stderr, flush=True)
            raise


def _build_docker(version, plat, arch, output_dir, log_file, job):
    """Build inside a container pinned to the target architecture."""
    image_tag = f"zstd-builder-{version}-{plat}-{arch}"
    container_name = f"zstd-extract-{version}-{plat}-{arch}"
    dir_name = staging_dir_name(plat, arch)
    output_path = os.path.join(output_dir, archive_name(plat, arch))

    print(
        f"\n{'=' * 60}\n  Building zstd {version} for {plat}/{TARGETS[arch]['cpu']}\n{'=' * 60}\n",
        flush=True,
    )

    with tempfile.TemporaryDirectory() as ctx:
        with open(os.path.join(ctx, "Dockerfile"), "w") as f:
            f.write(make_dockerfile(version, arch, plat))
        _write_helper_scripts(ctx)

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
    """Build on the macOS host with CMake — no container involved."""
    if sys.platform != "darwin":
        raise RuntimeError(
            "mac builds require a macOS host (there is no macOS container image); "
            "run this job on a macos runner."
        )

    dir_name = staging_dir_name("mac", arch)
    output_path = os.path.join(output_dir, archive_name("mac", arch))
    workspace = os.path.join(output_dir, f"workspace-mac-{arch}")
    staging = os.path.join(workspace, dir_name)

    print(
        f"\n{'=' * 60}\n  Building zstd {version} for mac/{TARGETS[arch]['cpu']} (native)"
        f"\n{'=' * 60}\n",
        flush=True,
    )

    if os.path.isdir(workspace):
        shutil.rmtree(workspace)
    os.makedirs(workspace)

    tarball = os.path.join(workspace, f"zstd-{version}.tar.gz")
    download_source(version, tarball, log_file, job)
    run_checked(["tar", "xzf", tarball, "-C", workspace], log_file, job)
    src = os.path.join(workspace, f"zstd-{version}")

    run_checked(["cmake"] + cmake_configure_args("mac", arch, staging), log_file, job, cwd=src)
    run_checked(["cmake", "--build", "out", "-j", str(os.cpu_count() or 4)], log_file, job, cwd=src)
    run_checked(["cmake", "--install", "out"], log_file, job, cwd=src)

    helpers = _write_helper_scripts(workspace)
    run_checked(["sh", helpers["relocate-pc.sh"], staging], log_file, job)

    with open(os.path.join(staging, "cmake-args.txt"), "w") as f:
        f.write(cmake_args_text("mac", arch, staging))
    shutil.copy2(os.path.join(src, "LICENSE"), os.path.join(staging, "LICENSE"))

    # Rosetta may or may not be installed, and an arm64 host cannot run
    # an x86_64 binary without it, so only execute the smoke test when
    # the slice we built matches the machine we are on. The link step
    # still runs either way.
    host_apple_arch = platform.machine()
    run_mode = "run" if TARGETS[arch]["apple_arch"] == host_apple_arch else "norun"
    run_checked(
        [
            "sh",
            helpers["smoke.sh"],
            staging,
            version,
            "dylib",
            f"-arch {TARGETS[arch]['apple_arch']}",
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
    """Body of the ``zstd-<version>`` release.

    Kept here rather than in the workflow so a release cut by CI and one
    cut by a local ``--upload`` read the same, digest included. Raises
    ``ValueError`` for a version with no pinned source hash, which is
    the same refusal the build makes.
    """
    return (
        f"zstd {version} built from source.\n\n"
        "Each archive contains:\n"
        "- `lib/libzstd.a` — static archive\n"
        "- `lib/libzstd.so` (`.dylib` on mac) — shared library\n"
        "- `lib/pkgconfig/libzstd.pc` — relocatable pkg-config file\n"
        "- `include/` — public C headers\n"
        "- `cmake-args.txt` — the exact CMake configure flags used\n"
        "- `LICENSE` — zstd's own licence\n\n"
        f"Source: {source_url(version)}\n"
        f"sha256: `{source_sha256(version)}`"
    )


def upload_release(version, built_files):
    """Create or update the zstd-<version> Release with the built assets.

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
                f"zstd {version}",
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
        description="Build zstd static archives and shared libraries",
    )
    parser.add_argument(
        "version",
        nargs="?",
        default=None,
        help="zstd version to build (e.g. 1.5.7). Defaults to the contents of zstd/VERSION.",
    )
    parser.add_argument(
        "--arch",
        choices=["amd64", "x86_64", "x64", "arm64", "aarch64"],
        metavar="ARCH",
        help=(
            "build for a single architecture (default: both). `x86_64`/`x64` are "
            "aliases for `amd64`, `aarch64` for `arm64`."
        ),
    )
    parser.add_argument(
        "--platform",
        choices=PLATFORMS,
        nargs="+",
        default=None,
        help=(
            "target platform(s). Accepts multiple values (e.g. --platform linux musl). "
            "Default matrix: linux/amd64, linux/arm64, musl/amd64, musl/arm64. `mac` "
            "needs a macOS host and is excluded from the default."
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
    except ValueError as exc:
        parser.error(str(exc))

    version = args.version or read_version()
    try:
        source_sha256(version)
    except ValueError as exc:
        parser.error(str(exc))

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    jobs = resolve_jobs(args.platform, args.arch)
    check_dependencies(upload=args.upload, platforms={plat for plat, _ in jobs})

    host_arch = ARCH_ALIASES.get(platform.machine(), platform.machine())
    foreign = [f"{p}/{a}" for p, a in jobs if p != "mac" and a != host_arch]
    if foreign:
        print(
            f"Host CPU arch is '{platform.machine()}'; {', '.join(foreign)} will build in an "
            "emulated container (QEMU). zstd is small enough for that to be minutes, not hours.",
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
            upload_release(version, built_files)
        elif args.upload:
            print("\nNo archives built — skipping upload.", file=sys.stderr)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)

    _print_summary(version, built_files, failures, output_dir, args.upload and bool(built_files))

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
