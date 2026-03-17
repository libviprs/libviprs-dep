# libviprs-dep

Pre-compiled native dependencies for [libviprs](https://github.com/libviprs/libviprs). Each dependency has its own directory with build scripts and documentation. Compiled binaries are published as GitHub Releases.

## Dependencies

| Directory | Library | Purpose |
| --- | --- | --- |
| [`pdfium/`](pdfium/) | [PDFium](https://pdfium.googlesource.com/pdfium/) | PDF page rasterization |

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

A separate **Build PDFium** workflow is available via manual dispatch for full Docker builds.

## License

[MIT](LICENSE)
