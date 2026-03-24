#!/bin/zsh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="EgovLawDownloader"
APP_DIR="$ROOT_DIR/dist/$APP_NAME.app"
EXECUTABLE="$APP_DIR/Contents/MacOS/$APP_NAME"
PLIST_PATH="$APP_DIR/Contents/Info.plist"
RESOURCE_DIR="$APP_DIR/Contents/Resources"
SOURCE_FILE="$ROOT_DIR/macos/EgovLawDownloader.m"

mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$RESOURCE_DIR"

# Objective-C + AppKit/WebKit で、ダブルクリック起動できる macOS アプリを作ります。
clang \
  -fobjc-arc \
  -framework Cocoa \
  -framework WebKit \
  "$SOURCE_FILE" \
  -o "$EXECUTABLE"

cp "$ROOT_DIR/macos/Resources/index.html" "$RESOURCE_DIR/index.html"

cat > "$PLIST_PATH" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>ja</string>
  <key>CFBundleExecutable</key>
  <string>EgovLawDownloader</string>
  <key>CFBundleIdentifier</key>
  <string>local.egov-law-downloader.macos</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>EgovLawDownloader</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSPrincipalClass</key>
  <string>NSApplication</string>
</dict>
</plist>
EOF

echo "Built app bundle:"
echo "$APP_DIR"
