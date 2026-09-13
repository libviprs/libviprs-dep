# libviprs-dep(1) — Build Tools Manual

A man-page-style reference for the libviprs-dep build tooling. For
narrative overview and download links, see
[`pdfium/README.md`](pdfium/README.md), [`zstd/README.md`](zstd/README.md)
and the [repo top-level README](README.md).

```
NAME          libviprs-dep-build — compile pre-built native dependencies for libviprs
SECTION       1 (User Commands)
UPDATED       2026-08-27
```

---

## NAME

**build_pdfium.py** — build PDFium shared libraries and static archives for
Linux (glibc), musl/Alpine, and macOS from source inside Docker, and
optionally publish them as GitHub Releases on `libviprs/libviprs-dep`.

**build_zstd.py** — the same, for the zstd compression library.

Everything from **OPTIONS** through **TROUBLESHOOTING** below describes
`build_pdfium.py`. zstd's driver shares the CLI shape and the archive
conventions but almost none of the machinery; see [**ZSTD**](#zstd) for
what it does differently and why it needs so much less.

## SYNOPSIS

```
python3 pdfium/build_pdfium.py VERSION
                               [--arch {amd64,arm64}]
                               [--platform PLATFORM [PLATFORM ...]]
                               [--parallel]
                               [--mem-per-build MB]
                               [--upload]
                               [--output-dir DIR]

python3 zstd/build_zstd.py [VERSION]
                           [--arch {amd64,arm64}]
                           [--platform PLATFORM [PLATFORM ...]]
                           [--parallel]
                           [--upload]
                           [--output-dir DIR]
```

`VERSION` is a PDFium chromium branch number (e.g. `7725`). It is
resolved to `origin/chromium/VERSION` at
`https://pdfium.googlesource.com/pdfium/`. For `build_zstd.py` it is an
upstream zstd release (e.g. `1.5.7`) and is optional — omitted, it comes
from `zstd/VERSION`.

## DESCRIPTION

`build_pdfium.py` orchestrates a reproducible PDFium build using only
Docker, Python, and (for uploads) the `gh` CLI. For each requested
`(platform, arch)` combination it:

1. Generates a platform-specific Dockerfile on the fly.
2. Builds an amd64 Docker image that runs the full compile inside the
   container.
3. Applies the **base** platform patch (`fpdfview.h` symbol-visibility
   fix, plus musl-specific toolchain setup where applicable — base mode
   does **not** rewrite `BUILD.gn`), writes `out/Static/args.gn` with
   `pdf_is_complete_lib = true`, and runs `ninja` against `out/Static`.
   The GN flag trips PDFium's own `BUILD.gn` branch that sets
   `static_component_type = "static_library"`, `complete_static_lib =
   true`, and strips `//build/config/compiler:thin_archive` from
   configs, so the pass emits a complete (fat) `libpdfium.a`.
4. Applies the **shared** patch on top (rewrites `component("pdfium")`
   → `shared_library("pdfium")` in `BUILD.gn`), writes
   `out/Shared/args.gn` *without* `pdf_is_complete_lib` (the flag is
   static-only), and runs `ninja` against `out/Shared`, producing
   `libpdfium.so`.
5. **Verifies** `libpdfium.a`: archive magic must be `!<arch>\n` (not
   `!<thin>\n`), archive must have ≥ 100 members and be ≥ 10 MB. Thin
   archives reference `.o` paths inside the build sandbox and would
   fail to link once extracted, so the Docker build fails here rather
   than publish a broken archive.
6. Stages both artifacts — along with the public C headers, the two
   `args.gn` files, and `LICENSE` — into a single directory.
7. Extracts the staging directory from the container and packages it as
   `pdfium-{platform}-{gn_cpu}.tgz` in the output directory.

The two-phase ninja build is the cleanest way to emit both a static
archive and a shared library from a single source checkout without
duplicating PDFium's large `component("pdfium")` target body inside
`BUILD.gn`.

### Supported platforms

| Platform | Output artifacts | Intended runtime |
| --- | --- | --- |
| `linux` | `libpdfium.so` + `libpdfium.a`, glibc-linked | Debian / Ubuntu / RHEL / mainstream distros |
| `musl`  | `libpdfium.so` + `libpdfium.a`, musl-linked  | Alpine, musl-based distroless images |
| `mac`   | `libpdfium.dylib` (requires macOS host) | macOS (Apple Silicon and x86_64) |

The default matrix is four archives:

| Platform | Arch | Archive |
| --- | --- | --- |
| linux | amd64 | `pdfium-linux-x64.tgz` |
| linux | arm64 | `pdfium-linux-arm64.tgz` |
| musl  | amd64 | `pdfium-musl-x64.tgz` |
| musl  | arm64 | `pdfium-musl-arm64.tgz` |

`mac` is intentionally excluded from the default matrix. PDFium's
`build/config/apple/sdk_info.py` invokes `xcodebuild` during `gn gen`
to query the macOS SDK version, and `xcodebuild` does not exist in the
Debian container used for glibc/musl builds. bblanchon/pdfium-binaries
works around this by running mac builds on actual `macos-15` GitHub
Actions runners rather than cross-compiling from Linux. The mac
Dockerfile generator is kept in `build_pdfium.py` for reference, but
opting into `--platform mac` on a Linux host fails at `gn gen` unless
you pre-provision an Xcode SDK and a stub `xcodebuild` inside the
container.

Intel Mac (`mac/amd64`) is also **not** in the default matrix — Apple
has shipped Apple Silicon exclusively for new Macs since 2020, so the
x86_64 dylib is rarely useful. Request it explicitly with
`--platform mac --arch amd64` when building on a macOS host.

The release workflow (`.github/workflows/release.yml`) ships both
per-arch mac archives — `pdfium-mac-arm64.tgz` and `pdfium-mac-x64.tgz`
— and, after both succeed, a `build-mac-universal` job runs `lipo
-create` over the two dylibs and uploads a third archive,
`pdfium-mac-univ.tgz`, containing a single fat Mach-O that loads on
either architecture. This fat-archive step has no `build_pdfium.py`
equivalent — it only exists as a CI post-processing job, mirroring
bblanchon/pdfium-binaries' `mac-univ.yml`.

Every compile runs inside an amd64 Linux container regardless of the
host's CPU arch. `build_pdfium.py` forces `--platform=linux/amd64` on
every `docker build` / `docker create` so Apple Silicon and Linux-arm64
hosts still run an amd64 container (via QEMU emulation) — this is
required because depot_tools ships amd64 Linux prebuilts for
`clang` / `gn` / `ninja` that don't execute natively under arm64.
Cross-compilation for the target arch happens inside the container via
GN args + sysroot; the Docker `--platform` flag only controls the
container's own CPU arch, not the target.

## OPTIONS

### Positional arguments

**`VERSION`**

:   PDFium chromium branch number, e.g. `7725`. Required.

### Optional arguments

**`--arch {amd64,arm64}`**

:   Build a single target architecture instead of both. Applies to every
    platform passed via `--platform`. Default: build both architectures.

**`--platform PLATFORM [PLATFORM ...]`**

:   One or more of `linux`, `musl`, `mac`. Defaults to the
    4-archive `{linux, musl} × {amd64, arm64}` matrix (see
    DESCRIPTION). Pass a single value (`--platform musl`) or several
    space-separated values (`--platform linux musl`). `--platform mac`
    requires a macOS host (see supported-platforms note above);
    pair it with `--arch amd64` to build an Intel Mac dylib.

**`--parallel`**

:   Fan out every `(platform, arch)` combo concurrently. With the
    default matrix this runs up to four Docker builds at once (one
    thread per combo); with `--platform linux` + `--arch amd64` it has
    no effect. In the terminal, press `Tab` or digits `1`–`4` to switch
    which build's live output is visible; the other builds continue in
    the background and replay on switch.

    Each parallel build reserves `--mem-per-build` MB from the Docker
    daemon's memory budget (read via `docker info`) before starting.
    Builds whose reservation would exceed the budget are held in a
    `queued — waiting for memory` state and launched as earlier builds
    finish, so a small Docker VM running multiple jobs degrades
    gracefully to serial execution instead of OOM-crashing. If
    `docker info` can't be read, gating is skipped with a one-line
    warning.

**`--mem-per-build MB`**

:   Pessimistic per-build memory estimate used by the `--parallel`
    scheduler. Default: `4096` (4 GiB), which comfortably covers
    PDFium's ninja link peak plus Docker overhead. Tune down if runs
    queue needlessly on a large host; tune up if you see OOM kills.
    Has no effect outside `--parallel` or when the default matrix has
    only one job.

**`--upload`**

:   Publish archives as assets on the GitHub Release tagged
    `pdfium-{VERSION}` on `libviprs/libviprs-dep`. If the release does
    not exist it is created; if it already exists, assets whose
    filenames match something newly built are **replaced** (via
    `gh release upload --clobber`) and any unrelated assets are
    **preserved**. This lets a partial re-run — e.g.
    `--platform musl --upload` after fixing a musl-only regression —
    update only the musl tarballs without touching the linux ones.

    The upload runs with **whatever archives successfully built**, even
    when other jobs in the same matrix failed. A flake on one arch
    therefore doesn't waste the 30-minute successful builds of the
    others; the good archives are published and the script exits `1`
    with a per-job failure summary so CI still treats the run as
    broken. If zero archives built, the upload is skipped entirely.

    Requires `gh` to be installed and authenticated (`gh auth login`).

**`--output-dir DIR`**

:   Where to write the `.tgz` archives. Default: `./bin`. Created if it
    does not already exist.

## INTERACTIVE CONTROLS

While a build is running in an interactive terminal, `build_pdfium.py`
reads single keypresses from stdin (via `termios` cbreak mode) without
needing `Enter`. The listener is active in both sequential and
`--parallel` modes.

| Key | Action |
| --- | --- |
| `Tab` | cycle the live-output view to the next job (parallel only) |
| `1`–`9` | switch the live-output view to the Nth job (parallel only) |
| `c` | cancel the currently-viewed job (parallel) or the running job (sequential) |
| `q` or `C` | cancel every job — running, extracting, and queued |

Cancelled jobs render as `⊘ cancelled` in the header, distinct from
`✗ failed`, so intentional stops are visually separated from real
errors. `cancel_all` also sets a sticky flag: any job still queued
behind the memory scheduler bails immediately when its turn would come
up, so `q` does not wait for slow jobs to finish before terminating
the whole run.

Cancellation sends `SIGTERM` to the `docker build` subprocess. If the
daemon survives but leaves orphan containers or images behind, run
`docker system prune` between runs.

## LOGS

Every job writes its full Docker build output to
`<output-dir>/logs/<plat>-<arch>.log` (so the default location is
`./bin/logs/linux-arm64.log`, `./bin/logs/mac-arm64.log`, …). The log
file is the authoritative post-mortem record when a build fails —
`--parallel` only keeps the last ~500 output lines per job in memory
for the in-terminal view switcher, but the log file on disk has every
line plus a header (version, start timestamp, image tag) and a footer
with the exit status and exception type.

On failure the script prints the log path to stderr so you can
`tail -n 200 bin/logs/linux-arm64.log` or open it in an editor without
hunting for it. The extraction and tarball-creation commands
(`docker create`, `docker cp`, `tar czf`) are also captured into the
same log file, so post-compile failures stay diagnosable.

Log files are not gitignored by path but `*.log` is — they're safe to
leave in place across runs. Each new invocation truncates its own
`<plat>-<arch>.log` rather than appending.

## FILES

```
pdfium/
├── build_pdfium.py            # entry point
├── build_mac_native.sh        # native mac build (called on Darwin hosts)
├── VERSION                    # chromium branch number the release workflow ships
├── bin/                       # default output directory (gitignored)
│   ├── pdfium-<plat>-<cpu>.tgz
│   └── logs/
│       └── <plat>-<arch>.log  # per-job Docker build log (overwritten each run)
├── patches/
│   ├── linux.py               # glibc linux patch script (accepts --mode)
│   ├── mac.py                 # macOS patch script (accepts --mode)
│   └── musl.py                # musl/Alpine patch script (accepts --mode)
└── tests/                     # pytest suite for pure-function logic

zstd/
├── build_zstd.py              # entry point
├── VERSION                    # zstd release the archives ship
├── bin/                       # default output directory (gitignored)
├── scripts/
│   └── verify_archive.sh      # invariant check run over every packaged .tgz
└── tests/                     # pytest suite, including the CI-coverage guards

.github/workflows/
├── build.yml                  # manual-dispatch build of an arbitrary chromium branch
├── ci.yml                     # lint + tests on every push; discovers its own
                               # paths, so a new dependency directory is covered
                               # without editing it
├── release.yml                # fires on push to `release`, fans out to
│                              # 4 ubuntu-latest + 1 macos-15 jobs, each
│                              # uploading to pdfium-<VERSION> via --upload
└── release-zstd.yml           # same trigger, zstd's own matrix: 4
                               # ubuntu-latest + 2 macos-15 jobs, each
                               # verifying before it uploads
```

`ci.yml` deliberately names no dependency directory: `ruff check .`,
a bare `pytest` (there is no `testpaths` in `pyproject.toml`), and
`git ls-files '*.sh' | xargs shellcheck`. `zstd/tests/test_ci_coverage.py`
fails if any of those three is narrowed back to a hardcoded path, which
is how `zstd/` would otherwise have landed entirely unchecked.

Each patch script is copied into the Docker build context as
`platform.py` before being invoked with `--mode base` (for the static
build) and `--mode shared` (for the shared build). See
[`pdfium/README.md`](pdfium/README.md) for the patch script details and
the GN args used.

## HOST REQUIREMENTS

The script runs on both macOS and Linux desktops — the heavy lifting
happens inside an amd64 Debian container, so the host only needs the
orchestration tools. Prerequisite checks run up-front (`check_dependencies`)
and emit OS-specific install hints when something is missing.

| Tool | Required when | macOS install | Linux install |
| --- | --- | --- | --- |
| Python 3.7+ | always | bundled / `brew install python` | distro package |
| Docker + buildx | always | Docker Desktop (`brew install --cask docker`) | Docker Engine — `curl -fsSL https://get.docker.com \| sh` + `sudo usermod -aG docker $USER` |
| `gh` CLI | `--upload` | `brew install gh` | distro repo (e.g. `sudo apt install gh` after adding gh apt repo) |
| `git` + `user.name`/`user.email` config | `--upload` | `brew install git` | `sudo apt install git` |

If `--upload` is passed, the prerequisite check also runs
`gh auth status` and verifies the authenticated account has write
access to `libviprs/libviprs-dep` via `gh repo view … --json
viewerPermission`. Accounts with only read access, or no access, fail
the preflight with an instruction pointing at `gh auth login` /
`gh auth switch`.

## ENVIRONMENT

**`PATH`**

:   Must include `docker`, `python3`, and — if `--upload` is passed —
    `gh` and `git`.

The build script does not itself consume any other environment variables.
Inside the Docker container, it sets and relies on `PATH`,
`DEPOT_TOOLS_UPDATE=0`, and (for `musl`) the musl-cross-make toolchain
prefix.

## EXIT STATUS

| Code | Meaning |
| --- | --- |
| `0` | Every requested build completed and, if `--upload` was passed, the release was created/updated. |
| `1` | At least one build failed, or a dependency check / `gh release` call failed. With `--upload`, archives from builds that *did* succeed are still uploaded before the script exits `1`; stderr lists which jobs failed and where their logs live. |
| `130` | Interrupted (SIGINT / Ctrl-C). |

## EXAMPLES

### Build the full default matrix

```bash
python3 pdfium/build_pdfium.py 7725
```

Produces `pdfium-linux-x64.tgz`, `pdfium-linux-arm64.tgz`,
`pdfium-musl-x64.tgz`, `pdfium-musl-arm64.tgz` in `./bin/`.

### Build only musl variants

```bash
python3 pdfium/build_pdfium.py 7725 --platform musl
```

### Build one combo for iterative debugging

```bash
python3 pdfium/build_pdfium.py 7725 --platform musl --arch arm64
```

### Parallel builds

```bash
python3 pdfium/build_pdfium.py 7725 --parallel
```

Fans out every `(platform, arch)` combo at once — with the default
matrix that's four concurrent Docker builds (`linux/amd64`,
`linux/arm64`, `musl/amd64`, `musl/arm64`). In the terminal, press
`Tab` or digits `1`–`4` to switch which build's live output is on
screen; `c` cancels the visible job and `q` cancels every job. On an
8-core machine with plenty of disk, wall time is roughly the slowest
single build rather than four back-to-back builds.

