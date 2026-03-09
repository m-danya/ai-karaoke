#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_TEMPLATE="$SCRIPT_DIR/ai-karaoke.desktop"
ICON_SOURCE="$SCRIPT_DIR/assets/logo-transparent.png"

APPS_DIR="$HOME/.local/share/applications"
ICONS_BASE_DIR="$HOME/.local/share/icons/hicolor"
ICON_TARGET_DIR="$ICONS_BASE_DIR/scalable/apps"
ICON_TARGET="$ICON_TARGET_DIR/ai-karaoke.png"
DESKTOP_TARGET="$APPS_DIR/ai-karaoke.desktop"

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is not installed or not in PATH" >&2
  exit 1
fi

if [[ ! -f "$DESKTOP_TEMPLATE" ]]; then
  echo "Error: desktop template not found: $DESKTOP_TEMPLATE" >&2
  exit 1
fi

if [[ ! -f "$ICON_SOURCE" ]]; then
  echo "Error: icon source not found: $ICON_SOURCE" >&2
  exit 1
fi

echo "[1/5] Installing tool executable with uv..."
uv tool install --force -e "$SCRIPT_DIR"

if command -v ai-karaoke >/dev/null 2>&1; then
  EXEC_PATH="$(command -v ai-karaoke)"
else
  EXEC_PATH="$HOME/.local/bin/ai-karaoke"
fi

if [[ ! -x "$EXEC_PATH" ]]; then
  echo "Error: ai-karaoke executable was not found after installation ($EXEC_PATH)" >&2
  exit 1
fi

echo "[2/5] Installing icon..."
mkdir -p "$ICON_TARGET_DIR"
install -m 0644 "$ICON_SOURCE" "$ICON_TARGET"

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$ICONS_BASE_DIR" >/dev/null 2>&1 || true
fi

echo "[3/5] Installing desktop entry..."
mkdir -p "$APPS_DIR"
awk -v exec_path="$EXEC_PATH" '
  BEGIN { has_exec = 0; has_icon = 0 }
  /^Exec=/ { print "Exec=" exec_path; has_exec = 1; next }
  /^Icon=/ { print "Icon=ai-karaoke"; has_icon = 1; next }
  { print }
  END {
    if (!has_exec) print "Exec=" exec_path
    if (!has_icon) print "Icon=ai-karaoke"
  }
' "$DESKTOP_TEMPLATE" > "$DESKTOP_TARGET"
chmod 0644 "$DESKTOP_TARGET"

echo "[4/5] Updating desktop database..."
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "[5/5] Done."
# echo "Desktop file: $DESKTOP_TARGET"
# echo "Executable:   $EXEC_PATH"
# echo "Icon:         $ICON_TARGET"
