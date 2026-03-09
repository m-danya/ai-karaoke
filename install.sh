#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_TEMPLATE="$SCRIPT_DIR/ai-karaoke.desktop"
ICON_SOURCE="$SCRIPT_DIR/assets/logo-transparent.png"
ENV_FILE="$SCRIPT_DIR/.env"
GENIUS_TOKEN_URL="https://genius.com/api-clients"

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

if [[ ! -t 0 ]]; then
  echo "Error: install.sh requires an interactive terminal to set GENIUS_ACCESS_TOKEN." >&2
  echo "Set it manually in $ENV_FILE and run install.sh again." >&2
  exit 1
fi

echo "[1/6] Configuring Genius token..."
echo "Open this page to get Client Access Token: $GENIUS_TOKEN_URL"

existing_token=""
if [[ -f "$ENV_FILE" ]]; then
  existing_line="$(grep -E '^GENIUS_ACCESS_TOKEN=' "$ENV_FILE" || true)"
  if [[ -n "$existing_line" ]]; then
    existing_token="${existing_line#GENIUS_ACCESS_TOKEN=}"
  fi
fi

if [[ -n "$existing_token" ]]; then
  echo "GENIUS_ACCESS_TOKEN already exists in .env"
  read -r -p "Press Enter to keep it, or paste a new token: " input_token
  if [[ -z "$input_token" ]]; then
    genius_token="$existing_token"
  else
    genius_token="$input_token"
  fi
else
  while true; do
    read -r -p "Paste GENIUS_ACCESS_TOKEN: " input_token
    if [[ -n "$input_token" ]]; then
      genius_token="$input_token"
      break
    fi
    echo "Token cannot be empty."
  done
fi

if [[ -f "$ENV_FILE" ]]; then
  if grep -qE '^GENIUS_ACCESS_TOKEN=' "$ENV_FILE"; then
    tmp_env_file="$(mktemp)"
    awk -v token="$genius_token" '
      BEGIN { replaced = 0 }
      /^GENIUS_ACCESS_TOKEN=/ {
        if (!replaced) {
          print "GENIUS_ACCESS_TOKEN=" token
          replaced = 1
        }
        next
      }
      { print }
      END {
        if (!replaced) {
          print "GENIUS_ACCESS_TOKEN=" token
        }
      }
    ' "$ENV_FILE" > "$tmp_env_file"
    mv "$tmp_env_file" "$ENV_FILE"
  else
    printf '\nGENIUS_ACCESS_TOKEN=%s\n' "$genius_token" >> "$ENV_FILE"
  fi
else
  printf 'GENIUS_ACCESS_TOKEN=%s\n' "$genius_token" > "$ENV_FILE"
fi

echo "Saved GENIUS_ACCESS_TOKEN to $ENV_FILE"

echo "[2/6] Installing tool executable with uv..."
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

echo "[3/6] Installing icon..."
mkdir -p "$ICON_TARGET_DIR"
install -m 0644 "$ICON_SOURCE" "$ICON_TARGET"

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$ICONS_BASE_DIR" >/dev/null 2>&1 || true
fi

echo "[4/6] Installing desktop entry..."
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

echo "[5/6] Updating desktop database..."
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
fi

echo "[6/6] Done."
# echo "Desktop file: $DESKTOP_TARGET"
# echo "Executable:   $EXEC_PATH"
# echo "Icon:         $ICON_TARGET"