### Build and publish a release

```bash
python3 pdfium/build_pdfium.py 7725 --upload
```

Creates the `pdfium-7725` GitHub Release (if missing) and attaches all
four archives. If the release already exists, its assets are appended
or replaced in place — any unrelated assets on the release are
preserved. If one arch fails, the other three are still uploaded and
the script exits `1` with a failure summary.

### Re-run one platform and update only its assets

```bash
python3 pdfium/build_pdfium.py 7725 --platform musl --parallel --upload
```

Rebuilds only the musl archives and uploads them with `--clobber`,
leaving the existing `pdfium-linux-*.tgz` assets on the release
untouched. Useful after fixing a platform-specific regression.

### Run via GitHub Actions

Trigger the **Build PDFium** workflow (`.github/workflows/build.yml`) via
`workflow_dispatch`, supplying the chromium branch number. Tick
`upload=true` to have the workflow create/replace the GitHub Release
with all archives from the matrix.

### Cutting a release via the `release` branch

The **Release** workflow (`.github/workflows/release.yml`) is triggered
by a merge to `release`. It reads the chromium branch from
`pdfium/VERSION`, then fans out to one job per default-matrix entry:

- Four `ubuntu-latest` jobs — `{linux, musl} × {amd64, arm64}` — each
  calls `build_pdfium.py --platform X --arch Y --upload`.
