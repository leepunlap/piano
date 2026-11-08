#!/bin/bash
# Build "Sampled Piano.app": a Mac app that plays the sample library from a
# MIDI keyboard.
#
# It is written in Objective-C rather than Python because the sample library
# is loaded as a shared library, and the published macOS build of it is
# x86_64 -- so the app is built x86_64 too and runs under Rosetta, which the
# Mac has.  A Python binding would have had the same problem, and this way
# there is one thing to launch rather than an interpreter with a set of
# packages that have to match the architecture.
#
# Usage: build_sampled_piano.sh [destination]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$HOME/Applications/Sampled Piano.app}"
BIN="$ROOT/bin/sfz_render"

mkdir -p "$DEST/Contents/MacOS" "$DEST/Contents/Resources"

clang -arch x86_64 -fobjc-arc -O2 \
  -framework Foundation -framework CoreMIDI -framework CoreAudio -framework AudioToolbox \
  -I "$ROOT/bin/include" -L "$ROOT/bin/lib" -lsfizz -rpath "$ROOT/bin/lib" \
  -DREPO_ROOT="\"$ROOT\"" \
  -o "$DEST/Contents/MacOS/Sampled Piano" "$ROOT/tools/sampled_piano.m"

cat > "$DEST/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>Sampled Piano</string>
    <key>CFBundleDisplayName</key>     <string>Sampled Piano</string>
    <key>CFBundleIdentifier</key>      <string>local.piano.sampled</string>
    <key>CFBundleExecutable</key>      <string>Sampled Piano</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleSignature</key>       <string>????</string>
    <key>CFBundleInfoDictionaryVersion</key> <string>6.0</string>
    <key>CFBundleShortVersionString</key>    <string>1.0</string>
    <key>CFBundleVersion</key>         <string>1.0</string>
    <key>LSMinimumSystemVersion</key>  <string>11.0</string>
    <key>NSHighResolutionCapable</key> <true/>
</dict>
</plist>
PLIST

plutil -lint "$DEST/Contents/Info.plist" >/dev/null
echo "built $DEST"
echo
echo "The library it plays:"
echo "  $ROOT/samples/SalamanderGrandPiano/Salamander Grand Piano V3.sfz"
echo
echo "If the keyboard sends nothing, its MIDI Out is off:"
echo "  on the Nord: SETUP -> MIDI CONTROL -> MIDI Out: ON"
