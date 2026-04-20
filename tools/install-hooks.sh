#!/bin/bash
# Install git hooks for libviprs-dep.
#
# Mirrors the CI workflow checks so issues are caught before push:
#   pre-commit: ruff lint + format check, shellcheck, pytest
#
# Usage:
#   ./tools/install-hooks.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.git/hooks"

cat > "$HOOKS_DIR/pre-commit" << 'HOOK'
#!/bin/bash
set -euo pipefail

echo "Running pre-commit checks..."

# Ruff lint
echo "  ruff check..."
ruff check pdfium/ || {
    echo "ruff check failed. Fix lint errors before committing."
    exit 1
}

# Ruff format
echo "  ruff format --check..."
ruff format --check pdfium/ || {
    echo "ruff format failed. Run 'ruff format pdfium/' to fix."
    exit 1
}

# Shellcheck
echo "  shellcheck..."
if command -v shellcheck &>/dev/null; then
    shellcheck pdfium/patches/*.sh || {
        echo "shellcheck failed. Fix shell script issues before committing."
        exit 1
    }
else
    echo "  (shellcheck not installed, skipping)"
fi

# Pytest
echo "  pytest..."
python3 -m pytest pdfium/tests/ -q || {
    echo "Tests failed. Fix failing tests before committing."
    exit 1
}

echo "Pre-commit checks passed."
HOOK

chmod +x "$HOOKS_DIR/pre-commit"
echo "Installed pre-commit hook to $HOOKS_DIR/pre-commit"