- Two `macos-15` matrix jobs — `mac/arm64` and `mac/amd64` — each
  calls `build_pdfium.py --platform mac --arch Y --upload`;
  `build_pdfium.py` detects the Darwin host and dispatches to
  `pdfium/build_mac_native.sh` instead of its Docker path.
- One further `macos-15` job (`build-mac-universal`) runs after both
  per-arch mac builds succeed. It downloads `pdfium-mac-arm64.tgz` and
  `pdfium-mac-x64.tgz` from the just-published release, `lipo
  -create`s the two `libpdfium.dylib` files into a universal Mach-O,
  and uploads the result as `pdfium-mac-univ.tgz`. Matches the pattern
  in bblanchon/pdfium-binaries' `mac-univ.yml`.

A preceding `create-release` job ensures the `pdfium-<VERSION>` tag
exists before any build job uploads, so parallel `gh release upload
--clobber` calls don't race on `gh release create`. Authentication uses
the workflow's auto-minted `GITHUB_TOKEN` (exposed as `GH_TOKEN`); each
build job declares `permissions: contents: write`.

To publish a new version: open a PR that bumps `pdfium/VERSION` against
`release`, merge it, and the workflow takes over. A final `summary`
job posts the release URL and per-job statuses to the workflow run's
summary page.

### Cutting a zstd release

zstd has its own workflow, `.github/workflows/release-zstd.yml`, on
the same `release`-branch trigger plus `workflow_dispatch`. It is a
separate file because `release.yml` is pdfium-shaped throughout, and
because a zstd flake should not colour a pdfium release run red.

- `resolve-version` reads `zstd/VERSION` and then asks
  `build_zstd.source_sha256` for the pinned digest. A version with no
  `SOURCE_SHA256` entry fails here, before any build job starts, and
  fails red rather than being skipped.
- `create-release` creates `zstd-<VERSION>`, with notes from
  `build_zstd.release_notes` so a release cut by CI reads the same as
  one cut by a local `--upload`.
- Four `ubuntu-latest` jobs cover `{linux, musl} × {amd64, arm64}`.
  Each cell builds inside a container of the *target* architecture, so
  the arm64 pair registers QEMU first and builds under emulation.
- Two `macos-15` jobs build the mac slices natively. The runners are
  Apple Silicon; the x86_64 slice is selected with
  `CMAKE_OSX_ARCHITECTURES` and its smoke test links without running,
  which `build_zstd.py` already handles.
