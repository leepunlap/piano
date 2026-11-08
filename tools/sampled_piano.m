// sampled_piano.m — play the sample library from a MIDI keyboard.
//
// The Nord Electro on the desk has a sound module of its own, and it is
// silent: its MIDI Out is switched off in its own setup and its volume is
// down.  This does not use the instrument's sound at all.  The keys come in
// over MIDI, and the sound comes from the sample library on this machine —
// an actual recorded grand, with sixteen velocity layers of it, so a soft
// note and a hard note are different recordings rather than the same
// recording played at different volumes.
//
// Written in Objective-C because CoreMIDI and CoreAudio are the two APIs
// macOS gives you for this, and because the audio callback has to be quick.
//
//   clang -arch x86_64 -fobjc-arc -framework Foundation -framework CoreMIDI \
//         -framework CoreAudio -framework AudioToolbox \
//         -I piano/include -L piano/lib -lsfizz -rpath piano/lib \
//         -o "Sampled Piano" sampled_piano.m
//
#import <Foundation/Foundation.h>
#import <CoreMIDI/CoreMIDI.h>
#import <CoreAudio/CoreAudio.h>
#import <AudioToolbox/AudioToolbox.h>
#import <pthread.h>
#include "sfizz.h"

static sfizz_synth_t *gSynth = NULL;
static pthread_mutex_t gLock = PTHREAD_MUTEX_INITIALIZER;
static int gBlockSize = 512;
static float *gLeft = NULL;      // the synth's output, one plane per channel
static float *gRight = NULL;
static int gVoices = 0;
static int gNotesOn = 0;

static void noteOn(int note, int velocity) {
    pthread_mutex_lock(&gLock);
    sfizz_send_note_on(gSynth, 0, note, velocity);
    gNotesOn++;
    pthread_mutex_unlock(&gLock);
}

static void noteOff(int note) {
    pthread_mutex_lock(&gLock);
    sfizz_send_note_off(gSynth, 0, note, 0);
    gNotesOn--;
    pthread_mutex_unlock(&gLock);
}

static void controlChange(int controller, int value) {
    pthread_mutex_lock(&gLock);
    sfizz_send_cc(gSynth, 0, controller, value);
    pthread_mutex_unlock(&gLock);
}

// ── the keyboard ─────────────────────────────────────────────────────────────
static void midiCallback(const MIDIPacketList *list, void *refCon, void *srcConnRefCon) {
    (void)refCon; (void)srcConnRefCon;
    const MIDIPacket *packet = &list->packet[0];
    for (UInt32 i = 0; i < list->numPackets; i++) {
        const MIDIPacket *current = &list->packet[i];
        for (UInt32 j = 0; j < current->length; ) {
            Byte status = current->data[j] & 0xF0;
            if (status == 0x90 && current->data[j + 2] > 0) {
                noteOn(current->data[j + 1], current->data[j + 2]);
                j += 3;
            } else if (status == 0x80 || status == 0x90) {
                noteOff(current->data[j + 1]);
                j += 3;
            } else if (status == 0xB0) {
                controlChange(current->data[j + 1], current->data[j + 2]);
                j += 3;
            } else if (status >= 0xF0) {
                break;
            } else {
                j += 2;
            }
        }
        packet = current;
    }
}

// ── the sound ────────────────────────────────────────────────────────────────
static OSStatus renderCallback(void *refCon,
                                AudioUnitRenderActionFlags *ioActionFlags,
                                const AudioTimeStamp *inTimeStamp,
                                UInt32 inBusNumber,
                                UInt32 inNumberFrames,
                                AudioBufferList *ioData) {
    (void)refCon; (void)ioActionFlags; (void)inTimeStamp; (void)inBusNumber;

    if (ioData->mNumberBuffers < 1 || gLeft == NULL) return noErr;

    // The synth renders whole blocks; if the audio unit asks for a different
    // number of frames, render enough blocks to cover it.
    UInt32 blocks = (inNumberFrames + gBlockSize - 1) / gBlockSize;
    pthread_mutex_lock(&gLock);
    for (UInt32 b = 0; b < blocks; b++) {
        float *pointers[2] = {gLeft + (size_t)b * gBlockSize, gRight + (size_t)b * gBlockSize};
        sfizz_render_block(gSynth, pointers, 2, gBlockSize);
    }
    pthread_mutex_unlock(&gLock);

    // The device may want its channels planar or interleaved; it gets to say.
    UInt32 channels = ioData->mNumberBuffers;
    if (channels == 1) {
        float *out = (float *)ioData->mBuffers[0].mData;
        UInt32 available = ioData->mBuffers[0].mDataByteSize / (2 * sizeof(float));
        UInt32 count = inNumberFrames < available ? inNumberFrames : available;
        for (UInt32 i = 0; i < count; i++) {
            out[2 * i] = gLeft[i];
            out[2 * i + 1] = gRight[i];
        }
    } else {
        for (UInt32 c = 0; c < channels; c++) {
            if (ioData->mBuffers[c].mData == NULL) continue;
            float *out = (float *)ioData->mBuffers[c].mData;
            float *plane = (c == 0) ? gLeft : gRight;
            UInt32 available = ioData->mBuffers[c].mDataByteSize / sizeof(float);
            UInt32 count = inNumberFrames < available ? inNumberFrames : available;
            memcpy(out, plane, count * sizeof(float));
        }
    }
    return noErr;
}

// ── setting up ───────────────────────────────────────────────────────────────
static AudioUnit gOutput = NULL;

