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
3. Applies the **base** platform patch (symbol-visibility fixes, plus
   musl-specific toolchain setup where applicable) and runs `ninja`
   against `out/Static`, producing `libpdfium.a`.
4. Applies the **shared** patch on top (rewrites
   `component("pdfium")` → `shared_library("pdfium")`) and runs `ninja`
   against `out/Shared`, producing `libpdfium.so`.
5. Stages both artifacts — along with the public C headers, the two
   `args.gn` files, and `LICENSE` — into a single directory.
6. Extracts the staging directory from the container and packages it as
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

:   After a successful build, publish the archives as assets on the
    GitHub Release tagged `pdfium-{VERSION}` on
    `libviprs/libviprs-dep`. If the release does not exist it is
    created; if it already exists, assets whose filenames match
    something newly built are **replaced** (via
    `gh release upload --clobber`) and any unrelated assets are
    **preserved**. This lets a partial re-run — e.g.
    `--platform musl --upload` after fixing a musl-only regression —
    update only the musl tarballs without touching the linux ones.
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
└── build.yml                  # CI workflow: builds the full matrix on dispatch
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
| `0` | All requested builds completed and, if `--upload` was passed, the release was created. |
| `1` | A dependency check failed, a Docker build failed, or the `gh release create` call failed. |
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
preserved.

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
binary. They differ only in the `BUILD.gn` target type that the
matching patch mode selects: `static_library("pdfium")` under
`--mode base` (emitting `libpdfium.a` at `out/Static/obj/libpdfium.a`),
then `shared_library("pdfium")` under `--mode shared` (emitting
`libpdfium.so` at `out/Shared/libpdfium.so`). The patch must rewrite
`component()` explicitly because `component()` resolves to `source_set`
under `is_component_build=false`, which groups objects but does not
link a `.a`.

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

- **pdfium-7725** (2026-04) — first release to ship both `libpdfium.so` and `libpdfium.a` per archive, and to include musl-linked variants (`pdfium-musl-x64.tgz`, `pdfium-musl-arm64.tgz`) in the default matrix. Interactive cancellation (`c` / `q`), retry-wrapped network steps, and `--upload` append/replace semantics landed in the same cycle. `mac` was removed from the default matrix after bblanchon/pdfium-binaries confirmed that mac builds require a macOS host.
- **pdfium earlier** — glibc-only shared library releases.

## LICENSE

The build tooling is released under [MIT](LICENSE). PDFium itself is
released under BSD-3-Clause; the bundled `LICENSE` file inside each
archive is PDFium's, not this repo's.
