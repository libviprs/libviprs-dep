# libviprs-dep

Pre-compiled native dependencies for [libviprs](https://github.com/libviprs/libviprs). Each dependency has its own directory with build scripts and documentation. Compiled binaries are published as GitHub Releases.

PDFium consumers and CLI flags that exercise it are documented at https://libviprs.org/cli/#flag-render. The matching CLI flags — [`--render`](https://libviprs.org/cli/#flag-render), [`--dpi`](https://libviprs.org/cli/#flag-dpi), [`--page`](https://libviprs.org/cli/#flag-page), and [`--match-page-size`](https://libviprs.org/cli/#flag-match-page-size) — are what load the binaries shipped here at runtime, gated by the `pdfium` and `pdfium-static` Cargo features.

For a full man-page-style reference on the build tooling, see [`MANUAL.md`](MANUAL.md).

## Dependencies

| Directory | Library | Purpose |
| --- | --- | --- |
| [`pdfium/`](pdfium/) | [PDFium](https://pdfium.googlesource.com/pdfium/) | PDF page rasterization |
| [`zstd/`](zstd/) | [zstd](https://github.com/facebook/zstd) | Packfile compression, so `--features packfile` stops compiling C |

## Release contents

Every archive published under [Releases](https://github.com/libviprs/libviprs-dep/releases) ships both a shared library and a static archive so downstream consumers can pick either linking strategy. Archives are named `<library>-<platform>-<cpu>.tgz` and unpack to a directory of the same name.

```
pdfium-<platform>-<cpu>/
├── lib/libpdfium.so   # or .dylib on mac — for dlopen / dynamic linking
├── lib/libpdfium.a    # static archive for pdfium-render/static
├── include/           # public C headers
├── args.gn            # GN args used for the shared build
├── args.static.gn     # GN args used for the static build
└── LICENSE
```

```
zstd-<platform>-<cpu>/
├── lib/libzstd.so             # or .dylib on mac, with the usual soname symlinks
├── lib/libzstd.a              # static archive, position-independent
├── lib/pkgconfig/libzstd.pc   # relocatable — prefix is ${pcfiledir}/../..
├── lib/cmake/zstd/            # CMake package config for find_package(zstd)
├── include/                   # zstd.h, zstd_errors.h, zdict.h
├── cmake-args.txt             # CMake configure flags used
└── LICENSE
```

The default in-process matrix (`build_pdfium.py` on a Linux host) is `{linux, musl} × {amd64, arm64}` — four archives. The release workflow additionally produces three macOS archives on `macos-15` runners: `pdfium-mac-arm64.tgz`, `pdfium-mac-x64.tgz`, and `pdfium-mac-univ.tgz` (a universal Mach-O built via `lipo -create` over the two per-arch dylibs). Pick the `linux-*` archives for glibc runtimes (Debian, Ubuntu, …), the `musl-*` archives for musl runtimes (Alpine, musl-based distroless images), and one of the `mac-*` archives for macOS (`mac-univ` if you want a single binary that loads on both Apple Silicon and Intel). Loading a glibc `.so` from a musl process — or vice versa — fails at `dlopen` time. macOS is intentionally excluded from `build_pdfium.py`'s in-process default matrix because PDFium's GN config invokes `xcodebuild` during `gn gen`, which doesn't exist on Linux, so mac builds require an actual macOS host (bblanchon/pdfium-binaries runs mac builds on `macos-15` GitHub Actions runners for the same reason).

zstd's matrix is the same four Linux archives — `{linux, musl} × {amd64, arm64}` — plus `zstd-mac-arm64.tgz` and `zstd-mac-x64.tgz` from a macOS host. It needs no Chromium toolchain: each combo builds in a container pinned to the target architecture, so a foreign-arch build is emulated rather than cross-compiled, and the build's own smoke test runs the library it just produced.

See [`pdfium/README.md`](pdfium/README.md#download) and [`zstd/README.md`](zstd/README.md#download) for direct download URLs and consumption examples.

## Quickstart

Build the full default matrix for chromium branch 7725 and publish it as a release:

```bash
python3 pdfium/build_pdfium.py 7725 --parallel --upload
```

`--parallel` fans out every `(platform, arch)` combo (4 by default) at once, gated by a memory scheduler that reads the Docker daemon's `MemTotal` and queues over-budget combos until earlier ones finish. Tune with `--mem-per-build MB` (default `4096`) if you see builds queuing unnecessarily on a large host or want extra safety margin on a small one. Each container caps its internal `gclient sync` worker pool at 8 so four concurrent builds stay under Docker Desktop's DNS forwarder limits (see `MANUAL.md` troubleshooting for details).

While the build is running, the terminal header accepts these keys:

| Key | Action |
| --- | --- |
| `Tab` / `1`–`9` | switch which build's live output is visible (parallel only) |
| `c` | cancel the currently-viewed job |
| `q` or `C` | cancel every job — running and queued |

Every job streams its full Docker build output to `pdfium/bin/logs/<plat>-<arch>.log`. If a job fails, the script prints the log path to stderr — `tail -n 200 pdfium/bin/logs/linux-arm64.log` gives you the authoritative post-mortem, since the in-terminal view only retains the last ~500 lines per job.

Partial failures don't lose the run. When `--upload` is passed, the archives from builds that *did* succeed are still attached to the GitHub Release (via `gh release upload --clobber`) before the script exits `1` with a per-job failure summary — so one flake never wastes a 30-minute successful build of the other three archives.

Or trigger the **Build PDFium** GitHub Actions workflow via `workflow_dispatch`, entering the chromium branch number and toggling `upload=true`.

zstd is a much smaller job — no chromium branch to pick, and the version comes from `zstd/VERSION`:

```bash
python3 zstd/build_zstd.py --parallel --upload
```

Every archive is put through `zstd/scripts/verify_archive.sh` before `--upload` publishes anything, so a malformed tarball can't reach a release even from a local run.

## Cutting a release

`pdfium/VERSION` is the single source of truth for the chromium branch we ship. To publish a new build:

1. Open a PR that bumps `pdfium/VERSION` (and anything else the release needs — patches, GN args, doc references).
2. Merge the PR into the `release` branch.
3. The **Release** workflow (`.github/workflows/release.yml`) fires on push to `release` and fans out:
   - Four `ubuntu-latest` jobs build `{linux, musl} × {amd64, arm64}` via Docker.
   - Two `macos-15` matrix jobs build `mac/arm64` and `mac/amd64` natively (via `pdfium/build_mac_native.sh`, since `xcodebuild` isn't available inside the Debian container used for the others).
   - A follow-up `macos-15` job (`build-mac-universal`) downloads the two per-arch mac archives and `lipo -create`s their `libpdfium.dylib` files into a universal Mach-O, uploading it as `pdfium-mac-univ.tgz`.
4. Each job runs `build_pdfium.py --upload`, which uploads its archive to the `pdfium-<VERSION>` GitHub Release with `gh release upload --clobber`. Parallel uploads are safe because a preceding `create-release` job ensures the tag exists before the fan-out, and `--clobber` replaces only matching asset names.
5. A final `summary` job posts the release URL + each job's result to the workflow run's summary page.

`GH_TOKEN` is the workflow's auto-minted `GITHUB_TOKEN`; each build job declares `permissions: contents: write` so `gh release *` has push access without any secrets configuration.

zstd has no release workflow of its own yet. Its releases are cut by running `python3 zstd/build_zstd.py --upload` from a host with Docker (and, for the mac archives, from a macOS host), which creates or updates the `zstd-<VERSION>` release. Everything such a workflow would need already lives in the driver — version resolution, verification, and `gh release upload --clobber` — so wiring it up is a YAML job rather than new build logic.

## Development

### Running tests

```bash
pip install pytest
pytest -v
```

Collection is repo-wide — `pyproject.toml` sets no `testpaths` — so every dependency's `tests/` directory runs without being listed anywhere.

### Git hooks

Hooks for every repo in the org are installed from `libviprs-tests`, which keeps one
installer and one guard holding it to each repo's CI:

```bash
../libviprs-tests/tools/install-hooks.sh
```

The pre-commit hook it writes for this repo runs ruff lint + format, shellcheck and
pytest, and it fails loudly when one of those tools is missing rather than skipping
it. This repo used to ship an installer of its own that skipped shellcheck when
shellcheck was not installed, which reported a pass for a check it had not run.

### Linting

```bash
pip install ruff
ruff check .
ruff format --check .
git ls-files '*.sh' | xargs shellcheck
```

These are exactly what CI runs, and none of them names a dependency directory: ruff walks the tree, pytest collects from the root, and shellcheck takes its file list from git. A new dependency is covered the day it lands.

### CI

GitHub Actions runs on every push and PR to `main`:

- **lint** — ruff check + format over the whole repo
- **test** — pytest on Python 3.9 and 3.12, collecting from the repo root
- **shellcheck** — every shell script `git ls-files '*.sh'` reports

A separate **Build PDFium** workflow (`.github/workflows/build.yml`) is available via manual dispatch. It builds the full `{linux, musl} × {amd64, arm64}` matrix inside Docker and — when `upload=true` is set — creates or replaces the GitHub Release on this repo.

## Further reading

- [`MANUAL.md`](MANUAL.md) — complete man-page-style reference for the build tooling, CLI options, artifact layout, environment, exit statuses, troubleshooting.
- [`pdfium/README.md`](pdfium/README.md) — PDFium-specific build pipeline overview, GN args, patches.
- [`zstd/README.md`](zstd/README.md) — zstd build pipeline, version pin rationale, pkg-config consumption.

## License

[MIT](LICENSE)
