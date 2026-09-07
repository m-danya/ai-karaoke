#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT_DIR/web"
TEMURIN_API_URL="https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse"
ANDROID_CMDLINE_TOOLS_URL="https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip"
ANDROID_CMDLINE_TOOLS_SHA256="4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583"

infer_scheme() {
  local raw_host="$1"
  local host="${raw_host%%/*}"
  host="${host%%:*}"
  if [[ "$host" == "localhost" || "$host" == "127.0.0.1" || "$host" == *.local ]]; then
    printf 'http'
    return
  fi
  if [[ "$host" =~ ^10\. ]] || [[ "$host" =~ ^192\.168\. ]] || [[ "$host" =~ ^127\. ]] || [[ "$host" =~ ^169\.254\. ]]; then
    printf 'http'
    return
  fi
  if [[ "$host" =~ ^172\.(1[6-9]|2[0-9]|3[0-1])\. ]]; then
    printf 'http'
    return
  fi
  printf 'https'
}

resolve_jdk_home() {
  local candidate="$1"

  if [[ -z "$candidate" || ! -x "$candidate/bin/java" || ! -x "$candidate/bin/javac" ]]; then
    return 1
  fi
  if ! "$candidate/bin/java" -version 2>&1 | head -n 1 | grep -Eq 'version "21[.\"]'; then
    return 1
  fi

  (
    cd "$candidate"
    pwd -P
  )
}

install_android_jdk() (
  set -euo pipefail

  local install_dir="$1"
  local install_parent
  local staging_dir
  local archive_path
  local checksum_path
  local extracted_dir
  local download_url
  local expected_checksum
  local actual_checksum
  local required_command

  for required_command in curl tar sha256sum mktemp; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
      echo "$required_command is required to install the Android JDK." >&2
      exit 1
    fi
  done

  if [[ -e "$install_dir" ]]; then
    echo "Android JDK path exists but is not a complete JDK: $install_dir" >&2
    exit 1
  fi

  install_parent="$(dirname "$install_dir")"
  mkdir -p "$install_parent"
  staging_dir="$(mktemp -d "$install_parent/.jdk-21.XXXXXX")"
  trap 'rm -rf "$staging_dir"' EXIT

  archive_path="$staging_dir/temurin-21.tar.gz"
  checksum_path="$archive_path.sha256.txt"
  extracted_dir="$staging_dir/extracted"

  echo "Downloading Temurin JDK 21 to install at $install_dir ..." >&2
  download_url="$(
    curl --fail --silent --show-error \
      --output /dev/null \
      --write-out '%{redirect_url}' \
      "$TEMURIN_API_URL"
  )"
  if [[ -z "$download_url" ]]; then
    echo "Adoptium did not return a JDK download URL." >&2
    exit 1
  fi

  curl --fail --location --show-error --retry 3 --retry-delay 2 \
    --output "$archive_path" \
    "$download_url"
  curl --fail --location --silent --show-error --retry 3 --retry-delay 2 \
    --output "$checksum_path" \
    "${download_url}.sha256.txt"

  expected_checksum="$(
    awk 'NR == 1 { print $1 }' "$checksum_path" | tr '[:upper:]' '[:lower:]'
  )"
  actual_checksum="$(sha256sum "$archive_path" | awk '{ print $1 }')"
  if [[ ! "$expected_checksum" =~ ^[[:xdigit:]]{64}$ || "$actual_checksum" != "$expected_checksum" ]]; then
    echo "Temurin JDK checksum verification failed." >&2
    exit 1
  fi

  mkdir "$extracted_dir"
  tar -xzf "$archive_path" -C "$extracted_dir" --strip-components=1
  if ! resolve_jdk_home "$extracted_dir" >/dev/null; then
    echo "The downloaded archive does not contain a complete JDK." >&2
    exit 1
  fi

  mv "$extracted_dir" "$install_dir"
  echo "Temurin JDK 21 installed at $install_dir" >&2
)

