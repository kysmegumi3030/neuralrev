"""High-level A/B comparison between a reference (competitor) renderer and a
candidate (your DSP) renderer. Thin orchestration over ``metrics``.

Every function takes two ``Renderer`` objects and returns plain data; ``print_*``
helpers format the common tables. Renderers should share a parameter naming
scheme (normalized values) so the same ``params`` dict drives both.
"""
from __future__ import annotations
import numpy as np

from . import metrics
from .signals import sine


def fr_gap(ref, cand, freqs, params=None, band=(40, 16000), amp=0.02):
    """Stepped-sine FR of both; returns ``dict(freqs, ref, cand, delta, mean, max)``
    where ``delta = cand - ref`` (dB), and mean/max over ``band``."""
    fr = sorted(float(f) for f in freqs)
    a = metrics.fr_stepped(ref, fr, amp=amp, params=params)
    b = metrics.fr_stepped(cand, fr, amp=amp, params=params)
    fa = np.array([a[f] for f in fr]); fb = np.array([b[f] for f in fr])
    d = fb - fa
    m = (np.array(fr) >= band[0]) & (np.array(fr) <= band[1])
    return dict(freqs=fr, ref=fa, cand=fb, delta=d,
                mean=float(np.mean(np.abs(d[m]))), max=float(np.max(np.abs(d[m]))))


def fr_gap_points(ref, cand, freqs, points, base_params=None, band=(40, 16000)):
    """Run ``fr_gap`` at several named operating points.
    ``points`` = list of ``(label, override_params)``. Returns ``{label: result}``."""
    out = {}
    for label, ov in points:
        p = dict(base_params or {}); p.update(ov or {})
        out[label] = fr_gap(ref, cand, freqs, params=p, band=band)
    return out


def thd_grid(ref, cand, freq, sweep_key, sweep_values, amps, base_params=None):
    """THD vs input level for both renderers, over a swept parameter (e.g.
    tape_quality). Returns ``{value: dict(amps, ref, cand)}`` (THD in %)."""
    out = {}
    for v in sweep_values:
        p = dict(base_params or {}); p[sweep_key] = v
        rc = metrics.thd_vs_level(ref, freq, amps, p)
        kc = metrics.thd_vs_level(cand, freq, amps, p)
        out[v] = dict(amps=list(amps),
                      ref=[rc[a] * 100 for a in amps],
                      cand=[kc[a] * 100 for a in amps])
    return out


def waveform_check(ref, cand, signal, params=None):
    """Delay-aligned waveform NRMSE on a deterministic path. Returns
    ``(lag, gain, nrmse_percent)``. Turn off noise/modulation in ``params`` first."""
    yc = ref.render(signal, params)
    yk = cand.render(signal, params)
    return metrics.align_nrmse(yc, yk)


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------
def print_fr_points(results):
    print("=== FR gap (cand - ref) ===")
    for label, r in results.items():
        print(f"  [{label:24s}] mean|Δ|={r['mean']:.2f}  max|Δ|={r['max']:.2f} dB")


def print_thd_grid(grid, sweep_key):
    print(f"=== THD% vs level @1kHz (ref | cand), swept {sweep_key} ===")
    for v, r in grid.items():
        print(f"  {sweep_key}={v:<5} REF  " + " ".join(f"{x:4.1f}" for x in r["ref"]))
        print(f"  {'':>{len(sweep_key)+7}}CAND " + " ".join(f"{x:4.1f}" for x in r["cand"]))


def run_report(ref, cand, freqs, points, thd_key=None, thd_values=None,
               amps=(0.05, 0.1, 0.2, 0.35, 0.5, 0.7, 0.9), base_params=None,
               waveform_signal=None):
    """Convenience: FR points + optional THD grid + optional waveform NRMSE."""
    frp = fr_gap_points(ref, cand, freqs, points, base_params)
    print_fr_points(frp)
    if thd_key and thd_values:
        grid = thd_grid(ref, cand, 1000.0, thd_key, thd_values, amps, base_params)
        print()
        print_thd_grid(grid, thd_key)
    if waveform_signal is not None:
        lag, g, nr = waveform_check(ref, cand, waveform_signal, base_params)
        print(f"\nwaveform: lag={lag} samp  gain={g:.3f}  NRMSE={nr:.1f}%")
    return frp
