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

## The MIDI link is unreliable

The USB device enumerates reliably and CoreMIDI binds to its ports, but the
data path is intermittent:

| | |
|---|---|
| USB enumeration | always there — three `IOUSBHostDevice` entries |
| CoreMIDI ports | `X8III MidiOut` and `X8III Port 2`, enumerated live |
| MIDI data | **nothing**, through rtmidi's callback, rtmidi on the second port, mido's polling, and CoreMIDI's own block API — all four at once |
| Once worked | 208 notes were captured in a single session, then it stopped again with no change on this side |

A device that enumerates but does not deliver, and that works for a while and
then does not, is a physical link: the cable, the port, or the bus-powered
interface itself.  A charge-only USB cable is the exact match for the
symptom — the device enumerates and the names are right, and no data ever
arrives.

So, in order:

1. Unplug the cable and wait ten seconds before plugging it back in.  The
   controller is bus-powered, so that is a real power cycle of the interface.
2. A different USB port, directly on the machine rather than through a hub.
3. A different cable — and one known to carry data.

## Watching the link

    python3 -m piano.cli monitor

prints every message as it arrives, so whether the link is alive can be
seen directly rather than inferred.  The same thing is available as a
logger that writes to a file, for when the question is "did anything arrive
in the last three minutes".

## Playing it

    open -a "~/Applications/Sampled Piano.app"

It listens on `X8III MidiOut` and plays what arrives.  `--volume` sets the
level; the output is limited so that chords cannot clip.
