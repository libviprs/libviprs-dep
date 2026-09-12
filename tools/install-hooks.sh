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

# Ruff lint. Paths are discovered rather than listed, matching
# .github/workflows/ci.yml — a new dependency directory is checked
# without editing this hook.
echo "  ruff check..."
ruff check . || {
    echo "ruff check failed. Fix lint errors before committing."
    exit 1
}

# Ruff format
echo "  ruff format --check..."
ruff format --check . || {
    echo "ruff format failed. Run 'ruff format .' to fix."
    exit 1
}

# Shellcheck
echo "  shellcheck..."
if command -v shellcheck &>/dev/null; then
    scripts=$(git ls-files '*.sh')
    if [ -z "$scripts" ]; then
        echo "no shell scripts found - script discovery is broken"
        exit 1
    fi
    echo "$scripts" | xargs shellcheck || {
        echo "shellcheck failed. Fix shell script issues before committing."
        exit 1
    }
else
    echo "  (shellcheck not installed, skipping)"
fi

# Pytest. No path argument: collection is repo-wide (see pyproject.toml).
echo "  pytest..."
python3 -m pytest -q || {
    echo "Tests failed. Fix failing tests before committing."
    exit 1
}

echo "Pre-commit checks passed."
HOOK

chmod +x "$HOOKS_DIR/pre-commit"
echo "Installed pre-commit hook to $HOOKS_DIR/pre-commit"
