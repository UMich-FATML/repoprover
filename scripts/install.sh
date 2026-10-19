#!/bin/sh
# Enable the repo's git hooks for this clone. Run once after cloning.
set -e
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
echo "Enabled .githooks/ (core.hooksPath)."
echo "Pre-commit runs: pixi run lint && pixi run format-check"
echo "Run 'pixi run check' before pushing (adds typecheck + tests)."
