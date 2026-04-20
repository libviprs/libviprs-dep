# libviprs-dep(1) — Build Tools Manual

A man-page-style reference for the libviprs-dep build tooling. For
narrative overview and download links, see
[`pdfium/README.md`](pdfium/README.md) and the
[repo top-level README](README.md).

```
NAME          libviprs-dep-build — compile pre-built native dependencies for libviprs
SECTION       1 (User Commands)
UPDATED       2026-04-20
```

---

## NAME

**build_pdfium.py** — build PDFium shared libraries and static archives for
Linux (glibc), musl/Alpine, and macOS from source inside Docker, and
optionally publish them as GitHub Releases on `libviprs/libviprs-dep`.

## SYNOPSIS

```
python3 pdfium/build_pdfium.py VERSION
                               [--arch {amd64,arm64}]
                               [--platform PLATFORM [PLATFORM ...]]
                               [--parallel]
                               [--mem-per-build MB]
                               [--upload]
                               [--output-dir DIR]
```

`VERSION` is a PDFium chromium branch number (e.g. `7725`). It is
resolved to `origin/chromium/VERSION` at
`https://pdfium.googlesource.com/pdfium/`.

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

.github/workflows/
├── build.yml                  # manual-dispatch build of an arbitrary chromium branch
├── ci.yml                     # lint + tests on PRs / pushes to main
└── release.yml                # fires on push to `release`, fans out to
                               # 4 ubuntu-latest + 1 macos-15 jobs, each
                               # uploading to pdfium-<VERSION> via --upload
```

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
- One `macos-15` job runs the same command with `--platform mac
  --arch arm64`; `build_pdfium.py` detects the Darwin host and dispatches
  to `pdfium/build_mac_native.sh` instead of its Docker path.

A preceding `create-release` job ensures the `pdfium-<VERSION>` tag
exists before any build job uploads, so parallel `gh release upload
--clobber` calls don't race on `gh release create`. Authentication uses
the workflow's auto-minted `GITHUB_TOKEN` (exposed as `GH_TOKEN`); each
build job declares `permissions: contents: write`.

To publish a new version: open a PR that bumps `pdfium/VERSION` against
`release`, merge it, and the workflow takes over. A final `summary`
job posts the release URL and per-job statuses to the workflow run's
summary page.

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
- [`README.md`](README.md) — repo top-level
- [`.github/workflows/build.yml`](.github/workflows/build.yml) — CI workflow definition
- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — lint + test workflow
- [bblanchon/pdfium-binaries](https://github.com/bblanchon/pdfium-binaries) — upstream reference build scripts
- [pdfium-render (Rust)](https://github.com/ajrcarey/pdfium-render) — consumer of the shared/static libraries
- [PDFium source](https://pdfium.googlesource.com/pdfium/) — upstream project

## HISTORY

- **pdfium-7725** (2026-04) — first release to ship both `libpdfium.so` and `libpdfium.a` per archive, and to include musl-linked variants (`pdfium-musl-x64.tgz`, `pdfium-musl-arm64.tgz`) in the default matrix. Interactive cancellation (`c` / `q`), retry-wrapped network steps, `--upload` append/replace semantics, partial-failure uploads, `complete_static_lib = true` for a non-empty `libpdfium.a`, `use_sysroot = false` for musl, and `gclient sync --jobs=8` to stop saturating Docker Desktop's DNS forwarder all landed in the same cycle. `mac` was removed from the default matrix after bblanchon/pdfium-binaries confirmed that mac builds require a macOS host.
- **pdfium earlier** — glibc-only shared library releases.

## LICENSE

The build tooling is released under [MIT](LICENSE). PDFium itself is
released under BSD-3-Clause; the bundled `LICENSE` file inside each
archive is PDFium's, not this repo's.
