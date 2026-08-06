# plugin_match

A reusable, plugin-agnostic **black-box measurement toolchain** for cloning a
commercial plugin's behavior: feed test signals into the competitor, measure its
response, then fit your own independent DSP to match.

This is **behavioral measurement, not decompilation** — you observe acoustic
input→output behavior of a plugin you are licensed to run and write your own DSP
to reproduce the measured response. No competitor code is read or copied.

## Install

```
pip install -r requirements.txt      # numpy, scipy, pedalboard
```

## Quickstart

```python
from plugin_match import VstRenderer, OfflineRenderer, signals, metrics, probe, abtest

# 1. wrap the competitor (any VST3/AU/VST pedalboard can load)
comp = VstRenderer(r"C:\Program Files\Common Files\vst3\Vendor\Thing.vst3", sr=48000)

# 2. measure it
fr  = metrics.fr_stepped(comp, [100, 1000, 10000])          # frequency response
thd = metrics.thd(comp.render(signals.sine(1000, 0.5, 0.5))[0], 1000)

# 3. wrap YOUR DSP (built from templates/offline_renderer.cpp)
cand = OfflineRenderer("build/my_dsp.exe",
                       param_order=["in_gain", "drive", "tone", ...])

# 4. A/B report
abtest.run_report(comp, cand,
                  freqs=[40, 100, 1000, 10000],
                  points=[("default", {}), ("hot", {"drive": 0.9})])
```

Run the worked example against DAW Cassette:

```
python examples/example_ab.py
```

## The clone workflow (6 steps)

1. **Wrap the competitor** with `VstRenderer`; set a `defaults` dict of its
   normalized parameter values.
2. **Probe parameters** with `probe.dump_params` + `probe.classify_param` — find
   which nominally-continuous params are actually **stepped switches** (tape type,
   noise mode, …) and their thresholds/states.
3. **Characterize** with `metrics`: `fr_stepped` (per-parameter FR), `thd_vs_level`
   / `harmonic_ladder_db` (distortion character), `compression_curve` (limiting),
   gain law. Use `decolor` to isolate the nonlinearity from the linear FR.
4. **Build your candidate DSP** and expose it to Python by compiling
   `templates/offline_renderer.cpp` against your shipped DSP header → `OfflineRenderer`.
5. **Fit** with `fit`: `fit_biquad_cascade` / `fit_shelves` for FR, `fit_softclip`
   for saturation (matched to the de-colored harmonic ladder), `gain_law_fit`.
   Emit the fitted constants into your project's own targets header (project-side).
6. **Verify** with `abtest.run_report` (FR gap / THD grid / waveform NRMSE) and
   iterate until within your fidelity target.

## Modules

| module | purpose |
|---|---|
| `signals`  | sine, impulse, DC, white/pink noise, log-sweep (+inverse), multitone, sawtooth, square |
| `render`   | `Renderer` ABC, `VstRenderer` (pedalboard), `OfflineRenderer` (f32 stdin/stdout exe) |
| `metrics`  | FR (stepped + broadband), THD, harmonic profile/ladder, THD-vs-level, compression curve, delay-aligned NRMSE, spectrum tilt, de-color |
| `fit`      | RBJ biquad magnitude, shelf fit, biquad-cascade fit, leaky soft-clip saturation fit, gain-law fit |
| `probe`    | parameter metadata dump, stepped/continuous classifier |
| `abtest`   | high-level FR/THD/waveform A/B report |

## Signal reference & the Gibbs phenomenon

`sawtooth` and `square` take `method` (+ `sigma`) to control the trade-off between
aliasing and Gibbs ringing — pick per test:

| call | aliasing | Gibbs ringing | use when |
|---|---|---|---|
| `method='naive'` | **yes** (ideal wave sampled) | none | you want a true discontinuous edge and don't care about aliasing |
| `method='additive'` | none (band-limited) | ~9% overshoot | clean spectra for FR/THD (default) |
| `method='additive', sigma=True` | none | **suppressed** (Lanczos σ) | you want band-limited *and* smooth (no ringing) |
| `method='polyblep'` | none | minimal | anti-aliased time-domain edge (duty=0.5) |

Other signals: `sine`, `impulse`, `dc`, `white_noise(dist='uniform'|'gauss')`,
`pink_noise`, `log_sweep` (returns `(x, inverse)` for IR extraction),
`multitone(phase='schroeder'|'random')`.

## Notes

- Signal arrays are mono `(N,)`; renderers broadcast to stereo and return
  `(channels, N)`.
- Params are dicts of **normalized** values (VST3 `raw_value`, 0..1). Give both
  renderers the same naming scheme so one `params` dict drives both.
- Nonlinear / time-variant plugins make broadband sweeps unreliable for FR — use
  `fr_stepped` (one small-signal sine per point).
- The `OfflineRenderer` protocol and the C++ template keep your **shipped** DSP as
  the single source of truth (you measure the exact code that ships).
