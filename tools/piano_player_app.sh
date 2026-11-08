#!/bin/bash
# Build the "Piano Player" app: a Mac app that plays a MIDI file with the
# Salamander piano samples.
#
# The app is a shell script in a bundle: it renders the MIDI with sfizz and
# the sample library, then plays it.  The samples are what make it worth
# having over a notation program, which plays MIDI with its own voices.
#
# Usage: piano_player_app.sh [destination]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$HOME/Applications/Piano Player.app}"

mkdir -p "$DEST/Contents/MacOS" "$DEST/Contents/Resources"

cat > "$DEST/Contents/MacOS/play" <<SCRIPT
#!/bin/bash
ROOT="$ROOT"
SFIZZ="\$ROOT/bin/sfizz_render"
SFZ="\$ROOT/samples/SalamanderGrandPiano/Salamander Grand Piano V3.sfz"

if [ \$# -lt 1 ]; then
  osascript -e 'choose file of type {"public.midi-audio"} with prompt "Play which MIDI file?"' \\
    | tr -d '\\r' > /tmp/piano_player_pick.txt 2>/dev/null || exit 1
  MIDI=\$(cat /tmp/piano_player_pick.txt)
else
  MIDI="\$1"
fi

if [ ! -f "\$MIDI" ]; then
  echo "No such file: \$MIDI" >&2
  exit 1
fi

BASE=\$(basename "\$MIDI")
WAV="/tmp/piano_player_\$(echo "\$BASE" | tr ' /' '__').wav"

echo "Rendering \$BASE with the Salamander piano..."
# --use-eot matters: without it sfizz renders past the end of the song and
# never stops.
if ! "\$SFIZZ" --sfz "\$SFZ" --midi "\$MIDI" --wav "\$WAV" \\
      --samplerate 48000 --polyphony 64 --use-eot; then
  osascript -e 'display alert "Piano Player" message "Could not render that MIDI file."'
  exit 1
fi

echo "Playing..."
afplay "\$WAV"
SCRIPT

chmod +x "$DEST/Contents/MacOS/play"

cat > "$DEST/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>Piano Player</string>
    <key>CFBundleDisplayName</key>     <string>Piano Player</string>
    <key>CFBundleIdentifier</key>      <string>local.piano.player</string>
    <key>CFBundleExecutable</key>      <string>play</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleSignature</key>       <string>????</string>
    <key>CFBundleInfoDictionaryVersion</key> <string>6.0</string>
    <key>CFBundleShortVersionString</key>    <string>1.0</string>
    <key>CFBundleVersion</key>         <string>1.0</string>
    <key>LSMinimumSystemVersion</key>  <string>11.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>CFBundleDocumentTypes</key>
    <array>
        <dict>
            <key>CFBundleTypeName</key>    <string>MIDI File</string>
            <key>CFBundleTypeRole</key>    <string>Viewer</string>
            <key>LSHandlerRank</key>       <string>Alternate</string>
            <key>CFBundleTypeExtensions</key>
            <array>
                <string>mid</string>
                <string>midi</string>
            </array>
        </dict>
    </array>
</dict>
</plist>
PLIST

plutil -lint "$DEST/Contents/Info.plist" >/dev/null
echo "built $DEST"