ensure_android_jdk() {
  local requested_java_home="$1"
  local managed_java_home="$2"
  local javac_path
  local discovered_java_home

  if resolve_jdk_home "$requested_java_home"; then
    return
  fi

  if [[ -n "$requested_java_home" ]]; then
    echo "Ignoring incomplete JAVA_HOME: $requested_java_home" >&2
  fi

  if javac_path="$(command -v javac 2>/dev/null)"; then
    discovered_java_home="$(dirname "$(dirname "$(readlink -f "$javac_path")")")"
    if resolve_jdk_home "$discovered_java_home"; then
      return
    fi
  fi

  if ! resolve_jdk_home "$managed_java_home" >/dev/null; then
    install_android_jdk "$managed_java_home"
  fi

  resolve_jdk_home "$managed_java_home"
}

android_sdk_is_ready() {
  local sdk_root="$1"
  local platform_version="$2"
  local build_tools_version="$3"

  [[
    -f "$sdk_root/platforms/android-$platform_version/android.jar" &&
      -x "$sdk_root/build-tools/$build_tools_version/apksigner"
  ]]
}

install_android_command_line_tools() (
  set -euo pipefail

  local sdk_root="$1"
  local sdk_parent
  local staging_dir
  local archive_path
  local extracted_dir
  local actual_checksum
  local required_command

  for required_command in curl unzip sha256sum mktemp; do
    if ! command -v "$required_command" >/dev/null 2>&1; then
      echo "$required_command is required to install the Android SDK." >&2
      exit 1
    fi
  done

  if [[ -e "$sdk_root/cmdline-tools/latest" ]]; then
    echo "Android command-line tools path exists but is incomplete: $sdk_root/cmdline-tools/latest" >&2
    exit 1
  fi

  sdk_parent="$(dirname "$sdk_root")"
  mkdir -p "$sdk_parent" "$sdk_root/cmdline-tools"
  staging_dir="$(mktemp -d "$sdk_parent/.android-sdk.XXXXXX")"
  trap 'rm -rf "$staging_dir"' EXIT

  archive_path="$staging_dir/command-line-tools.zip"
  extracted_dir="$staging_dir/extracted"

  echo "Downloading Android SDK command-line tools ..." >&2
  curl --fail --location --show-error --retry 3 --retry-delay 2 \
    --output "$archive_path" \
    "$ANDROID_CMDLINE_TOOLS_URL"

  actual_checksum="$(sha256sum "$archive_path" | awk '{ print $1 }')"
  if [[ "$actual_checksum" != "$ANDROID_CMDLINE_TOOLS_SHA256" ]]; then
    echo "Android command-line tools checksum verification failed." >&2
    exit 1
  fi

  mkdir "$extracted_dir"
  unzip -q "$archive_path" -d "$extracted_dir"
  if [[ ! -x "$extracted_dir/cmdline-tools/bin/sdkmanager" ]]; then
    echo "The downloaded archive does not contain sdkmanager." >&2
    exit 1
  fi

  mv "$extracted_dir/cmdline-tools" "$sdk_root/cmdline-tools/latest"
  echo "Android SDK command-line tools installed at $sdk_root" >&2
)

ensure_android_sdk() {
  local sdk_root="$1"
  local platform_version="$2"
  local build_tools_version="$3"
  local sdkmanager_path="$sdk_root/cmdline-tools/latest/bin/sdkmanager"
  local sdkmanager_status

  if android_sdk_is_ready "$sdk_root" "$platform_version" "$build_tools_version"; then
    return
  fi

  if [[ ! -x "$sdkmanager_path" ]]; then
    install_android_command_line_tools "$sdk_root"
  fi

  echo "Installing Android SDK platform $platform_version and build-tools $build_tools_version ..." >&2
  set +e
  set +o pipefail
  yes | "$sdkmanager_path" \
    --sdk_root="$sdk_root" \
    "platform-tools" \
    "platforms;android-$platform_version" \
    "build-tools;$build_tools_version"
  sdkmanager_status="${PIPESTATUS[1]}"
  set -o pipefail
  set -e

  if [[ "$sdkmanager_status" -ne 0 ]]; then
    echo "Android SDK package installation failed." >&2
    return "$sdkmanager_status"
  fi

  if ! android_sdk_is_ready "$sdk_root" "$platform_version" "$build_tools_version"; then
    echo "Android SDK installation completed without the required packages." >&2
    return 1
  fi
}