- Every job runs `zstd/scripts/verify_archive.sh` over its own archive
  between the build and the upload. `--upload` is deliberately not
  passed to the driver in CI: it would publish before the verifier ran.

No zstd release has been cut yet. Because the first publish of an
already-committed version changes no file, dispatch the workflow by
hand rather than pushing something to `release` to trigger it.

### Cutting an acadsharp release

acadsharp has its own workflow too, `.github/workflows/release-acadsharp.yml`,
on the same `release`-branch trigger plus `workflow_dispatch`. Same
reason as zstd: `release.yml` is pdfium-shaped throughout, and one
dependency's flake should not colour another's release run red.

- `resolve-version` reads `acadsharp/VERSION`, hands it to
  `build_acadsharp.split_version` so an empty or malformed file fails
  there, and asks `build_acadsharp.source_sha256` for the pinned digest
  of the upstream half. It then checks `acadsharp/native/global.json`
  names an `sdk.version` and proves that SDK installs, with
  `actions/setup-dotnet` reading the same file. A bad pin of any of
  those three kinds dies in this one job rather than on five claimed
  runners.
- `create-release` creates `acadsharp-<VERSION>`, not marked as a
  pre-release, so `gh release view` without a tag still resolves it the
  way it does for pdfium and zstd. It carries
  a notes preamble generated from `acadsharp/VERSION`,
  `acadsharp/native/global.json`, `acadsharp/include/viprs_acadsharp.h`
  and `acadsharp/build_acadsharp.py`. Nothing version-bearing is typed
  into the workflow, so bumping a pin cannot leave the release page
  stating the old one.
- Four container cells cover `{linux, musl} × {x64, arm64}`, and one
  `macos-15` job builds the mac slice with the pinned SDK installed on
  the runner. Each cell carries both words for its architecture, because
  there are two: the driver's CLI takes `--arch amd64|arm64` like
  `build_zstd.py` and `build_pdfium.py`, while the archive name and
  `verify_archive.sh`'s third argument take `x64|arm64`. The cell spells
  out both rather than leaning on the driver's `ARCH_ALIASES` to convert,
  so a cell that would build one target and upload another under a name
  the build never produced is something a test can see.
  Nothing is emulated: ADR 0001 measured a cross-architecture
  publish producing the object file and then failing at the native link,
  and the .NET runtime documents `qemu-user-static` as unsupported, so
  arm64 cells take `ubuntu-24.04-arm` instead. `fail-fast` is off.
- Every cell runs `acadsharp/scripts/verify_archive.sh` over its own
  archive, with the cell's platform and cpu passed in, between the build
  and the upload. `--upload` is deliberately not passed to the driver:
  it would publish before the verifier ran.
- `release-notes` runs whatever happened above. It downloads whatever
  actually reached the release, hashes each asset, reads
  `static_certified` and the Rust triple out of each archive's
  `metadata/LINKINFO.json`, and rewrites the release body with a row per
  archive. Every target that did not publish gets a line naming the job
  that should have produced it, because a release that quietly ships four
  of five is worse than one that ships four and says so.

No acadsharp release has been cut yet, and `acadsharp/README.md` says so
rather than linking archives that do not exist. Cut the first one by
dispatching the workflow by hand, for the same reason zstd's has to be
dispatched: publishing an already-committed version changes no file, so
there is nothing to push at `release`.

To try the whole thing without committing to a version, dispatch it with
the `version` input set to a throwaway (`3.7.1-viprs.0`, say). It needs a
`SOURCE_SHA256` entry for its upstream half like any other version, and
it publishes to `acadsharp-3.7.1-viprs.0`, which is then deleted with
`gh release delete acadsharp-3.7.1-viprs.0 --cleanup-tag`.

## ARTIFACT LAYOUT

Each `.tgz` extracts to a self-contained directory:

```
pdfium-<platform>-<gn_cpu>/
├── lib/
│   ├── libpdfium.so       # or libpdfium.dylib on mac
│   └── libpdfium.a        # static archive (not present on mac yet)
├── include/               # public C headers
├── args.gn                # GN args used for the shared build
├── args.static.gn         # GN args used for the static build
└── LICENSE                # PDFium's BSD-3-Clause license
```

`args.gn` and `args.static.gn` are kept separate so a consumer
investigating linker issues can see exactly which flags produced each
binary. They differ in one line: `args.static.gn` sets
`pdf_is_complete_lib = true`, which trips PDFium's own BUILD.gn branch
that selects `static_component_type = "static_library"`,
`complete_static_lib = true`, and drops the `thin_archive` config — so
the Static pass emits `libpdfium.a` at `out/Static/obj/libpdfium.a`.
`args.gn` (the Shared pass) omits that flag because the shared pass
works off a `BUILD.gn` rewrite applied by `--mode shared`
(`component("pdfium")` → `shared_library("pdfium")`, required because
`component()` resolves to `source_set` under `is_component_build=false`
and would not link a `.so`), emitting `libpdfium.so` at
`out/Shared/libpdfium.so`.

## CONSUMING THE ARTIFACTS

### Shared library (default for `pdfium-render`)

```bash
sudo cp pdfium-<plat>-<cpu>/lib/libpdfium.so /usr/local/lib/
sudo ldconfig
```

`pdfium-render`'s default `dlopen`-based path resolves the library via
the system loader, so placing it on `LD_LIBRARY_PATH` or in
`/usr/local/lib` is sufficient. In Rust:

```rust
Pdfium::bind_to_library(Pdfium::pdfium_platform_library_name_at_path("./"))
    .or_else(|_| Pdfium::bind_to_system_library())
```

### Static archive (for `pdfium-render/static`)

```bash
export PDFIUM_STATIC_LIB_PATH=/path/to/pdfium-<plat>-<cpu>/lib
cargo build --features pdfium-render/static
```

`pdfium-render`'s `static` feature links `libpdfium.a` at build time via
its `build.rs`, eliminating the `dlopen` step entirely. This is the
correct choice when the consuming binary is built for a fully-static
musl target (e.g. `x86_64-unknown-linux-musl` without
`target-feature=-crt-static`), since `dlopen` is unavailable in such
binaries.

### Libc compatibility matrix

| Binary libc | Needs `libpdfium.*` from | Notes |
| --- | --- | --- |
| glibc (Debian, Ubuntu, …) | `pdfium-linux-*` | |
| musl (Alpine, distroless musl) | `pdfium-musl-*` | Loading a glibc `.so` from a musl process fails at `dlopen` |
| macOS | `pdfium-mac-*` (not in default matrix) | Use `--platform mac` to build |

## ZSTD

`build_zstd.py` publishes the same shape of artifact as
`build_pdfium.py` — one archive per `(platform, arch)`, each carrying a
static archive *and* a shared library — from a library that needs almost
none of PDFium's machinery.

### How it differs from build_pdfium.py

