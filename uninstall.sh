#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

APPS_DIR="$HOME/.local/share/applications"
ICONS_BASE_DIR="$HOME/.local/share/icons/hicolor"
ICON_TARGET="$ICONS_BASE_DIR/scalable/apps/ai-karaoke.png"
DESKTOP_TARGET="$APPS_DIR/ai-karaoke.desktop"

echo "[1/5] Uninstalling tool executable with uv..."
if command -v uv >/dev/null 2>&1; then
  uv tool uninstall ai-karaoke >/dev/null 2>&1 || true
else
  echo "Warning: uv is not installed or not in PATH; skipping tool uninstall."
fi

echo "[2/5] Removing desktop entry..."
if [[ -f "$DESKTOP_TARGET" ]]; then
  rm -f "$DESKTOP_TARGET"
fi

echo "[3/5] Removing icon..."
if [[ -f "$ICON_TARGET" ]]; then
  rm -f "$ICON_TARGET"
fi

echo "[4/5] Updating caches..."
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$ICONS_BASE_DIR" >/dev/null 2>&1 || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "[5/5] Done."
