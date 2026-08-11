# NeuralRev

A stereo delay + neural reverb VST3 plugin for macOS.

## Overview

NeuralRev combines a flexible stereo delay with a neural network-based reverb in a classic two-pedal layout. The UI is rendered with Flutter, featuring custom chicken-head pointer knobs, stomp switches, and panel toggles — all styled after vintage pedal hardware.

## Signal Chain

```
Input → Delay → Reverb → Output
```

The delay and reverb are wired in series. **Bypass only disables the reverb** — the delay remains active independently, so you can use it as a standalone delay pedal.

## Features

### Delay Section

| Control | Range | Description |
|---------|-------|-------------|
| ACTIVE | On / Off | Stomp switch to enable/disable the delay |
| DRY/WET | 0.00 – 1.00 | Delay mix |
| TIME L | 100 – 1100 ms | Left channel delay time |
| TIME R | 100 – 1100 ms | Right channel delay time |
| FEEDBACK | 0.00 – 0.50 | Feedback amount |
| LOW PASS | 1000 – 16000 Hz | Damping filter |
| HIGH PASS | 20 – 800 Hz | Low-cut filter |

**Modes:**

- **MONO**: Both channels share TIME L; TIME R is dimmed (inactive).
- **STEREO**: Independent left/right delay times with cross-feedback.

**Sync Mode:**

When enabled, delay times are synced to a tempo (40–240 BPM) using musical note divisions (0–20, from whole note to 1/128 triplet). In stereo mode, left and right channels can have independent note values.

### Reverb Section

| Control | Range | Description |
|---------|-------|-------------|
| DRY/WET | 0.00 – 1.00 | Reverb mix |
| PRE-DELAY | 1 – 200 ms | Reverb pre-delay |
| DECAY | 0.50 – 8.00 s | Reverb decay time |
| LOW CUT | 50 – 700 Hz | Low-frequency cut |
| HIGH CUT | 1000 – 10000 Hz | High-frequency cut |
| BYPASS | On / Off | Bypass reverb only (delay still active) |

The reverb engine is based on a neural network model, delivering dense, smooth reverberation with minimal CPU usage.

### UI Features

- **Chicken-head pointer knobs**: Vintage-style cream knobs with drag-to-adjust interaction (hold Shift for fine control, double-click to reset to default)
- **Stomp switches**: LED-lit footswitches with panel labels for delay active and reverb bypass
- **Tap-to-edit numeric input**: Tap the value label below any knob to enter an exact numeric value
- **Smart dimming**: Inactive controls automatically dim to indicate they're not affecting the signal

## Install

1. Go to [Releases](https://github.com/kysmegumi3030/neuralrev/releases) and download the latest `NeuralRev.vst3`.
2. Copy `NeuralRev.vst3` to `~/Library/Audio/Plug-Ins/VST3/`.
3. Rescan plugins in your DAW.

## Build from Source

```bash
cmake -B build -G Xcode
cmake --build build --target NeuralRev_VST3 --clean-first
```

The built plugin will be automatically installed to `~/Library/Audio/Plug-Ins/VST3/NeuralRev.vst3`.

Requirements:
- macOS
- Xcode Command Line Tools
- Flutter SDK
- CMake 3.15+

## License

MIT
