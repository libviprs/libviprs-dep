#!/usr/bin/env python3
"""Build PDFium shared libraries for Linux amd64 and arm64 using Docker.

Compiles PDFium from source by spinning up temporary Docker containers
for each target architecture. The resulting libpdfium.so binaries can
optionally be uploaded as GitHub Releases to libviprs/libviprs-dep.

Requirements:
    - Docker with buildx support
    - gh CLI (only when using --upload)

Usage:
    python3 build_pdfium.py 7725                    # build both architectures
    python3 build_pdfium.py 7725 --parallel         # build both in parallel
    python3 build_pdfium.py 7725 --arch amd64       # build amd64 only
    python3 build_pdfium.py 7725 --arch arm64       # build arm64 only
    python3 build_pdfium.py 7725 --platform linux   # explicit platform (default)
    python3 build_pdfium.py 7725 --upload           # build and upload to GitHub
"""

import argparse
import atexit
import collections
import concurrent.futures
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time

try:
    import termios
    import tty
    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False

GITHUB_REPO = "libviprs/libviprs-dep"

# Directory containing per-platform patch scripts (e.g. patches/linux.sh).
PATCHES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patches")

# Supported platforms.  Each platform has a patch script in patches/<name>.sh
# that is copied into the Docker build context and run against the PDFium source.
PLATFORMS = ["linux"]

# Target architectures.  All builds run inside an amd64 Docker container
# and cross-compile for arm64 using PDFium's built-in sysroot + clang.
# This avoids slow and fragile QEMU emulation.
TARGETS = {
    "amd64": {"gn_cpu": "x64"},
    "arm64": {"gn_cpu": "arm64"},
}

# GN build arguments for a self-contained shared library.
#
# pdf_is_standalone         — build without chromium browser integration
# pdf_enable_v8             — no JS engine (not needed for rasterization)
# pdf_enable_xfa            — no XFA form support
# is_component_build        — single .so, not many small ones
# use_custom_libcxx         — bundle libc++ so .so is portable across distros
# pdf_use_partition_alloc   — skip complex allocator (fails on some platforms)
# clang_use_chrome_plugins  — skip Chrome's custom clang plugins
GN_ARGS_TEMPLATE = """\
is_debug = false
pdf_is_standalone = true
pdf_enable_v8 = false
pdf_enable_xfa = false
is_component_build = false
treat_warnings_as_errors = false
use_custom_libcxx = true
pdf_use_skia = false
pdf_use_partition_alloc = false
clang_use_chrome_plugins = false
target_os = "linux"
target_cpu = "{gn_cpu}"
"""

# Regex to detect Docker buildkit step markers like [3/14]
STEP_RE = re.compile(r"\[\s*(\d+)/(\d+)\]")

IS_TTY = sys.stdout.isatty()

# Maximum number of output lines to keep per architecture for replay on switch
OUTPUT_BUFFER_SIZE = 500

# ---------------------------------------------------------------------------
# Keyboard listener — reads raw keypresses in a background thread
# ---------------------------------------------------------------------------


class KeyListener:
    """Reads single keypresses from stdin without blocking the main thread.

    Puts terminal into raw mode and reads in a daemon thread.  Calls
    *callback(ch)* for each character received.  Only works on Unix
    systems with termios; on other platforms the listener is a no-op.
    """

    def __init__(self, callback):
        self._callback = callback
        self._stop = threading.Event()
        self._old_settings = None
        self._thread = None

    def start(self):
        if not HAS_TERMIOS or not sys.stdin.isatty():
            return
        self._old_settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._old_settings is not None:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self._old_settings)
            self._old_settings = None

    def _run(self):
        while not self._stop.is_set():
            try:
                ch = sys.stdin.read(1)
                if ch:
                    self._callback(ch)
            except (OSError, ValueError):
                break


# ---------------------------------------------------------------------------
# Terminal UI — fixed header with progress, scrolling build output below
# ---------------------------------------------------------------------------

