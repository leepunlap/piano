# The keyboard

A **MIDIPLUS X8III** (USB product string "X8III", serial `KCX83210900273`,
vendor "MIDIPLUS", device class 0xEF — miscellaneous).

## It has no sound module

It is a MIDI controller and nothing else.  Pressing its keys produces no
sound, ever, and that is not a fault: there are no voices in it to make one.
Everything it plays has to come from somewhere else — this machine, through
`Sampled Piano.app`, which renders the Salamander library.

This is worth writing down because it looks like a fault.  Earlier notes in
this project chased the silence as though the instrument had a sound engine
that was switched off — volume, headphone mute, layer faders, a MIDI Out
menu.  None of that applies here: there is nothing in the instrument to
unmute.

## The MIDI link

The USB device enumerates reliably and CoreMIDI binds to its ports, and the
data comes through.

It used not to, and the reason was two things rather than one.

**The cable.** The device enumerates and the names are right, but no data
arrives — through rtmidi's callback, through rtmidi on the second port,
through mido's polling, through CoreMIDI's own block API, all at once.
A charge-only USB cable is the exact match for that symptom: the device
appears and exposes no data endpoints.  A different cable and a different
port fixed it.

**One connection at a time.** The X8III's interface serves a single
connection to `X8III MidiOut`.  While `Sampled Piano` was running, a
separate logger on the same port received *nothing* while the app went on
receiving notes — 253 of them, with the logger attached.  So a logger
running alongside the app does not observe the keyboard; it locks the app
out of it.  That is what made the link look intermittent for a long time:
every diagnostic run stole the connection from the thing being diagnosed.

Only one thing at a time may listen to `X8III MidiOut`.

## Watching the link

    python3 -m piano.cli monitor

prints every message as it arrives, so whether the link is alive can be
seen directly rather than inferred.  Run it *instead of* the app, not
alongside it.

## Playing it

    open -a "~/Applications/Sampled Piano.app"

It listens on `X8III MidiOut` and plays what arrives.  `--volume` sets the
level; the output is limited so that chords cannot clip.
