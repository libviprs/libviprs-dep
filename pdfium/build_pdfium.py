#!/usr/bin/env python3
"""Build PDFium shared libraries for Linux and macOS using Docker.

Compiles PDFium from source by spinning up temporary Docker containers
for each target architecture. Linux builds produce libpdfium.so, macOS
builds cross-compile from Linux and produce libpdfium.dylib. Binaries
can optionally be uploaded as GitHub Releases to libviprs/libviprs-dep.

Requirements:
    - Docker with buildx support
    - gh CLI (only when using --upload)

Usage:
    python3 build_pdfium.py 7725                    # build both architectures
    python3 build_pdfium.py 7725 --parallel         # build both in parallel
    python3 build_pdfium.py 7725 --arch amd64       # build amd64 only
    python3 build_pdfium.py 7725 --arch arm64       # build arm64 only
    python3 build_pdfium.py 7725 --platform linux   # explicit platform (default)
    python3 build_pdfium.py 7725 --platform mac     # build for macOS
    python3 build_pdfium.py 7725 --platform musl    # build for musl/Alpine
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

# Directory containing per-platform patch scripts (e.g. patches/linux.py).
PATCHES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "patches")

# Supported platforms.  Each platform has a patch script in patches/<name>.py
# that is copied into the Docker build context and run against the PDFium source.
PLATFORMS = ["linux", "mac", "musl"]

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
GN_ARGS_COMMON = """\
is_debug = false
pdf_is_standalone = true
pdf_enable_v8 = false
pdf_enable_xfa = false
is_component_build = false
treat_warnings_as_errors = false
pdf_use_skia = false
pdf_use_partition_alloc = false
clang_use_chrome_plugins = false
target_cpu = "{gn_cpu}"
{extra_args}
"""

# Per-platform GN args appended to GN_ARGS_COMMON.
GN_ARGS_PLATFORM = {
    "linux": 'target_os = "linux"\nuse_custom_libcxx = true',
    "mac": 'target_os = "mac"',
    "musl": (
        'target_os = "linux"\n'
        "is_musl = true\n"
        "is_clang = false\n"
        "use_custom_libcxx = false\n"
        "use_custom_libcxx_for_host = false\n"
        "use_glib = false"
    ),
}


def gn_args_for(plat, gn_cpu, extra_args):
    """Build the full GN args string for a platform + architecture."""
    platform_args = GN_ARGS_PLATFORM.get(plat, "")
    all_extra = "\n".join(filter(None, [platform_args, extra_args]))
    return GN_ARGS_COMMON.format(gn_cpu=gn_cpu, extra_args=all_extra)


# arm64: disable Branch Target Identification enforcement — the Debian
# Bullseye sysroot's CRT objects (crti.o, crtbeginS.o) weren't compiled
# with BTI support, so the linker fails with -z force-bti.
GN_ARGS_ARM64 = 'arm_control_flow_integrity = "none"'

# Regex to detect Docker buildkit step markers like [3/14]
STEP_RE = re.compile(r"\[\s*(\d+)/(\d+)\]")

IS_TTY = sys.stdout.isatty()

# Maximum number of output lines to keep per architecture for replay on switch
OUTPUT_BUFFER_SIZE = 500

# Minimum thresholds before we transition from "estimating..." to a
# numeric ETA.  Cached Docker steps complete instantly and inflate the
# rate, so we need real wall time and real step throughput first.
ETA_MIN_PHASE_SECS = 10  # seconds of wall time in current phase
ETA_MIN_PHASE_STEPS = 5  # steps completed in current phase

# EMA smoothing factor for step completion rate.  Applied over the most
# recent 60 non-COPY step intervals.  Higher = more weight on recent.
# 2/(60+1) ≈ 0.033 gives a smooth average over ~60 observations.
EMA_ALPHA = 2.0 / 61.0

# Initial pessimistic ETA (seconds) shown when the estimating period
# ends but the EMA doesn't yet have enough samples to be reliable.
# The displayed estimate starts here and converges toward the observed
# rate as the window fills.
ETA_INITIAL_SECS = 3600  # 60 minutes

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


def _estimate_remaining(
    step, total, phase_start_step, phase_start_time, ema_secs_per_step, ema_samples, now
):
    """Estimate remaining build time, or return None if insufficient data.

    Returns None during the "estimating..." period (not enough wall time
    or steps to compute a meaningful rate).

    Once past that threshold, uses the EMA-smoothed seconds-per-step rate
    (computed over the last ~60 non-COPY step intervals) and multiplies
    by remaining steps.  Blends with a pessimistic prior (ETA_INITIAL_SECS)
    that decays as the EMA accumulates more samples, so the estimate
    starts high and converges toward the observed rate.
    """
    if phase_start_time is None:
        return None

    phase_elapsed = now - phase_start_time
    phase_steps_done = step - phase_start_step
    remaining_steps = total - step

    if phase_elapsed < ETA_MIN_PHASE_SECS or phase_steps_done < ETA_MIN_PHASE_STEPS:
        return None

    # EMA samples needed before we fully trust the observed rate.
    # Matches the EMA span: 60 samples for full confidence.
    EMA_FULL_CONFIDENCE = 60

    if ema_secs_per_step is not None and ema_secs_per_step > 0:
        observed_remaining = ema_secs_per_step * remaining_steps
    else:
        # No valid EMA yet (all steps were COPY/CACHED) — use prior only
        return max(ETA_INITIAL_SECS - phase_elapsed, 0)

    # Blend: trust ramps linearly with EMA sample count.
    # At 0 samples → pure prior; at 60 samples → pure EMA.
    trust = min(ema_samples / EMA_FULL_CONFIDENCE, 1.0)
    prior_remaining = max(ETA_INITIAL_SECS - phase_elapsed, 0)
    blended = (1 - trust) * prior_remaining + trust * observed_remaining

    return max(blended, 0)


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
                # Phase tracking: when the step counter resets (e.g. Docker
                # steps → ninja steps), we record the new baseline so the
                # ETA is computed from the current phase's pace, not the
                # overall average which is polluted by cached steps.
                "phase_start_step": 0,
                "phase_start_time": None,
                # EMA rate tracking (secs per step), ignoring COPY steps.
                "ema_secs_per_step": None,
                "ema_samples": 0,  # how many observations fed the EMA
                "last_step_num": 0,
                "last_step_time": None,
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

    def set_step(self, arch, step, total, is_copy=False):
        with self._lock:
            s = self.status[arch]
            now = time.time()

            # Detect phase change: step counter reset or total changed
            # (e.g. Docker [15/20] → ninja [1/2223])
            if step < s["step"] or total != s["total_steps"] or s["phase_start_time"] is None:
                s["phase_start_step"] = step
                s["phase_start_time"] = now
                s["ema_secs_per_step"] = None
                s["ema_samples"] = 0

            # Update EMA rate, skipping COPY steps which are instant
            # and would pollute the rate estimate.
            if not is_copy and s["last_step_time"] is not None and step > s["last_step_num"]:
                dt = now - s["last_step_time"]
                ds = step - s["last_step_num"]
                if dt > 0 and ds > 0:
                    instant_rate = dt / ds  # secs per step
                    if s["ema_secs_per_step"] is None:
                        s["ema_secs_per_step"] = instant_rate
                    else:
                        s["ema_secs_per_step"] = (
                            EMA_ALPHA * instant_rate + (1 - EMA_ALPHA) * s["ema_secs_per_step"]
                        )
                    s["ema_samples"] += 1

            s["last_step_num"] = step
            s["last_step_time"] = now
            s["step"] = step
            s["total_steps"] = total
            s["elapsed"] = now - s["start_time"]
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
                highlighted = self._parallel and arch == self._active_view
                arch_lines = self._render_arch(arch, s, w, highlighted)
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

    def _render_arch(self, arch, s, w, highlighted=False):
        state = s["state"]
        elapsed = s["elapsed"]
        lines = []
        # ANSI: dim white background for the active view row
        bg_on = "\033[48;5;236m" if highlighted else ""
        bg_off = "\033[0m" if highlighted else ""
        indicator = ">" if highlighted else " "

        if state == "waiting":
            line = f" {indicator}{arch:<7} waiting"
            lines.append(f"│{bg_on}{line:<{w - 2}}{bg_off}│")

        elif state == "building":
            step = s["step"]
            total = s["total_steps"] or 1
            frac = step / total
            bar_w = max(w - 42, 10)
            bar = make_bar(frac, bar_w)
            pct = int(frac * 100)
            step_label = f"Step {step}/{total}" if total > 1 else "starting..."
            line = f" {indicator}{arch:<7} {bar}  {pct:>3}%  {step_label}"
            lines.append(f"│{bg_on}{line:<{w - 2}}{bg_off}│")

            time_parts = [f"{fmt_time(elapsed)} elapsed"]
            if step < total:
                remaining = _estimate_remaining(
                    step,
                    total,
                    s["phase_start_step"],
                    s["phase_start_time"],
                    s["ema_secs_per_step"],
                    s["ema_samples"],
                    time.time(),
                )
                if remaining is not None:
                    time_parts.append(f"~{fmt_time(remaining)} remaining")
                else:
                    time_parts.append("estimating...")
            time_line = "  " + " " * 8 + " · ".join(time_parts)
            lines.append(f"│{bg_on}{time_line:<{w - 2}}{bg_off}│")

        elif state == "extracting":
            line = f" {indicator}{arch:<7} extracting binary...  ({fmt_time(elapsed)})"
            lines.append(f"│{bg_on}{line:<{w - 2}}{bg_off}│")

        elif state == "done":
            line = f" {indicator}{arch:<7} ✓ done  ({fmt_time(elapsed)})"
            lines.append(f"│{bg_on}{line:<{w - 2}}{bg_off}│")

        elif state == "failed":
            line = f" {indicator}{arch:<7} ✗ failed  ({fmt_time(elapsed)})"
            lines.append(f"│{bg_on}{line:<{w - 2}}{bg_off}│")

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
                # COPY/CACHED steps are instant and would pollute the
                # EMA rate — flag them so set_step skips the rate update.
                is_copy = "] COPY " in line or "] CACHED" in line
                self.set_step(arch, step, total, is_copy=is_copy)
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
        errors.append("docker not found. Install from https://docs.docker.com/get-docker/")
    else:
        result = subprocess.run(["docker", "info"], capture_output=True)
        if result.returncode != 0:
            errors.append("Docker daemon is not running. Start Docker and try again.")
        else:
            result = subprocess.run(["docker", "buildx", "version"], capture_output=True)
            if result.returncode != 0:
                errors.append(
                    "docker buildx not available. "
                    "Install from https://docs.docker.com/build/install-buildx/"
                )

    if upload:
        if not shutil.which("gh"):
            errors.append(
                "gh CLI not found (required for --upload). Install from https://cli.github.com/"
            )
        else:
            result = subprocess.run(["gh", "auth", "status"], capture_output=True)
            if result.returncode != 0:
                errors.append("gh CLI is not authenticated. Run 'gh auth login' first.")

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

    The platform patch script (patches/<plat>.py) is copied into the
    build context and applied in Step 5.
    """
    if plat == "mac":
        return _make_dockerfile_mac(version, arch)
    if plat == "musl":
        return _make_dockerfile_musl(version, arch)
    return _make_dockerfile_linux(version, arch, plat)