find_latest_build_tool() {
  local tool_name="$1"
  local tool_path

  tool_path="$(
    find "$ANDROID_HOME/build-tools" -mindepth 2 -maxdepth 2 -type f -name "$tool_name" \
      | sort -V \
      | tail -n 1
  )"

  if [[ -z "$tool_path" ]]; then
    echo "Unable to find $tool_name in $ANDROID_HOME/build-tools." >&2
    exit 1
  fi

  printf '%s' "$tool_path"
}

ensure_signing_keystore() {
  if [[ -f "$ANDROID_KEYSTORE_PATH" ]]; then
    return
  fi

  if [[ -n "$REQUESTED_ANDROID_KEYSTORE_PATH" ]]; then
    echo "ANDROID_KEYSTORE_PATH does not exist: $ANDROID_KEYSTORE_PATH" >&2
    exit 1
  fi

  mkdir -p "$(dirname "$ANDROID_KEYSTORE_PATH")"
  keytool -genkeypair \
    -keystore "$ANDROID_KEYSTORE_PATH" \
    -alias "$ANDROID_KEY_ALIAS" \
    -storepass "$ANDROID_KEYSTORE_PASSWORD" \
    -keypass "$ANDROID_KEY_PASSWORD" \
    -keyalg RSA \
    -keysize 2048 \
    -validity 10000 \
    -dname "CN=Android Debug,O=Android,C=US" \
    >/dev/null
}

sign_release_apk() {
  local unsigned_apk="$1"
  local signed_apk="$2"
  local apksigner_path

  apksigner_path="$(find_latest_build_tool apksigner)"
  ensure_signing_keystore

  rm -f "$signed_apk"
  "$apksigner_path" sign \
    --ks "$ANDROID_KEYSTORE_PATH" \
    --ks-key-alias "$ANDROID_KEY_ALIAS" \
    --ks-pass "pass:$ANDROID_KEYSTORE_PASSWORD" \
    --key-pass "pass:$ANDROID_KEY_PASSWORD" \
    --out "$signed_apk" \
    "$unsigned_apk"

  "$apksigner_path" verify "$signed_apk" >/dev/null
}

# Use JDK 21 for the Capacitor/Gradle release build.
export ANDROID_JDK_DIR="${ANDROID_JDK_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/ai-karaoke/jdk-21}"
export JAVA_HOME="$(ensure_android_jdk "${JAVA_HOME:-}" "$ANDROID_JDK_DIR")"
export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/ai-karaoke/android-sdk}}"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export GRADLE_USER_HOME="${GRADLE_USER_HOME:-${XDG_CACHE_HOME:-$HOME/.cache}/ai-karaoke/gradle}"
REQUESTED_ANDROID_KEYSTORE_PATH="${ANDROID_KEYSTORE_PATH:-}"
export ANDROID_KEYSTORE_PATH="${REQUESTED_ANDROID_KEYSTORE_PATH:-$HOME/.android/debug.keystore}"
export ANDROID_KEYSTORE_PASSWORD="${ANDROID_KEYSTORE_PASSWORD:-android}"
export ANDROID_KEY_ALIAS="${ANDROID_KEY_ALIAS:-androiddebugkey}"
export ANDROID_KEY_PASSWORD="${ANDROID_KEY_PASSWORD:-$ANDROID_KEYSTORE_PASSWORD}"
ensure_android_sdk "$ANDROID_HOME" 36 36.0.0
"$ROOT_DIR/scripts/build-web.sh"
cd "$WEB_DIR"
printf 'sdk.dir=%s\n' "$ANDROID_HOME" > android/local.properties
npx cap sync android
cd android
./gradlew --no-daemon assembleRelease
APK_OUTPUT_DIR="$WEB_DIR/android/app/build/outputs/apk/release"
sign_release_apk "$APK_OUTPUT_DIR/app-release-unsigned.apk" "$APK_OUTPUT_DIR/app-release-signed.apk"
cp "$APK_OUTPUT_DIR/app-release-signed.apk" "$ROOT_DIR/AI Karaoke.apk"
printf 'APK copied to: %s\n' "$ROOT_DIR/AI Karaoke.apk"