HEADER_LINES = 9


def fmt_time(seconds):
    """Format seconds as m:ss or h:mm:ss."""
    m, s = divmod(int(seconds), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def make_bar(fraction, width):
    """Render a progress bar string of the given character width."""
    filled = int(fraction * width)
    return "█" * filled + "░" * (width - filled)


class BuildProgress:
    """Manages a fixed terminal header showing per-architecture progress."""

    def __init__(self, version, archs, parallel=False):
        self.version = version
        self.archs = archs
        self._uploading = False
        self._parallel = parallel and len(archs) > 1
        self._lock = threading.Lock()
        self.status = {}
        # Per-arch output buffer (ring buffer of recent lines)
        self._output = {arch: collections.deque(maxlen=OUTPUT_BUFFER_SIZE) for arch in archs}
        # Which arch's output is currently displayed (None = interleaved/sequential)
        self._active_view = archs[0] if self._parallel else None
        self._key_listener = None
        for arch in archs:
            self.status[arch] = {
                "state": "waiting",
                "step": 0,
                "total_steps": 0,
                "start_time": None,
                "elapsed": 0,
            }
        self.active = IS_TTY
        if self.active:
            self._setup()
            atexit.register(self._cleanup)
            if self._parallel:
                self._key_listener = KeyListener(self._on_key)
                self._key_listener.start()

    # -- terminal setup / teardown -----------------------------------------

    def _setup(self):
        rows = shutil.get_terminal_size().lines
        # Reserve header area: clear lines then set scroll region below it.
        sys.stdout.write("\033[H")
        for _ in range(HEADER_LINES):
            sys.stdout.write("\033[2K\n")
        sys.stdout.write(f"\033[{HEADER_LINES + 1};{rows}r")
        sys.stdout.write(f"\033[{HEADER_LINES + 1};1H")
        sys.stdout.flush()
        self._render()

    def _cleanup(self):
        if not self.active:
            return
        if self._key_listener:
            self._key_listener.stop()
            self._key_listener = None
        # Reset scroll region and move cursor below everything.
        sys.stdout.write("\033[r")
        rows = shutil.get_terminal_size().lines
        sys.stdout.write(f"\033[{rows};1H\n")
        sys.stdout.flush()
        self.active = False

    def finish(self):
        """Explicitly tear down the header when we're done."""
        self._cleanup()

    def _on_key(self, ch):
        """Handle a keypress for view switching during parallel builds."""
        if ch == "\t":
            # Tab: cycle to next arch
            with self._lock:
                idx = self.archs.index(self._active_view)
                self._active_view = self.archs[(idx + 1) % len(self.archs)]
                self._replay_output()
                self._render()
        elif ch in ("1", "2"):
            idx = int(ch) - 1
            if idx < len(self.archs):
                with self._lock:
                    self._active_view = self.archs[idx]
                    self._replay_output()
                    self._render()

    def _replay_output(self):
        """Clear the scroll area and replay buffered output for the active view."""
        if not self.active or not self._active_view:
            return
        rows = shutil.get_terminal_size().lines
        scroll_lines = rows - HEADER_LINES
        # Move to scroll region top and clear it
        sys.stdout.write(f"\033[{HEADER_LINES + 1};1H")
        for _ in range(scroll_lines):
            sys.stdout.write("\033[2K\n")
        # Replay recent lines
        buf = self._output[self._active_view]
        replay = list(buf)[-scroll_lines:]
        sys.stdout.write(f"\033[{HEADER_LINES + 1};1H")
        for line in replay:
            sys.stdout.write(f"{line}\n")
        sys.stdout.flush()

    # -- state updates -----------------------------------------------------

    def start_arch(self, arch):
        with self._lock:
            s = self.status[arch]
            s["state"] = "building"
            s["step"] = 0
            s["total_steps"] = 0
            s["start_time"] = time.time()
            self._render()

    def set_step(self, arch, step, total):
        with self._lock:
            s = self.status[arch]
            s["step"] = step
            s["total_steps"] = total
            s["elapsed"] = time.time() - s["start_time"]
            self._render()

    def set_extracting(self, arch):
        with self._lock:
            s = self.status[arch]
            s["state"] = "extracting"
            if s["start_time"]:
                s["elapsed"] = time.time() - s["start_time"]
            self._render()

    def set_done(self, arch):
        with self._lock:
            s = self.status[arch]
            s["state"] = "done"
            if s["start_time"]:
                s["elapsed"] = time.time() - s["start_time"]
            self._render()

    def set_failed(self, arch):
        with self._lock:
            s = self.status[arch]
            s["state"] = "failed"
            if s["start_time"]:
                s["elapsed"] = time.time() - s["start_time"]
            self._render()

    def set_uploading(self):
        """Replace all arch statuses with a single uploading message."""
        with self._lock:
            self._uploading = True
            self._render()

    # -- rendering ---------------------------------------------------------

    def _render(self):
        if not self.active:
            return
        w = min(shutil.get_terminal_size().columns, 120)
        lines = []

        title = f" PDFium chromium/{self.version} "
        if self._parallel and self._active_view:
            hint = f"viewing: {self._active_view}  (Tab/1/2 to switch) "
            pad = max(w - len(title) - len(hint) - 4, 0)
            lines.append(f"┌──{title}{'─' * pad}{hint}┐")
        else:
            pad = max(w - len(title) - 4, 0)
            lines.append(f"┌──{title}{'─' * pad}┐")
        lines.append(f"│{' ' * (w - 2)}│")

        if self._uploading:
            line = "  Uploading to GitHub Releases..."
            lines.append(f"│{line:<{w - 2}}│")
        else:
            for arch in self.archs:
                s = self.status[arch]
                arch_lines = self._render_arch(arch, s, w)
                lines.extend(arch_lines)

        lines.append(f"│{' ' * (w - 2)}│")
        lines.append(f"└{'─' * (w - 2)}┘")

        # Pad or trim to fixed height
        while len(lines) < HEADER_LINES:
            lines.append("")
        lines = lines[:HEADER_LINES]

        sys.stdout.write("\033[s")  # save cursor
        sys.stdout.write("\033[H")  # move to top-left
        for line in lines:
            sys.stdout.write(f"\033[2K{line}\n")
        sys.stdout.write("\033[u")  # restore cursor
        sys.stdout.flush()

    def _render_arch(self, arch, s, w):
        state = s["state"]
        elapsed = s["elapsed"]
        lines = []

        if state == "waiting":
            line = f"  {arch:<7} waiting"
            lines.append(f"│{line:<{w - 2}}│")

        elif state == "building":
            step = s["step"]
            total = s["total_steps"] or 1
            frac = step / total
            bar_w = max(w - 42, 10)
            bar = make_bar(frac, bar_w)
            pct = int(frac * 100)
            step_label = f"Step {step}/{total}" if total > 1 else "starting..."
            line = f"  {arch:<7} {bar}  {pct:>3}%  {step_label}"
            lines.append(f"│{line:<{w - 2}}│")

            time_parts = [f"{fmt_time(elapsed)} elapsed"]
            if step > 0 and total > 0:
                rate = elapsed / step
                remaining = rate * (total - step)
                time_parts.append(f"~{fmt_time(remaining)} remaining")
            time_line = "  " + " " * 8 + " · ".join(time_parts)
            lines.append(f"│{time_line:<{w - 2}}│")

        elif state == "extracting":
            line = f"  {arch:<7} extracting binary...  ({fmt_time(elapsed)})"
            lines.append(f"│{line:<{w - 2}}│")

        elif state == "done":
            line = f"  {arch:<7} ✓ done  ({fmt_time(elapsed)})"
            lines.append(f"│{line:<{w - 2}}│")

        elif state == "failed":
            line = f"  {arch:<7} ✗ failed  ({fmt_time(elapsed)})"
            lines.append(f"│{line:<{w - 2}}│")

        return lines

    # -- Docker output streaming with step parsing -------------------------

    def stream_docker_build(self, cmd, arch):
        """Run a docker build command, stream its output, and parse step progress."""
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in process.stdout:
            line = line.rstrip("\n")
            # Parse step markers
            m = STEP_RE.search(line)
            if m:
                step = int(m.group(1))
                total = int(m.group(2))
                self.set_step(arch, step, total)
            # Buffer and conditionally display
            with self._lock:
                if self._parallel:
                    self._output[arch].append(line)
                    # Only print if this arch is the active view
                    if self._active_view == arch:
                        if self.active:
                            sys.stdout.write(f"{line}\n")
                            sys.stdout.flush()
                        else:
                            print(line)
                else:
                    if self.active:
                        sys.stdout.write(f"{line}\n")
                        sys.stdout.flush()
                    else:
                        print(line)
        process.wait()
        return process.returncode


# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------

def check_dependencies(upload):
    """Verify all required external tools are installed."""
    errors = []

    if sys.version_info < (3, 7):
        errors.append(
            f"Python 3.7+ required, found {platform.python_version()}. "
            f"Install from https://www.python.org/downloads/"
        )

    if not shutil.which("docker"):
        errors.append(
            "docker not found. "
            "Install from https://docs.docker.com/get-docker/"
        )
    else:
        result = subprocess.run(["docker", "info"], capture_output=True)
        if result.returncode != 0:
            errors.append(
                "Docker daemon is not running. Start Docker and try again."
            )
        else:
            result = subprocess.run(
                ["docker", "buildx", "version"], capture_output=True
            )
            if result.returncode != 0:
                errors.append(
                    "docker buildx not available. "
                    "Install from https://docs.docker.com/build/install-buildx/"
                )

    if upload:
        if not shutil.which("gh"):
            errors.append(
                "gh CLI not found (required for --upload). "
                "Install from https://cli.github.com/"
            )
        else:
            result = subprocess.run(
                ["gh", "auth", "status"], capture_output=True
            )
            if result.returncode != 0:
                errors.append(
                    "gh CLI is not authenticated. Run 'gh auth login' first."
                )

    if errors:
        print("Missing dependencies:\n")
        for err in errors:
            print(f"  - {err}")
        print()
        sys.exit(1)


# ---------------------------------------------------------------------------
# Dockerfile generation
# ---------------------------------------------------------------------------

def make_dockerfile(version, arch, plat):
    """Generate a Dockerfile that compiles PDFium at the given version.

    All builds run inside an amd64 container.  For arm64 targets, PDFium
    is cross-compiled using its built-in clang and a Debian sysroot,
    avoiding slow QEMU emulation entirely.

    The platform patch script (patches/<plat>.sh) is copied into the
    build context and applied in Step 5.
    """
    target = TARGETS[arch]
    gn_cpu = target["gn_cpu"]
    branch = f"chromium/{version}"
    gn_args = GN_ARGS_TEMPLATE.format(gn_cpu=gn_cpu)

    # For cross-compilation, install the target arch's cross-compiler
    base_pkgs = (
        "git curl python3 ca-certificates "
        "build-essential pkg-config lsb-release sudo file"
    )
    if arch == "arm64":
        base_pkgs += " g++-aarch64-linux-gnu"

    return f"""\
FROM debian:bookworm-slim

# Step 0: System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    {base_pkgs} \\
    && ln -sf /usr/bin/python3 /usr/bin/python \\
    && rm -rf /var/lib/apt/lists/*

# Step 1: Install depot_tools
RUN git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git \\
    /opt/depot_tools
ENV PATH="/opt/depot_tools:${{PATH}}"
ENV DEPOT_TOOLS_UPDATE=0

# Step 2: Configure gclient
# checkout_configuration=small skips V8, test deps, and cipd packages
# (including RBE client which doesn't exist for arm64).
WORKDIR /build
RUN gclient config --unmanaged https://pdfium.googlesource.com/pdfium.git \\
    --custom-var "checkout_configuration=small"
RUN echo "target_os = [ '{plat}' ]" >> .gclient

# Step 3: Checkout source at target branch
RUN gclient sync -r "origin/{branch}" --no-history --shallow

# Step 4: Build dependencies and sysroot
WORKDIR /build/pdfium
RUN build/install-build-deps.sh --no-prompt --no-chromeos-fonts --no-nacl || true
RUN gclient runhooks
RUN python3 build/linux/sysroot_scripts/install-sysroot.py --arch={gn_cpu}

# Step 5: Apply platform patch
COPY platform.sh /tmp/platform.sh
RUN chmod +x /tmp/platform.sh && /tmp/platform.sh /build/pdfium

# Step 6: Configure GN
RUN mkdir -p out/Release && cat > out/Release/args.gn <<'ARGS'
{gn_args}ARGS
RUN gn gen out/Release

# Step 7: Build
RUN ninja -C out/Release pdfium

# Step 8: Verify output
RUN ls -lh out/Release/libpdfium.so && file out/Release/libpdfium.so

# Step 9: Stage artifacts into /staging
COPY LICENSE /tmp/LICENSE
RUN mkdir -p /staging/lib /staging/include && \\
    cp out/Release/libpdfium.so /staging/lib/ && \\
    cp out/Release/args.gn /staging/ && \\
    cp -r public/*.h /staging/include/ && \\
    cp /tmp/LICENSE /staging/
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def archive_name(plat, arch):
    """Archive name: pdfium-{platform}-{gn_cpu}.tgz"""
    gn_cpu = TARGETS[arch]["gn_cpu"]
    return f"pdfium-{plat}-{gn_cpu}.tgz"


def staging_dir_name(plat, arch):
    """Top-level directory name inside the archive: pdfium-{platform}-{gn_cpu}"""
    gn_cpu = TARGETS[arch]["gn_cpu"]
    return f"pdfium-{plat}-{gn_cpu}"


def release_tag(version):
    """Release tag derived from version: pdfium-7725"""
    return f"pdfium-{version}"


def run(cmd, **kwargs):
    """Run a command, printing it for visibility."""
    display = cmd if isinstance(cmd, str) else " ".join(cmd)
    print(f"  $ {display}", flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def build_for_arch(version, arch, plat, output_dir, progress):
    """Build PDFium for a single architecture using Docker.

    All builds run inside an amd64 container.  arm64 targets are
    cross-compiled using PDFium's clang and a Debian sysroot.

    The platform patch script (patches/<plat>.sh) is copied into the
    Docker build context as ``platform.sh`` and applied during the build.
    """
    image_tag = f"pdfium-builder-{version}-{arch}"
    container_name = f"pdfium-extract-{version}-{arch}"

    progress.start_arch(arch)

    patch_script = os.path.join(PATCHES_DIR, f"{plat}.sh")
    if not os.path.isfile(patch_script):
        progress.set_failed(arch)
        raise RuntimeError(
            f"No patch script for platform '{plat}' "
            f"(expected {patch_script})"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        dockerfile_path = os.path.join(tmpdir, "Dockerfile")
        with open(dockerfile_path, "w") as f:
            f.write(make_dockerfile(version, arch, plat))

        # Copy the platform patch script and LICENSE into the build context
        shutil.copy2(patch_script, os.path.join(tmpdir, "platform.sh"))
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        shutil.copy2(
            os.path.join(repo_root, "LICENSE"),
            os.path.join(tmpdir, "LICENSE"),
        )

        # No --platform flag: always build on host (amd64).
        # Cross-compilation is handled by GN args + sysroot.
        cmd = [
            "docker", "build",
            "--progress=plain",
            "-t", image_tag,
            tmpdir,
        ]
        rc = progress.stream_docker_build(cmd, arch)
        if rc != 0:
            progress.set_failed(arch)
            raise RuntimeError(f"Docker build failed for {arch} (exit {rc})")

    # Extract staged artifacts and create tarball
    progress.set_extracting(arch)
    dir_name = staging_dir_name(plat, arch)
    tarball = archive_name(plat, arch)
    output_path = os.path.join(output_dir, tarball)

    with tempfile.TemporaryDirectory() as extract_dir:
        staging_dest = os.path.join(extract_dir, dir_name)
        try:
            run(["docker", "create", "--name", container_name, image_tag])
            run([
                "docker", "cp",
                f"{container_name}:/staging",
                staging_dest,
            ])
        finally:
            subprocess.run(
                ["docker", "rm", "-f", container_name], capture_output=True
            )
            subprocess.run(
                ["docker", "rmi", "-f", image_tag], capture_output=True
            )

        # Create tarball with top-level directory name
        run([
            "tar", "czf", output_path,
            "-C", extract_dir,
            dir_name,
        ])

    progress.set_done(arch)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"  -> {output_path} ({size_mb:.1f} MB)", flush=True)
    return output_path


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_release(version, built_files, progress):
    """Create a GitHub Release and upload the built binaries."""
    tag = release_tag(version)
    branch = f"chromium/{version}"

    progress.set_uploading()

    # Delete existing release if present
    result = subprocess.run(
        ["gh", "release", "view", tag, "-R", GITHUB_REPO],
        capture_output=True,
    )
    if result.returncode == 0:
        print(f"Release '{tag}' already exists, deleting...", flush=True)
        run([
            "gh", "release", "delete", tag,
            "-R", GITHUB_REPO, "--yes",
        ])

    run([
        "gh", "release", "create", tag,
        "-R", GITHUB_REPO,
        "--title", f"PDFium {branch}",
        "--notes",
        f"PDFium shared library built from source.\n\n"
        f"Source: https://pdfium.googlesource.com/pdfium/+/refs/heads/{branch}\n\n"
        f"Build configuration:\n```\n{GN_ARGS_TEMPLATE.format(gn_cpu='<target>')}```",
        *built_files,
    ])

    print(
        f"\nRelease: "
        f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build PDFium shared libraries using Docker",
    )
    parser.add_argument(
        "version",
        help="PDFium chromium branch number (e.g. 7725 for chromium/7725)",
    )
    parser.add_argument(
        "--arch",
        choices=["amd64", "arm64"],
        help="build for a single architecture (default: both)",
    )
    parser.add_argument(
        "--platform",
        choices=PLATFORMS,
        default="linux",
        help="target platform — selects the patch script from patches/ (default: linux)",
    )
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="build all architectures in parallel (default: sequential)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="upload binaries to GitHub Releases",
    )
    parser.add_argument(
        "--output-dir",
        default="./bin",
        help="output directory (default: ./bin)",
    )
    args = parser.parse_args()

    check_dependencies(upload=args.upload)

    archs = [args.arch] if args.arch else ["amd64", "arm64"]
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    progress = BuildProgress(args.version, archs, parallel=args.parallel)

    built_files = []
    try:
        plat = args.platform
        if args.parallel and len(archs) > 1:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(archs)) as pool:
                futures = {
                    pool.submit(build_for_arch, args.version, arch, plat, output_dir, progress): arch
                    for arch in archs
                }
                for future in concurrent.futures.as_completed(futures):
                    arch = futures[future]
                    built_files.append(future.result())
            # Sort so upload order is deterministic (amd64 before arm64)
            built_files.sort()
        else:
            for arch in archs:
                path = build_for_arch(args.version, arch, plat, output_dir, progress)
                built_files.append(path)

        if args.upload:
            upload_release(args.version, built_files, progress)
    except (RuntimeError, subprocess.CalledProcessError) as e:
        progress.finish()
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        progress.finish()
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)

    progress.finish()
    print(f"\nBuilt {len(built_files)} binaries in {output_dir}/")


if __name__ == "__main__":
    main()
