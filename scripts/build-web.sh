#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/web"
npm ci
npm run build
mkdir -p "$ROOT_DIR/src/ai_karaoke/remote_web"
cp -R out/. "$ROOT_DIR/src/ai_karaoke/remote_web/"
