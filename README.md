# libviprs-dep

Pre-compiled native dependencies for [libviprs](https://github.com/libviprs/libviprs). Each dependency has its own directory with build scripts and documentation. Compiled binaries are published as GitHub Releases.

For a full man-page-style reference on the build tooling, see [`MANUAL.md`](MANUAL.md).

## Dependencies

| Directory | Library | Purpose |
| --- | --- | --- |
| [`pdfium/`](pdfium/) | [PDFium](https://pdfium.googlesource.com/pdfium/) | PDF page rasterization |

## Release contents

Every archive published under [Releases](https://github.com/libviprs/libviprs-dep/releases) ships both a shared library and a static archive so downstream consumers can pick either linking strategy:

```
pdfium-<platform>-<cpu>/
├── lib/libpdfium.so   # or .dylib on mac — for dlopen / dynamic linking
├── lib/libpdfium.a    # static archive for pdfium-render/static
├── include/           # public C headers
├── args.gn            # GN args used for the shared build
├── args.static.gn     # GN args used for the static build
└── LICENSE
```

The default release matrix is `{linux, musl} × {amd64, arm64}` — four archives per tag. Pick the `linux-*` archives for glibc runtimes (Debian, Ubuntu, …) and the `musl-*` archives for musl runtimes (Alpine, musl-based distroless images). Loading a glibc `.so` from a musl process — or vice versa — fails at `dlopen` time. macOS (`pdfium-mac-*`) is available on request but not in the default matrix.

See [`pdfium/README.md`](pdfium/README.md#download) for direct download URLs and consumption examples.

## Quickstart

Build the full default matrix for chromium branch 7725 and publish it as a release:

```bash
python3 pdfium/build_pdfium.py 7725 --parallel --upload
```

`--parallel` fans out every `(platform, arch)` combo (5 by default) at once, gated by a memory scheduler that reads the Docker daemon's `MemTotal` and queues over-budget combos until earlier ones finish. Tune with `--mem-per-build MB` (default `4096`) if you see builds queuing unnecessarily on a large host or want extra safety margin on a small one.

Every job streams its full Docker build output to `pdfium/bin/logs/<plat>-<arch>.log`. If a job fails, the script prints the log path to stderr — `tail -n 200 pdfium/bin/logs/linux-arm64.log` gives you the authoritative post-mortem, since the in-terminal view only retains the last ~500 lines per job.

Or trigger the **Build PDFium** GitHub Actions workflow via `workflow_dispatch`, entering the chromium branch number and toggling `upload=true`.

## Development

### Running tests

```bash
pip install pytest
pytest pdfium/tests/ -v
```

### Git hooks

Install pre-commit checks that mirror the CI workflow:

```bash
./tools/install-hooks.sh
```

The pre-commit hook runs ruff lint + format, shellcheck, and pytest before each commit.

### Linting

```bash
pip install ruff
ruff check pdfium/
ruff format --check pdfium/
shellcheck pdfium/patches/*.sh
```

### CI

GitHub Actions runs on every push and PR to `main`:

- **lint** — ruff check + format
- **test** — pytest on Python 3.9 and 3.12
- **shellcheck** — validates platform patch scripts

A separate **Build PDFium** workflow (`.github/workflows/build.yml`) is available via manual dispatch. It builds the full `{linux, musl} × {amd64, arm64}` matrix inside Docker and — when `upload=true` is set — creates or replaces the GitHub Release on this repo.

## Further reading

- [`MANUAL.md`](MANUAL.md) — complete man-page-style reference for the build tooling, CLI options, artifact layout, environment, exit statuses, troubleshooting.
- [`pdfium/README.md`](pdfium/README.md) — PDFium-specific build pipeline overview, GN args, patches.

## License

[MIT](LICENSE)