def _make_dockerfile_linux(version, arch, plat):
    """Dockerfile for Linux builds (runs in Docker, cross-compiles arm64).

    Two ninja phases are run against the same source checkout so the
    release archive ships both ``libpdfium.a`` (produced by the
    base-patched ``component("pdfium")`` → ``static_library``) and
    ``libpdfium.so`` (produced after the shared-library rewrite).
    """
    target = TARGETS[arch]
    gn_cpu = target["gn_cpu"]
    branch = f"chromium/{version}"
    extra_args = GN_ARGS_ARM64 if arch == "arm64" else ""
    gn_args = gn_args_for(plat, gn_cpu, extra_args)

    # For cross-compilation, install the target arch's cross-compiler
    base_pkgs = "git curl python3 ca-certificates build-essential pkg-config lsb-release sudo file"
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
# Bootstrap depot_tools (creates python3_bin_reldir.txt needed by gn),
# then disable auto-updates for the rest of the build.
RUN gclient --version
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

# Step 5: Apply base platform patch (fpdfview.h symbol visibility only)
COPY platform.py /tmp/platform.py
RUN python3 /tmp/platform.py /build/pdfium --mode base

# Step 6: Configure static build
RUN mkdir -p out/Static && cat > out/Static/args.gn <<'ARGS'
{gn_args}ARGS
RUN gn gen out/Static

