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
| `mac`   | `libpdfium.dylib` (single-phase today) | macOS (Apple Silicon and x86_64) |

The default matrix is five archives:

| Platform | Arch | Archive |
| --- | --- | --- |
| linux | amd64 | `pdfium-linux-x64.tgz` |
| linux | arm64 | `pdfium-linux-arm64.tgz` |
| mac   | arm64 | `pdfium-mac-arm64.tgz` (Apple Silicon) |
| musl  | amd64 | `pdfium-musl-x64.tgz` |
| musl  | arm64 | `pdfium-musl-arm64.tgz` |

Intel Mac (`mac/amd64`) is **not** in the default matrix — Apple has
shipped Apple Silicon exclusively for new Macs since 2020, so the
x86_64 dylib is rarely useful. Request it explicitly with
`--platform mac --arch amd64` if you need it.

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

:   One or more of `linux`, `musl`, `mac`. Defaults to the full
    5-archive matrix (see DESCRIPTION). Pass a single value
    (`--platform musl`) or several space-separated values
    (`--platform linux musl`). `--platform mac` alone produces only
    `mac/arm64`; pair it with `--arch amd64` to build an Intel Mac
    dylib.

**`--parallel`**

:   Fan out every `(platform, arch)` combo concurrently. With the
    default matrix this runs four Docker builds at once (one thread per
    combo); with `--platform linux` + `--arch amd64` it has no effect.
    In the terminal, press `Tab` or digits `1`–`4` to switch which
    build's live output is visible; the other builds continue in the
    background and replay on switch.

**`--upload`**

:   After a successful build, call `gh release create` to publish the
    archives as a GitHub Release tagged `pdfium-{VERSION}` on
    `libviprs/libviprs-dep`. If a release with that tag already exists
    it is deleted and replaced. Requires `gh` to be installed and
    authenticated (`gh auth login`).

**`--output-dir DIR`**

:   Where to write the `.tgz` archives. Default: `./bin`. Created if it
    does not already exist.

## FILES

```
pdfium/
├── build_pdfium.py            # entry point
├── bin/                       # default output directory (gitignored)
│   └── pdfium-<plat>-<cpu>.tgz
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
`pdfium-mac-arm64.tgz`, `pdfium-musl-x64.tgz`, `pdfium-musl-arm64.tgz`
in `./bin/`.

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
matrix that's five concurrent Docker builds (`linux/amd64`,
`linux/arm64`, `mac/arm64`, `musl/amd64`, `musl/arm64`). In the
terminal, press `Tab` or digits `1`–`5` to switch which build's live
output is on screen. On an 8-core machine with plenty of disk, wall
time is roughly the slowest single build rather than five back-to-back
builds.

### Build and publish a release

```bash
python3 pdfium/build_pdfium.py 7725 --upload
```

Equivalent to a full build followed by `gh release create pdfium-7725`
with all four archives attached. The existing release (if any) is
replaced in place.

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
matching patch mode selects.

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

## SEE ALSO

- [`pdfium/README.md`](pdfium/README.md) — build pipeline overview and download links
- [`README.md`](README.md) — repo top-level
- [`.github/workflows/build.yml`](.github/workflows/build.yml) — CI workflow definition
- [`.github/workflows/ci.yml`](.github/workflows/ci.yml) — lint + test workflow
- [bblanchon/pdfium-binaries](https://github.com/bblanchon/pdfium-binaries) — upstream reference build scripts
- [pdfium-render (Rust)](https://github.com/ajrcarey/pdfium-render) — consumer of the shared/static libraries
- [PDFium source](https://pdfium.googlesource.com/pdfium/) — upstream project

## HISTORY

- **pdfium-7725** (2026-04) — first release to ship both `libpdfium.so` and `libpdfium.a` per archive, and to include musl-linked variants (`pdfium-musl-x64.tgz`, `pdfium-musl-arm64.tgz`) in the default matrix.
- **pdfium earlier** — glibc-only shared library releases.

## LICENSE

The build tooling is released under [MIT](LICENSE). PDFium itself is
released under BSD-3-Clause; the bundled `LICENSE` file inside each
archive is PDFium's, not this repo's.
