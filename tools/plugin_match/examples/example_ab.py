"""End-to-end example: measure a competitor VST with plugin_match.

Usage:
    python example_ab.py [path\\to\\competitor.vst3]

Defaults to the installed Klevgrand DAW Cassette. Demonstrates the reusable
workflow: probe parameters -> detect stepped switches -> measure FR / THD /
harmonics / compression with several signal types. No candidate DSP is required
for this demo (that step uses OfflineRenderer + your exe; see README).
"""
import sys
import os

# make `import plugin_match` work when run directly from examples/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
from plugin_match import VstRenderer, signals, metrics, probe

DEFAULT_VST = r"C:\Program Files\Common Files\vst3\Klevgrand\DAWCassette.vst3"


def main():
    vst = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VST
    comp = VstRenderer(vst, sr=48000)
    print(f"loaded: {vst}\n")

    # 1) parameter metadata
    print("=== parameters ===")
    for row in probe.dump_params(comp.plugin):
        print(f"  {row['name']:<16} range=({row['min_value']},{row['max_value']})"
              f" step={row['step_size']} default={row['default_value']}")

    # 2) classify a couple of params as stepped vs continuous (fingerprint = a
    #    band-mean gain for tape; output level for a noise/level control).
    print("\n=== stepped-switch detection ===")
    probe_sig = signals.multitone([90, 220, 500, 1500, 5000, 10000], 0.5, 0.02, comp.sr)

    def band_gain(y):
        return metrics.band_mean_db(y, probe_sig, comp.sr, 60, 300)

    def out_level(y):  # for hiss/noise controls: measure output on silent input
        return max(metrics.db(metrics.rms(y[0])), -120.0)  # floor: stable value for silence

    silence = signals.dc(0.5, 0.0, comp.sr)
    names = comp.param_names()
    checks = [(n, band_gain, None) for n in ("tape",) if n in names]
    checks += [(n, out_level, silence) for n in ("noise",) if n in names]
    # NOTE: fingerprint choice determines which transitions are visible. The
    # tape low-band (60-300) fingerprint only sees the bass step (state0->1); a
    # HF-sensitive fingerprint would also reveal state1->2. The noise fingerprint
    # reveals this competitor CYCLES the 3 hiss states every 0.3 of the 0..1 range.
    for name, mfn, stim in checks:
        r = probe.classify_param(comp, name, mfn, npoints=41, stimulus=stim)
        print(f"  {name:<8} -> {r['kind']:<10} thresholds={[round(t,3) for t in r['thresholds']]}"
              f" states={[round(s,3) for s in r['states']]}")

    # 3) frequency response (stepped small-signal sine)
    print("\n=== frequency response (dB) ===")
    freqs = np.round(np.geomspace(40, 16000, 10)).astype(int)
    fr = metrics.fr_stepped(comp, freqs)
    print("  " + "  ".join(f"{int(f)}:{fr[float(f)]:+.1f}" for f in freqs))

    # 4) THD + harmonic ladder at a couple of levels
    print("\n=== THD @1kHz + harmonic ladder (dB re H1) ===")
    for a in (0.1, 0.5):
        y = comp.render(signals.sine(1000, 0.5, a, comp.sr))
        t = metrics.thd(y[0][int(0.12 * comp.sr):], 1000, comp.sr)[0] * 100
        lad = metrics.harmonic_ladder_db(y[0][int(0.12 * comp.sr):], 1000, comp.sr)
        print(f"  amp={a}: THD={t:4.1f}%  H3={lad[3]:.1f} H5={lad[5]:.1f} H7={lad[7]:.1f}")

    # 5) signal library / Gibbs demo: a 2 kHz sawtooth three ways
    print("\n=== signal library: 2 kHz sawtooth aliasing/Gibbs ===")
    sr = comp.sr
    for method, sigma in (("naive", False), ("additive", False), ("additive", True)):
        x = signals.sawtooth(2000, 0.2, 0.5, sr, method=method, sigma=sigma)
        S = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        f = np.fft.rfftfreq(len(x), 1 / sr)
        # energy that is NOT on a true harmonic of 2 kHz = aliasing proxy
        harmon = np.zeros_like(f, bool)
        for k in range(1, int(sr / 2 / 2000) + 1):
            harmon |= np.abs(f - 2000 * k) < 40
        alias = 20 * np.log10(np.sum(S[~harmon]) / (np.sum(S[harmon]) + 1e-12) + 1e-12)
        peak = float(np.max(np.abs(x)))
        tag = f"{method}{'+sigma' if sigma else ''}"
        print(f"  {tag:<16} alias/harmonic={alias:6.1f} dB   peak={peak:.3f}")


if __name__ == "__main__":
    main()