| | `build_pdfium.py` | `build_zstd.py` |
| --- | --- | --- |
| Source | `gclient sync` of a Chromium-style checkout | one pinned release tarball, sha256 checked before anything compiles |
| Build system | GN + two `ninja` passes (static, then shared) | one CMake configure; `ZSTD_BUILD_STATIC` and `ZSTD_BUILD_SHARED` are both on, so both libraries come out of a single pass |
| Container arch | always amd64, cross-compiling to the target | pinned to the *target* arch, emulated when that is foreign |
| Patches | per-platform patch scripts | none — upstream builds as-is |
| Memory gating | `--mem-per-build` against the daemon's `MemTotal` | none; peak RSS is a few hundred MB |
| Wall time | 20–40 min per combo | 1–2 min native, ~5–10 min emulated |
| Version source | chromium branch, required on the CLI | `zstd/VERSION`, CLI argument optional |

Pinning the container to the target architecture is what buys most of
the simplicity: there is no cross toolchain, no sysroot, and the smoke
test at the end of the build *runs* the library it just produced instead
of merely linking it. PDFium can't do this — a QEMU-emulated 30-minute
Chromium build is not a trade anyone would take — but for a library this
size the emulation costs minutes.

### Options

**`VERSION`**

:   Upstream zstd release, e.g. `1.5.7`. Optional; defaults to the
    contents of `zstd/VERSION`. A version with no entry in
    `SOURCE_SHA256` (in `build_zstd.py`) is refused rather than
    downloaded unverified.

**`--platform {linux,musl,mac} [...]`**

:   Target platform(s). Default matrix is `{linux, musl} × {amd64,
    arm64}`. `mac` is excluded from the default because there is no
    macOS container image; it builds natively with CMake on a macOS
    host and needs no Docker at all.

**`--arch {amd64,arm64}`**, **`--parallel`**, **`--upload`**,
**`--output-dir DIR`**

:   As `build_pdfium.py`. `--arch` takes the same aliases
    (`x86_64`, `x64`, `aarch64`). There is no `--mem-per-build`.

### Artifact layout

```
zstd-<platform>-<cpu>/
├── lib/
│   ├── libzstd.a                 # static archive, position-independent
│   ├── libzstd.so -> libzstd.so.1.5.7        # .dylib chain on mac
│   ├── libzstd.so.1 -> libzstd.so.1.5.7
│   ├── libzstd.so.1.5.7
│   ├── pkgconfig/libzstd.pc      # prefix=${pcfiledir}/../..
│   └── cmake/zstd/*.cmake        # find_package(zstd) support
├── include/                      # zstd.h, zstd_errors.h, zdict.h
├── cmake-args.txt                # the CMake configure flags used
└── LICENSE                       # zstd's own BSD-3-Clause licence
```

`cmake-args.txt` is zstd's answer to `args.gn`: the exact flags that
produced the binaries, next to the binaries. The `LICENSE` here is
upstream zstd's, taken from the source tarball.

### Verification

`zstd/scripts/verify_archive.sh <tgz>` runs over the packaged tarball
and is called by `build_zstd.py` on every archive it produces — before
`--upload` can publish it, so verification isn't something only CI does.
It enforces:

1. One top-level directory, named to match the tarball, with the full
   layout above present.
2. `libzstd.a` has fat-archive magic (`!<arch>`, never `!<thin>`), is
   large enough to be real, holds at least ten objects, and carries a
   symbol index that **defines** `ZSTD_compress`, `ZSTD_decompress`,
   `ZSTD_versionNumber`, `ZSTD_createCCtx` and `ZDICT_trainFromBuffer`.
3. Every object inside `libzstd.a`, and the shared library, is built for
   the architecture the filename claims — read straight out of the ELF
   `e_machine` / Mach-O `cputype` fields, so an arm64 archive can be
   checked on an x64 runner with no cross binutils installed.
4. The shared library is `ET_DYN` / `MH_DYLIB` and exports the public
   API — not an executable, not a stray relocatable object.
5. `libzstd.pc` is relocatable and carries no build-machine paths.

Separately, the build itself compiles a compress/decompress round-trip
against the staged libraries and runs it, asserting
`ZSTD_versionString()` matches `VERSION`. That is the check only the
build host can make: verification proves the tarball is well-formed,
execution proves the code works.

### Consuming the artifacts

`zstd-sys` links a prebuilt library when built with its `pkg-config`
feature or with `ZSTD_SYS_USE_PKG_CONFIG=1` in the environment, and the
shipped `libzstd.pc` resolves relative to itself:

```bash
tar xzf zstd-linux-x64.tgz
export PKG_CONFIG_PATH="$PWD/zstd-linux-x64/lib/pkgconfig"
pkg-config --modversion libzstd     # 1.5.7
```