static int setupAudio(int sampleRate) {
    AudioComponentDescription description = {0};
    description.componentType = kAudioUnitType_Output;
    description.componentSubType = kAudioUnitSubType_HALOutput;
    description.componentManufacturer = kAudioUnitManufacturer_Apple;

    AudioComponent component = AudioComponentFindNext(NULL, &description);
    if (component == NULL) { fprintf(stderr, "no audio output component\n"); return -1; }
    if (AudioComponentInstanceNew(component, &gOutput) != noErr) {
        fprintf(stderr, "could not make the audio unit\n");
        return -1;
    }

    UInt32 streamSize = sizeof(AudioStreamBasicDescription);
    AudioStreamBasicDescription format = {0};
    format.mSampleRate = sampleRate;
    format.mFormatID = kAudioFormatLinearPCM;
    format.mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagsNativeEndian;
    format.mChannelsPerFrame = 2;
    format.mBitsPerChannel = 32;
    format.mFramesPerPacket = 1;
    format.mBytesPerFrame = 2 * sizeof(Float32);
    format.mBytesPerPacket = format.mBytesPerFrame;

    if (AudioUnitSetProperty(gOutput, kAudioUnitProperty_StreamFormat,
                              kAudioUnitScope_Input, 0, &format, streamSize) != noErr) {
        fprintf(stderr, "could not set the output format\n");
        return -1;
    }

    AURenderCallbackStruct callback = {0};
    callback.inputProc = renderCallback;
    callback.inputProcRefCon = NULL;
    if (AudioUnitSetProperty(gOutput, kAudioUnitProperty_SetRenderCallback,
                              kAudioUnitScope_Input, 0, &callback, sizeof(callback)) != noErr) {
        fprintf(stderr, "could not set the render callback\n");
        return -1;
    }

    if (AudioUnitInitialize(gOutput) != noErr) {
        fprintf(stderr, "could not initialise the audio unit\n");
        return -1;
    }
    if (AudioOutputUnitStart(gOutput) != noErr) {
        fprintf(stderr, "could not start the audio output\n");
        return -1;
    }
    return 0;
}

static int setupMIDI(const char *wanted) {
    MIDIClientRef client = 0;
    if (MIDIClientCreateWithBlock(CFSTR("Sampled Piano"), &client, NULL) != noErr) {
        fprintf(stderr, "could not create the MIDI client\n");
        return -1;
    }

    // List what is there, so that a keyboard which is not appearing can be
    // recognised as a keyboard which is not plugged in.
    ItemCount sources = MIDIGetNumberOfSources();
    printf("MIDI sources:\n");
    for (ItemCount i = 0; i < sources; i++) {
        MIDIEndpointRef endpoint = MIDIGetSource(i);
        CFStringRef name = NULL;
        MIDIObjectGetStringProperty(endpoint, kMIDIPropertyDisplayName, &name);
        char buffer[256] = {0};
        if (name) {
            CFStringGetCString(name, buffer, sizeof(buffer), kCFStringEncodingUTF8);
            CFRelease(name);
        }
        printf("  %-28s %s\n", buffer, "" );
    }

    for (ItemCount i = 0; i < sources; i++) {
        MIDIEndpointRef endpoint = MIDIGetSource(i);
        CFStringRef name = NULL;
        MIDIObjectGetStringProperty(endpoint, kMIDIPropertyDisplayName, &name);
        char buffer[256] = {0};
        if (name) {
            CFStringGetCString(name, buffer, sizeof(buffer), kCFStringEncodingUTF8);
            CFRelease(name);
        }
        if (wanted == NULL || strstr(buffer, wanted) != NULL) {
            MIDIPortRef port = 0;
            if (MIDIInputPortCreateWithBlock(client, CFSTR("input"), &port, ^(const MIDIPacketList *list, void *ref) {
                midiCallback(list, ref, NULL);
            }) == noErr) {
                MIDIPortConnectSource(port, endpoint, NULL);
                printf("listening to %s\n", buffer);
                break;
            }
        }
    }
    return 0;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        const char *sfzPath = "/Users/blee/src/piano/samples/SalamanderGrandPiano/Salamander Grand Piano V3.sfz";
        const char *wanted = NULL;
        for (int i = 1; i < argc; i++) {
            if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
                wanted = argv[++i];
            } else if (strcmp(argv[i], "--test") != 0) {
                sfzPath = argv[i];
            }
        }

        gSynth = sfizz_create_synth();
        if (gSynth == NULL) { fprintf(stderr, "could not create the synth\n"); return 1; }
        sfizz_set_samples_per_block(gSynth, gBlockSize);
        sfizz_set_num_voices(gSynth, 64);
        sfizz_set_volume(gSynth, 0.8);
        if (!sfizz_load_file(gSynth, sfzPath)) {
            fprintf(stderr, "could not load %s\n", sfzPath);
            return 1;
        }
        printf("library: %s\n", sfzPath);

        gLeft = (float *)calloc((size_t)gBlockSize * 8, sizeof(float));
        gRight = (float *)calloc((size_t)gBlockSize * 8, sizeof(float));
        if (setupAudio(48000) != 0) return 1;
        if (setupMIDI(wanted) != 0) return 1;

        int test = 0;
        for (int i = 1; i < argc; i++) {
            if (strcmp(argv[i], "--test") == 0) test = 1;
        }
        if (test) {
            // Play a chord without a keyboard, so that the sound path can be
            // checked on its own.
            printf("test: playing a chord\n");
            fflush(stdout);
            noteOn(60, 100); noteOn(64, 100); noteOn(67, 100); noteOn(72, 100);
            sleep(3);
            noteOff(60); noteOff(64); noteOff(67); noteOff(72);
            sleep(2);
            return 0;
        }

        printf("playing - press keys on the keyboard, Ctrl-C to stop\n");
        fflush(stdout);
        while (1) { sleep(1); }
    }
    return 0;
}