# Step 7: Build static archive (component() -> static_library -> libpdfium.a)
RUN ninja -C out/Static pdfium

# Step 8: Apply shared-library patch on top of base
RUN python3 /tmp/platform.py /build/pdfium --mode shared

# Step 9: Configure shared build
RUN mkdir -p out/Shared && cat > out/Shared/args.gn <<'ARGS'
{gn_args}ARGS
RUN gn gen out/Shared

# Step 10: Build shared library (shared_library -> libpdfium.so)
RUN ninja -C out/Shared pdfium

# Step 11: Verify both outputs
RUN ls -lh out/Static/libpdfium.a out/Shared/libpdfium.so && \\
    file out/Static/libpdfium.a out/Shared/libpdfium.so

# Step 12: Stage artifacts into /staging
COPY LICENSE /tmp/LICENSE
RUN mkdir -p /staging/lib /staging/include && \\
    cp out/Shared/libpdfium.so /staging/lib/ && \\
    cp out/Static/libpdfium.a /staging/lib/ && \\
    cp out/Shared/args.gn /staging/args.gn && \\
    cp out/Static/args.gn /staging/args.static.gn && \\
    cp -r public/*.h /staging/include/ && \\
    cp /tmp/LICENSE /staging/
"""


def _make_dockerfile_mac(version, arch):
    """Dockerfile for macOS builds.

    macOS builds cannot run natively inside Docker.  This Dockerfile
    cross-compiles PDFium for macOS using the Chromium toolchain's clang
    and the macOS SDK sysroot that gclient downloads.  The host container
    is still Linux (amd64).
    """
    target = TARGETS[arch]
    gn_cpu = target["gn_cpu"]
    branch = f"chromium/{version}"
    gn_args = gn_args_for("mac", gn_cpu, "")

    return f"""\
FROM debian:bookworm-slim

# Step 0: System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    git curl python3 ca-certificates build-essential pkg-config lsb-release sudo file \\
    && ln -sf /usr/bin/python3 /usr/bin/python \\
    && rm -rf /var/lib/apt/lists/*

# Step 1: Install depot_tools
RUN git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git \\
    /opt/depot_tools
ENV PATH="/opt/depot_tools:${{PATH}}"
RUN gclient --version
ENV DEPOT_TOOLS_UPDATE=0

# Step 2: Configure gclient
WORKDIR /build
RUN gclient config --unmanaged https://pdfium.googlesource.com/pdfium.git \\
    --custom-var "checkout_configuration=small"
RUN echo "target_os = [ 'mac' ]" >> .gclient

# Step 3: Checkout source at target branch
RUN gclient sync -r "origin/{branch}" --no-history --shallow

# Step 4: Build dependencies
WORKDIR /build/pdfium
RUN gclient runhooks

# Step 5: Apply platform patch
COPY platform.py /tmp/platform.py
RUN python3 /tmp/platform.py /build/pdfium

# Step 6: Configure GN
RUN mkdir -p out/Release && cat > out/Release/args.gn <<'ARGS'
{gn_args}ARGS
RUN gn gen out/Release

# Step 7: Build
RUN ninja -C out/Release pdfium

# Step 8: Verify output
RUN ls -lh out/Release/libpdfium.dylib && file out/Release/libpdfium.dylib

# Step 9: Stage artifacts into /staging
COPY LICENSE /tmp/LICENSE
RUN mkdir -p /staging/lib /staging/include && \\
    cp out/Release/libpdfium.dylib /staging/lib/ && \\
    cp out/Release/args.gn /staging/ && \\
    cp -r public/*.h /staging/include/ && \\
    cp /tmp/LICENSE /staging/
"""


def _make_dockerfile_musl(version, arch):
    """Dockerfile for musl (Alpine-compatible) builds.

    Uses musl-cross-make toolchains instead of Chromium's clang. The
    musl patch script installs a custom GN toolchain definition and
    patches BUILDCONFIG.gn to route builds through musl-gcc. Like the
    linux dockerfile, this runs two ninja phases so a single source
    checkout produces both ``libpdfium.a`` and ``libpdfium.so``.
    """
    target = TARGETS[arch]
    gn_cpu = target["gn_cpu"]
    branch = f"chromium/{version}"
    gn_args = gn_args_for("musl", gn_cpu, "")

    # Map gn_cpu to musl-cross-make target triple prefix
    musl_targets = {
        "x64": "x86_64-linux-musl",
        "arm64": "aarch64-linux-musl",
    }
    musl_target = musl_targets[gn_cpu]

    return f"""\
FROM debian:bookworm-slim

# Step 0: System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \\
    git curl python3 ca-certificates build-essential pkg-config lsb-release sudo file \\
    xz-utils \\
    && ln -sf /usr/bin/python3 /usr/bin/python \\
    && rm -rf /var/lib/apt/lists/*

# Step 1: Install musl-cross-make toolchain
RUN curl -fsSL "https://musl.cc/{musl_target}-cross.tgz" | tar xz -C /opt
ENV PATH="/opt/{musl_target}-cross/bin:${{PATH}}"

# Step 2: Install depot_tools
RUN git clone https://chromium.googlesource.com/chromium/tools/depot_tools.git \\
    /opt/depot_tools
ENV PATH="/opt/depot_tools:${{PATH}}"
RUN gclient --version
ENV DEPOT_TOOLS_UPDATE=0

# Step 3: Configure gclient
WORKDIR /build
RUN gclient config --unmanaged https://pdfium.googlesource.com/pdfium.git \\
    --custom-var "checkout_configuration=small"
RUN echo "target_os = [ 'linux' ]" >> .gclient

# Step 4: Checkout source at target branch
RUN gclient sync -r "origin/{branch}" --no-history --shallow

# Step 5: Build dependencies
WORKDIR /build/pdfium
RUN gclient runhooks

# Step 6: Apply base platform patch (no shared_library rewrite yet)
COPY platform.py /tmp/platform.py
RUN python3 /tmp/platform.py /build/pdfium --mode base

# Step 7: Configure static build
RUN mkdir -p out/Static && cat > out/Static/args.gn <<'ARGS'
{gn_args}ARGS
RUN gn gen out/Static

# Step 8: Build static archive (component() -> static_library -> libpdfium.a)
RUN ninja -C out/Static pdfium

# Step 9: Apply shared-library patch on top of base
RUN python3 /tmp/platform.py /build/pdfium --mode shared

# Step 10: Configure shared build
RUN mkdir -p out/Shared && cat > out/Shared/args.gn <<'ARGS'
{gn_args}ARGS
RUN gn gen out/Shared

# Step 11: Build shared library (shared_library -> libpdfium.so)
RUN ninja -C out/Shared pdfium

# Step 12: Verify both outputs
RUN ls -lh out/Static/libpdfium.a out/Shared/libpdfium.so && \\
    file out/Static/libpdfium.a out/Shared/libpdfium.so

# Step 13: Stage artifacts into /staging
COPY LICENSE /tmp/LICENSE
RUN mkdir -p /staging/lib /staging/include && \\
    cp out/Shared/libpdfium.so /staging/lib/ && \\
    cp out/Static/libpdfium.a /staging/lib/ && \\
    cp out/Shared/args.gn /staging/args.gn && \\
    cp out/Static/args.gn /staging/args.static.gn && \\
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

    The platform patch script (patches/<plat>.py) is copied into the
    Docker build context as ``platform.py`` and applied during the build.
    """
    image_tag = f"pdfium-builder-{version}-{arch}"
    container_name = f"pdfium-extract-{version}-{arch}"

    progress.start_arch(arch)

    patch_script = os.path.join(PATCHES_DIR, f"{plat}.py")
    if not os.path.isfile(patch_script):
        progress.set_failed(arch)
        raise RuntimeError(f"No patch script for platform '{plat}' (expected {patch_script})")

    with tempfile.TemporaryDirectory() as tmpdir:
        dockerfile_path = os.path.join(tmpdir, "Dockerfile")
        with open(dockerfile_path, "w") as f:
            f.write(make_dockerfile(version, arch, plat))

        # Copy the platform patch script and LICENSE into the build context
        shutil.copy2(patch_script, os.path.join(tmpdir, "platform.py"))
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        shutil.copy2(
            os.path.join(repo_root, "LICENSE"),
            os.path.join(tmpdir, "LICENSE"),
        )

        # No --platform flag: always build on host (amd64).
        # Cross-compilation is handled by GN args + sysroot.
        gn_cpu = TARGETS[arch]["gn_cpu"]
        print(
            f"\n{'=' * 60}\n  Building PDFium for {plat}/{gn_cpu}  (arch={arch})\n{'=' * 60}\n",
            flush=True,
        )
        cmd = [
            "docker",
            "build",
            "--no-cache",
            "--progress=plain",
            "-t",
            image_tag,
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
            run(
                [
                    "docker",
                    "cp",
                    f"{container_name}:/staging",
                    staging_dest,
                ]
            )
        finally:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            subprocess.run(["docker", "rmi", "-f", image_tag], capture_output=True)

        # Create tarball with top-level directory name
        run(
            [
                "tar",
                "czf",
                output_path,
                "-C",
                extract_dir,
                dir_name,
            ]
        )

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
        run(
            [
                "gh",
                "release",
                "delete",
                tag,
                "-R",
                GITHUB_REPO,
                "--yes",
            ]
        )

    run(
        [
            "gh",
            "release",
            "create",
            tag,
            "-R",
            GITHUB_REPO,
            "--title",
            f"PDFium {branch}",
            "--notes",
            f"PDFium shared library built from source.\n\n"
            f"Source: https://pdfium.googlesource.com/pdfium/+/refs/heads/{branch}\n\n"
            "Build configuration:\n```\n"
            f"{GN_ARGS_COMMON.format(gn_cpu='<target>', extra_args='')}```",
            *built_files,
        ]
    )

    print(
        f"\nRelease: https://github.com/{GITHUB_REPO}/releases/tag/{tag}",
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
        nargs="+",
        default=None,
        help=(
            "target platform(s) — selects patch script(s) from patches/. "
            "Accepts multiple values (e.g. --platform linux musl). "
            "Default: linux + musl, so each release ships both a glibc and "
            "a musl variant. Pass --platform mac to build macOS alone."
        ),
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
    platforms = args.platform if args.platform else ["linux", "musl"]
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    built_files = []
    try:
        # Each platform pass gets its own BuildProgress so per-arch status
        # lines stay readable. Parallelism is within a platform (archs
        # fan out in parallel); platforms run sequentially so the terminal
        # UI doesn't have to juggle more than two or three rows at once.
        for plat in platforms:
            print(f"\n{'=' * 60}\n  Platform: {plat}\n{'=' * 60}\n", flush=True)
            progress = BuildProgress(args.version, archs, parallel=args.parallel)
            try:
                if args.parallel and len(archs) > 1:
                    with concurrent.futures.ThreadPoolExecutor(max_workers=len(archs)) as pool:
                        futures = {
                            pool.submit(
                                build_for_arch,
                                args.version,
                                arch,
                                plat,
                                output_dir,
                                progress,
                            ): arch
                            for arch in archs
                        }
                        for future in concurrent.futures.as_completed(futures):
                            built_files.append(future.result())
                else:
                    for arch in archs:
                        path = build_for_arch(args.version, arch, plat, output_dir, progress)
                        built_files.append(path)
            finally:
                progress.finish()

        # Sort so upload order is deterministic regardless of parallel completion order.
        built_files.sort()

        if args.upload:
            # Reuse a lightweight progress instance just to show the "Uploading..."
            # banner during the gh release create; no arch work runs here.
            upload_progress = BuildProgress(args.version, archs, parallel=False)
            try:
                upload_release(args.version, built_files, upload_progress)
            finally:
                upload_progress.finish()
    except (RuntimeError, subprocess.CalledProcessError) as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)

    print(f"\nBuilt {len(built_files)} binaries in {output_dir}/")


if __name__ == "__main__":
    main()