Linking the static archive by hand needs `-pthread`, because the library
is built with `ZSTD_MULTITHREAD_SUPPORT=ON` (matching distro packages
and zstd's own release binaries):

```bash
cc main.c -I zstd-linux-x64/include zstd-linux-x64/lib/libzstd.a -pthread -o main
```

### Examples

```bash
# Default matrix, version from zstd/VERSION
python3 zstd/build_zstd.py

# One combo, for iterating
python3 zstd/build_zstd.py --platform musl --arch arm64

# macOS, on a macOS host (no Docker involved)
python3 zstd/build_zstd.py --platform mac --arch arm64

# Everything at once, then publish what passed verification
python3 zstd/build_zstd.py --parallel --upload
```

### Troubleshooting

**`No pinned source hash for zstd <version>`**

:   `zstd/VERSION` was bumped without adding the new tarball's sha256 to
    `SOURCE_SHA256` in `build_zstd.py`. Add it; the build will not
    download a tarball it can't check.

**`exec format error` or a build that hangs on the first `RUN`**

:   The daemon has no binfmt handler for the target architecture. Docker
    Desktop ships one; on a bare Docker Engine install
    `docker run --privileged --rm tonistiigi/binfmt --install all` once.

**`smoke: linked zstd is X, expected Y`**

:   The staged headers and the staged library disagree, which means a
    stale build directory got reused. The build normally rebuilds from
    scratch (`--no-cache`); if you are iterating by hand, remove the
    workspace under `<output-dir>/workspace-*` first.

## ACADSHARP

`build_acadsharp.py` publishes one archive per `(platform, cpu)` like the
other two, but what is inside is different in kind: ACadSharp is a C#
library, and the artifact is a .NET NativeAOT shim exposing a
VIPRS-owned C ABI. A consumer links it with no .NET installed anywhere.

### How it differs from the other two

| | `build_zstd.py` | `build_acadsharp.py` |
| --- | --- | --- |
| Source | one pinned release tarball | a pinned *generated* GitHub tarball plus a pinned submodule commit, because `src/CSUtilities` is a submodule and generated tarballs never carry one |
| Build system | CMake | `dotnet publish -p:PublishAot=true`, with ILC linking through clang |
| Container arch | pinned to the target, emulated when foreign | pinned to the target, **never** emulated: .NET does not support QEMU and the SDK ships no cross toolchain, so a foreign cell needs a runner of that architecture |
| Base image | `debian:bookworm-slim` / `alpine:3.20` | the same two, with the SDK installed by `dotnet-install.sh`; there is no bookworm SDK image and the stock one is noble, whose output needs GLIBC_2.38 |
| Manifests | `cmake-args.txt` | `metadata/LINKINFO.json` and `metadata/BUILDINFO.json`, which `acadsharp-rs`'s `build.rs` parses |
| mac | builds natively with CMake | builds natively with the SDK; NativeAOT cannot cross-compile to macOS at all, so `macos-15` is the only way to produce that cell |

### The matrix, and it is five cells

| archive | .NET runtime identifier | Rust triple in `LINKINFO.json` |
| --- | --- | --- |
| `acadsharp-linux-x64.tgz` | `linux-x64` | `x86_64-unknown-linux-gnu` |
| `acadsharp-linux-arm64.tgz` | `linux-arm64` | `aarch64-unknown-linux-gnu` |
| `acadsharp-musl-x64.tgz` | `linux-musl-x64` | `x86_64-unknown-linux-musl` |
| `acadsharp-musl-arm64.tgz` | `linux-musl-arm64` | `aarch64-unknown-linux-musl` |
| `acadsharp-mac-arm64.tgz` | `osx-arm64` | `aarch64-apple-darwin` |

The default matrix is the first four. `mac` is reachable with `--platform
mac --arch arm64` on a macOS host and is excluded from the default for
the same reason it is in the other two drivers: there is no macOS
container image. There is no Windows cell and there will not be one.

### Options

**`--platform {linux,musl,mac} [...]`**, **`--arch {amd64,arm64}`**,
**`--parallel`**, **`--upload`**, **`--output-dir DIR`**

:   As `build_zstd.py`, including the `x86_64`/`x64`/`aarch64` aliases.

**`--target RID`**

:   A .NET runtime identifier, repeatable, as an alias for the cell.

**`--version VERSION`**

:   The artifact version to build, e.g. `3.7.1-viprs.1`. Defaults to the
    contents of `acadsharp/VERSION`, which is the usual case. The release
    workflow passes its `workflow_dispatch` override here, so the
    archives, the tag, the release notes and `artifact_version` in
    `LINKINFO.json` all agree on one number; without it a dispatch
    override tagged one version and shipped another. It also makes the
    throwaway pre-release G1.5 has to cut reachable without committing a
    version bump and reverting it. The version carries two numbers
    (upstream plus shim revision) and both reach the manifest.

**`--plan`**

:   Print the cells, their runtime identifiers, their triples and the
    `dotnet publish` commands, then stop. Runs no container.

### Artifact layout

```
acadsharp-<platform>-<cpu>/
├── lib/
│   ├── libacadsharp_native.so       # .dylib on mac
│   ├── libacadsharp_native.a        # only where the static smoke certified it
│   └── libacadsharp_native_init.a   # one object: the runtime's static initialiser
├── include/
│   └── viprs_acadsharp.h         # the frozen C ABI, byte-identical to the repo's
├── metadata/
│   ├── LINKINFO.json             # the consumer contract
│   ├── BUILDINFO.json            # what produced the binaries
│   └── CHECKSUMS.txt             # sha256 of every other file in the archive
├── LICENSES/
│   ├── ACadSharp-LICENSE         # MIT, Copyright (c) 2021 Albert Domenech
│   └── THIRD_PARTY_NOTICES       # the .NET runtime and every restored package
└── README.md
```

`LINKINFO.json` is the one to read. It carries the Rust triple, the ABI
and wire versions, the header's sha256 and its first eight bytes as the
fingerprint `viprs_acad_abi_fingerprint()` returns, and the link facts
measured on that target.

The two linking modes have separate fields, because they measure
different things and a consumer cannot tell which it is holding
otherwise:

| field | when | what |
| --- | --- | --- |
| `shared_system_libraries` | always | the shared library's own `NEEDED` list, libc and the loader dropped |
| `static_library` | certified only | the merged archive |
| `static_init_library` | certified only | one object, the runtime's static initialiser |
| `static_system_libraries` | certified only | what the static link needed |
| `static_link_args` | certified only | extra flags that link needed, normally empty |

The four `static_*` fields are present together when `static_certified`
is `true` and absent together when it is `false`. Absent, not empty: an
empty string is a path and an empty list is a measurement of none, and
neither means "not measured". `static_certified` is written by the driver
and only when the static smoke linked **and ran** on that target;
shared-only is a recorded outcome, not a failure.

Nothing puts `NativeAOT_StaticInitialization` in `static_link_args`. That
symbol does not exist in .NET 10: linking with `--require-defined` for it
fails outright, and without it the link succeeds and the binary matches
the JIT oracle. Nothing puts any other symbol-forcing flag there either,
for a different and larger reason, in the next section.

### Verification

`acadsharp/scripts/verify_archive.sh <tgz> [platform] [cpu]` runs over
the packaged tarball, and `build_acadsharp.py` runs it on every archive
it produces before calling one done. It enforces:

1. One top-level directory named after the tarball, with the full layout
   above present.
2. `CHECKSUMS.txt` covers every other file and every digest matches. A
   file nobody listed is as much a defect as a wrong digest.
3. Both manifests parse and carry every frozen field.
4. `abi_header_sha256` is the hash of the header shipped beside it, and
   `abi_fingerprint` is that hash's first eight bytes.
5. Every library is the architecture the filename claims, is the right
   kind of object, is not truncated, and **exports every entry point the
   shipped header declares**. The ELF and Mach-O symbol tables are walked
   out of the bytes rather than through `nm`, because GNU `nm` cannot
   read Mach-O, macOS `nm` cannot read ELF, and a check that skips itself
   when the tool cannot read the file is not a check.
6. A static archive is `!<arch>`, never a GNU thin one, and holds no two
   members of the same name. Duplicates link today only because the
   linker takes the first definition it finds, and they make
   `--whole-archive` on that file impossible.
7. `libacadsharp_native_init.a` holds exactly one small object and
   defines the initialiser the consumer's `--whole-archive` pulls in.
8. `static_certified: true` is held to an archive that links **and runs**
   here, whenever the host can build for that target, and to the cargo
   recipe above when the host has cargo. Linking alone cannot tell a
   working archive from a broken one: without its initialiser the link
   is clean and the binary aborts at the first call, which is exit 134.

It needs `python3`, which parses the manifests and the checksums; a
missing interpreter is a refusal rather than a skipped check.

Separately, the build itself compiles a program that `dlopen`s the staged
library, resolves every entry point by bare name and compares the live
`viprs_acad_abi_fingerprint()` against the header's hash. That is the
check only the build host can make.

### Consuming the artifacts

Shared, which is the easy one:

```bash
tar xzf acadsharp-linux-x64.tgz
cc main.c -I acadsharp-linux-x64/include \
   -L acadsharp-linux-x64/lib -lacadsharp_native -o main
```

Static needs the initialiser archive, whole and first:

```bash
cc main.c -I acadsharp-linux-x64/include \
   -Wl,--whole-archive acadsharp-linux-x64/lib/libacadsharp_native_init.a \
   -Wl,--no-whole-archive acadsharp-linux-x64/lib/libacadsharp_native.a \
   -lm -o main
```

`libacadsharp_native_init.a` holds one object: the runtime's static
initialiser, which lives in `.init_array` and **defines no symbol anyone
references**. Link it as an ordinary archive and the linker leaves it
out, the link succeeds, and the binary aborts on the first call into the
library. Put it after the main archive and the link fails on
`RhRegisterOSModule`.

### Consuming from Rust

Read `metadata/LINKINFO.json` rather than guessing: match its `target`
against `TARGET`, check `abi_version`, `wire_version` and
`abi_fingerprint` against the generated bindings, then, from a `-sys`
crate's build script:

```
cargo:rustc-link-search=native=<archive>/lib
cargo:rustc-link-lib=static:-bundle,+whole-archive=acadsharp_native_init
cargo:rustc-link-lib=static:-bundle=acadsharp_native
cargo:rustc-link-lib=<each static_system_libraries entry>
```

in that order, or, for the shared library, `cargo:rustc-link-lib=
acadsharp_native` plus one `cargo:rustc-link-lib` per
`shared_system_libraries` entry.

All three modifiers are load-bearing. `+whole-archive` on the init
archive because nothing references what is in it. `-bundle` on **both**,
because with the default `+bundle` rustc packs a static native library
into the `-sys` crate's rlib, and that rlib lands on the link line before
the whole-archived init archive: the linker reads left to right, has no
reason to pull the runtime object defining `RhRegisterOSModule` while it
is at the rlib, and cannot go back for it afterwards. And the init
archive first, because it is the one with the dangling references.

**Do not express any of this as `cargo:rustc-link-arg`.** Cargo does not
treat the directives alike. `rustc-link-search` and `rustc-link-lib`
travel to the link line of everything that depends on the emitting
crate; `rustc-link-arg` binds to that package's own targets and goes no
further. A requirement written as an argument therefore reaches the
`-sys` crate's own tests and examples, stays green there, and is missing
from every binary that depends on it. That is why the initialiser ships
as `static_init_library` and is pulled in with `static:+whole-archive`
rather than forced with `-Wl,-u,<symbol>`, and why `static_link_args` is
normally empty: anything in it has to be applied by the final binary's
own package, not by a dependency.

`acadsharp/scripts/link_consumer_smoke.sh <unpacked-archive>` runs
exactly that recipe through a two-crate workspace and executes the
binary, and `acadsharp/scripts/verify_archive.sh` runs it too when the
host can build for the archive's target.

### Examples

```bash
# The four container cells, sequentially
python3 acadsharp/build_acadsharp.py

# One cell, for iterating
python3 acadsharp/build_acadsharp.py --platform musl --arch arm64

# macOS, on a macOS host (no Docker involved)
python3 acadsharp/build_acadsharp.py --platform mac --arch arm64

# What it would run, without running it
python3 acadsharp/build_acadsharp.py --plan
```

### Troubleshooting

**`no sha256 for ACadSharp <version>`** or **`no upstream commit`**

:   `acadsharp/VERSION` was bumped without adding the tarball digest to
    `SOURCE_SHA256` or the tag's commit to `SOURCE_COMMIT` in
    `build_acadsharp.py`. `acadsharp_commit` is a frozen manifest field,
    so a tag with no commit cannot be packaged at all.

**`sha256 mismatch` on a tag that has not moved**

:   `SOURCE_SHA256` pins a GitHub *generated* tarball, and GitHub does
    not promise those stay byte-for-byte stable forever. The fix is to
    vendor the tree, not to relax the check.

**`<projitems> is missing. src/CSUtilities is a git submodule`**

:   The source tree is the tarball without the submodule.
    `ACadSharp.csproj` imports `CSMath.projitems` during evaluation, so
    the failure lands before restore and never mentions submodules.

**`the ABI smoke failed against the staged library`**

:   The shim does not export every entry point `viprs_acadsharp.h`
    declares. The message names the missing ones. The archive is still
    written, for inspection; it is not shippable.

**`unrecognised emulation mode: aarch64linux` during the publish**

:   An x64 container was asked to publish for arm64. ILC produces the
    object file and the native link then fails, because the SDK image
    ships no cross binutils or sysroot. Run the cell on a runner of its
    own architecture.

## TROUBLESHOOTING

### `DlOpen { desc: "Dynamic loading not supported" }` from `pdfium-render`

Your Rust binary is built as a fully static musl executable
(`-C target-feature=+crt-static`), which has no dynamic linker mapped
in and therefore cannot `dlopen`. Two fixes:

1. **Preferred**: switch to `pdfium-render`'s `static` feature and
   point `PDFIUM_STATIC_LIB_PATH` at the directory containing
   `libpdfium.a` from the matching `pdfium-musl-<cpu>.tgz`.
2. **Alternative**: build your Rust binary with
   `-C target-feature=-crt-static` so it is a dynamic musl executable,
   then use the matching `libpdfium.so` from `pdfium-musl-<cpu>.tgz`.

### `libpdfium.so: Error loading shared library: No such file or directory`

The `.so` is not on the loader's search path. Copy it to
`/usr/local/lib` and run `ldconfig`, or set `LD_LIBRARY_PATH` to the
directory containing it.

### `undefined reference to FPDF_*` when static-linking

You are using `libpdfium.a` from an older release that predates the
`FPDF_EXPORT` visibility patch. Upgrade to `pdfium-7725` or newer. The
visibility patch is applied unconditionally under `--mode base`.

### `libpdfium.a: archive has no index; run ranlib to add one` (or tiny `.a`)

You're using a `libpdfium.a` from an early release that shipped a GNU
thin archive — it only stored `.o` path references, not the object
code, so once the Docker build sandbox was gone the archive was
unlinkable. Upgrade to a release built after the `complete_static_lib`
fix; the first 8 bytes of the archive must read `!<arch>\n`, never
`!<thin>\n`. Current releases enforce this during the Docker build
(archive magic + `ar t` member count + size floor) and will fail the
build rather than publish a broken archive.

### Docker buildkit steps fail with "no space left on device"

A full default matrix build produces ~30 GB of Docker image layers
before cleanup. Ensure the Docker daemon has at least that much free
space, or run `docker system prune` between builds.

### `ls: cannot access 'out/Static/libpdfium.a'` at the verify step

The verify step looks at `out/Static/obj/libpdfium.a`, not
`out/Static/libpdfium.a` — GN's `static_library` template writes its
archive into the `obj/` subtree. If you are patching the Dockerfile by
hand and see this error, update both the verify step
(`ls -lh out/Static/obj/libpdfium.a`) and the staging copy
(`cp out/Static/obj/libpdfium.a /staging/lib/`) to the `obj/` path.

### `libpdfium.a` is 8 bytes (empty `ar` archive)

GN's `static_library` only archives objects the target *directly*
owns; PDFium's `pdfium` target is an umbrella with many `deps` and
almost no direct `sources`, so a naive `component() → static_library`
rewrite produces an archive containing just the `!<arch>\n` magic
header. The fix is to let PDFium's own `BUILD.gn` handle the static
wiring: the Static pass writes `pdf_is_complete_lib = true` into
`out/Static/args.gn`, which trips the branch in `pdfium/BUILD.gn` that
sets `static_component_type = "static_library"`, `complete_static_lib
= true`, and strips `//build/config/compiler:thin_archive` from
configs. If you forked the build script, make sure the Static
`args.gn` carries that flag — and that the Shared `args.gn` does
**not**, since the shared pass rewrites the target to
`shared_library("pdfium")` and GN rejects `complete_static_lib` on
non-static targets.

### `ERROR at //build/config/sysroot.gni:60:7: Assertion failed` (musl)

GN asserts `path_exists(sysroot)` resolves to an existing directory,
but the musl Dockerfile deliberately skips
`build/linux/sysroot_scripts/install-sysroot.py` — the musl build uses
the sysroot bundled with musl-cross-make, not the Debian sysroot. The
musl GN args now include `use_sysroot = false` to tell Chromium's
build config to skip the Debian sysroot lookup entirely. Affects
`musl/arm64` in particular, because Chromium only auto-downloads the
amd64 sysroot via hooks; arm64 requires the explicit install step
that musl skips.

### `FileNotFoundError: No such file or directory: 'xcodebuild'` during `gn gen`

You attempted `--platform mac` on a Linux host. PDFium's
`build/config/apple/sdk_info.py` calls `xcodebuild -version` to
populate the mac SDK variables, and `xcodebuild` does not exist
inside the Debian container. Either build mac on a macOS host, or
pre-provision an Xcode SDK plus a stub `xcodebuild` in the Dockerfile
before the `gn gen` step.

### `lockfile.LockError: Errno 11 EAGAIN` during `gclient sync`

gclient's internal parallel workers race on the gsutil bundle bootstrap
flock. The Dockerfile mitigates this by running
`python3 /opt/depot_tools/gsutil.py --version` before `gclient sync`
so the bundle download completes single-threaded. If the error
returns, re-run the affected job — the race is non-deterministic and
the retry usually succeeds.

### `Could not resolve host: chromium.googlesource.com` (or `musl.cc`)

Transient DNS failures inside the Docker VM, typically when several
containers start in parallel. The Dockerfiles wrap the relevant
fetches in retry loops (5 attempts × 10 s backoff for `git clone
depot_tools`; `curl --retry 5 --retry-delay 10 --retry-all-errors`
for `musl.cc`). If all retries fail, check the Docker VM's DNS
configuration (`docker info | grep -i dns`) or drop parallelism for
the affected run.

### Repeated `subprocess ... git fetch ... failed; will retry after a short nap` from gclient, or `read udp ... i/o timeout` looking up `storage.googleapis.com`

These are not rate-limits from Google — Google's infrastructure
handles this traffic trivially. The bottleneck is Docker Desktop's
built-in DNS forwarder (typically at `192.168.65.7:53`), which is a
minimal userspace resolver not designed for hundreds of concurrent
lookups. `gclient sync` defaults its worker pool to `cpu_count()` —
on a 28-core host, four parallel containers × 28 workers = ~112
simultaneous git fetches + DNS queries per container, which
saturates the forwarder.

The Dockerfiles cap per-container parallelism with
`gclient sync ... --jobs=8`, putting the full matrix at ~32
concurrent requests — well within any DNS resolver's capacity. If
you still see timeouts, either:

- **Give the Docker daemon a real DNS resolver**: Docker Desktop →
  Settings → Resources → Network → set DNS to `8.8.8.8, 1.1.1.1`.
- **Lower the matrix's parallelism**: drop `--parallel`, or re-run
  with `--platform linux` then `--platform musl` sequentially.

### Docker build fails with `rpc error: code = Unavailable ... EOF`

The BuildKit daemon inside the Docker VM disconnected mid-build —
almost always an OOM kill. Raise the Docker VM's memory limit (Docker
Desktop → Settings → Resources → Memory) or drop `--parallel`. On a
7.5 GiB VM, five simultaneous PDFium builds exhaust RAM during the
depot_tools bootstrap; 16 GiB+ is the practical floor for a full
parallel matrix.

