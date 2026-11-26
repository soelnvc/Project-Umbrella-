#!/usr/bin/env bash
# Render docs/brief.html to docs/brief.pdf (A4) with headless Chrome. Usage: tools/build_brief.sh
#   CHROME  path to a Chrome or Chromium binary (default: the macOS Google Chrome app)
set -eu
cd "$(dirname "$0")/.."
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PROFILE="$(mktemp -d)"
trap 'rm -rf "$PROFILE"' EXIT
"$CHROME" --headless --disable-gpu --no-pdf-header-footer --user-data-dir="$PROFILE" \
  --print-to-pdf="$(pwd)/docs/brief.pdf" "file://$(pwd)/docs/brief.html" 2>/dev/null
pages=$(grep -c "/Type /Page[^s]" docs/brief.pdf || true)
echo "docs/brief.pdf: $pages pages"
