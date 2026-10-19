# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.

#!/bin/bash
# Set up the toy paper project for testing repoprover.
#
# This script:
#   1. Creates a working directory and ingests examples/toy_paper via
#      `repoprover init-paper --source` (exercises the paper pipeline)
#   2. Adds a lake project around the ingested sources
#   3. Initializes git, fetches Mathlib, and builds
#
# Usage:
#   bash examples/toy_paper/setup.sh [TARGET_DIR]
#
# TARGET_DIR defaults to /tmp/repoprover-toy-paper

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-/tmp/repoprover-toy-paper}"

if [ -d "$TARGET" ]; then
    echo "Target directory already exists: $TARGET"
    echo "Remove it first or pass a different path."
    exit 1
fi

echo "=== Setting up toy paper project in $TARGET ==="

# Ingest the paper (writes manifest.json, CONTENTS.md, tex/, issues/)
# Project name must match the lake project created below.
python -m repoprover init-paper "$TARGET" \
    --source "$SCRIPT_DIR" \
    --project-name ToyPaper \
    --force

cd "$TARGET"

# Lake project scaffolding around the ingested sources (same pins as the
# toy book project: mathlib + REPL are both required by repoprover)
cat > lakefile.lean <<'EOF'
-- Copyright (c) Meta Platforms, Inc. and affiliates.
-- All rights reserved.

import Lake
open Lake DSL

package «ToyPaper» where
  leanOptions := #[
    ⟨`autoImplicit, false⟩
  ]

require mathlib from git
  "https://github.com/leanprover-community/mathlib4" @ "v4.28.0"

require REPL from git
  "https://github.com/leanprover-community/repl" @ "v4.28.0-rc1"

@[default_target]
lean_lib «ToyPaper» where
  globs := #[.submodules `ToyPaper]
EOF
echo "leanprover/lean4:v4.28.0" > lean-toolchain

# Root module: agents add imports here as they create modules
mkdir -p ToyPaper
cat > ToyPaper.lean <<'EOF'
-- Copyright (c) Meta Platforms, Inc. and affiliates.
-- All rights reserved.
EOF

# Initialize git
git init -b main
git add -A
git commit -m "Import paper"

echo ""
echo "=== Fetching Mathlib (this may take a few minutes) ==="
lake update

echo ""
echo "=== Building project ==="
lake build

echo ""
echo "=== Done! ==="
echo ""
echo "Run repoprover with:"
echo "  python -m repoprover run $TARGET --pool-size 2 --verbose"
