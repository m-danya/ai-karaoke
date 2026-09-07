# Build the Android APK

Run the build script from the repository root:

```bash
./scripts/build-android.sh
```

Node.js 22+ and npm are required. On Linux, the script can install Temurin JDK 21
and Android SDK 36 into `~/.local/share/ai-karaoke/`. It runs `npm ci`, typechecks
and builds the web client, syncs Capacitor, runs `assembleRelease`, signs and
verifies the APK, and writes **AI Karaoke.apk** in the repository root.

## Toolchain configuration

To use an existing installation, set `JAVA_HOME` to a JDK 21 directory and
`ANDROID_HOME` to an Android SDK directory. The SDK needs platform 36 and
build-tools 36.0.0; the script installs missing components automatically.
`ANDROID_SDK_ROOT` is also accepted when `ANDROID_HOME` is unset.

The managed JDK location can be overridden with `ANDROID_JDK_DIR`.
`GRADLE_USER_HOME` controls the Gradle cache, which defaults to
`~/.cache/ai-karaoke/gradle/`. Default data and cache locations respect
`XDG_DATA_HOME` and `XDG_CACHE_HOME`.

## Signing

By default, the script uses `~/.android/debug.keystore` for local sideloading,
creating it if needed. Keep that key to install future builds as updates.

To use your own signing key, set these environment variables:

- `ANDROID_KEYSTORE_PATH`: path to an existing keystore.
- `ANDROID_KEYSTORE_PASSWORD`: keystore password.
- `ANDROID_KEY_ALIAS`: signing key alias.
- `ANDROID_KEY_PASSWORD`: key password; defaults to the keystore password.

Do not commit signing keys or passwords. The build does not bundle `.env`
secrets or an embedded server address. Android discovers the server on the LAN
or accepts a manually entered host.