## SEE ALSO

- [`pdfium/README.md`](pdfium/README.md) — build pipeline overview and download links
- [`zstd/README.md`](zstd/README.md) — zstd build pipeline, version pin rationale, pkg-config consumption
- [`README.md`](README.md) — repo top-level
- [`.github/workflows/build.yml`](.github/workflows/build.yml) — CI workflow definition
- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — lint + test workflow
- [bblanchon/pdfium-binaries](https://github.com/bblanchon/pdfium-binaries) — upstream reference build scripts
- [pdfium-render (Rust)](https://github.com/ajrcarey/pdfium-render) — consumer of the shared/static libraries
- [PDFium source](https://pdfium.googlesource.com/pdfium/) — upstream project

## HISTORY

- **zstd-1.5.7** (2026-08) — second dependency in the repo. Publishes `libzstd.a` + `libzstd.so`/`.dylib` per platform with a relocatable `libzstd.pc`, built in a container pinned to the target architecture rather than cross-compiled. CI stopped hardcoding `pdfium/` in the same cycle, so dependency directories are discovered rather than listed.
- **pdfium-7725** (2026-04) — first release to ship both `libpdfium.so` and `libpdfium.a` per archive, and to include musl-linked variants (`pdfium-musl-x64.tgz`, `pdfium-musl-arm64.tgz`) in the default matrix. Interactive cancellation (`c` / `q`), retry-wrapped network steps, `--upload` append/replace semantics, partial-failure uploads, `complete_static_lib = true` for a non-empty `libpdfium.a`, `use_sysroot = false` for musl, and `gclient sync --jobs=8` to stop saturating Docker Desktop's DNS forwarder all landed in the same cycle. `mac` was removed from the default matrix after bblanchon/pdfium-binaries confirmed that mac builds require a macOS host.
- **pdfium earlier** — glibc-only shared library releases.

## LICENSE

The build tooling is released under [MIT](LICENSE). PDFium itself is
released under BSD-3-Clause; the bundled `LICENSE` file inside each
archive is PDFium's, not this repo's.
