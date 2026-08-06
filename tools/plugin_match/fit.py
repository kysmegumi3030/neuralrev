"""Model fitters: turn measured targets into DSP parameters.

- RBJ biquad magnitude (analytic, for building target curves and fitting).
- Shelf fit (low+high) to an FR delta.
- Biquad-cascade fit to an arbitrary FR curve (head-emphasis style).
- Leaky soft-clip saturation fit to a harmonic ladder vs level.
- Linear gain-law fit (normalized param -> dB).

All fits use ``scipy.optimize.least_squares``. Frequencies in Hz, gains in dB.
"""
from __future__ import annotations
import numpy as np
from scipy.optimize import least_squares


# ---------------------------------------------------------------------------
# RBJ biquad magnitude response (Audio EQ Cookbook)
# ---------------------------------------------------------------------------
def rbj_biquad_mag_db(kind, f0, gain_db, Q, freqs, sr=48000):
    """Magnitude (dB) of an RBJ biquad at ``freqs``. ``kind`` in {'ls','pk','hs'}."""
    freqs = np.asarray(freqs, dtype=np.float64)
    A = 10.0 ** (gain_db / 40.0)
    w0 = 2 * np.pi * f0 / sr
    cw, sw = np.cos(w0), np.sin(w0)
    al = sw / (2 * Q)
    if kind == "pk":
        b0, b1, b2 = 1 + al * A, -2 * cw, 1 - al * A
        a0, a1, a2 = 1 + al / A, -2 * cw, 1 - al / A
    elif kind == "ls":
        ts = 2 * np.sqrt(A) * sw * 0.5 * np.sqrt(2)
        b0 = A * ((A + 1) - (A - 1) * cw + ts); b1 = 2 * A * ((A - 1) - (A + 1) * cw)
        b2 = A * ((A + 1) - (A - 1) * cw - ts); a0 = (A + 1) + (A - 1) * cw + ts
        a1 = -2 * ((A - 1) + (A + 1) * cw); a2 = (A + 1) + (A - 1) * cw - ts
    elif kind == "hs":
        ts = 2 * np.sqrt(A) * sw * 0.5 * np.sqrt(2)
        b0 = A * ((A + 1) + (A - 1) * cw + ts); b1 = -2 * A * ((A - 1) + (A + 1) * cw)
        b2 = A * ((A + 1) + (A - 1) * cw - ts); a0 = (A + 1) - (A - 1) * cw + ts
        a1 = 2 * ((A - 1) - (A + 1) * cw); a2 = (A + 1) - (A - 1) * cw - ts
    else:
        raise ValueError(f"unknown biquad kind {kind!r}")
    z = np.exp(-1j * 2 * np.pi * freqs / sr)
    H = (b0 + b1 * z + b2 * z * z) / (a0 + a1 * z + a2 * z * z)
    return 20.0 * np.log10(np.abs(H) + 1e-12)


# ---------------------------------------------------------------------------
# Shelf fit (low + high) to an FR delta
# ---------------------------------------------------------------------------
def fit_shelves(delta_db, freqs, sr=48000, Q=0.7,
                low_bounds=(200, -12, 900, 12), high_bounds=(2000, -12, 12000, 12)):
    """Fit ``low_shelf + high_shelf`` to ``delta_db``. Returns
    ``dict(ls_hz, ls_db, hs_hz, hs_db, maxres)``. Bounds = (fmin,gmin,fmax,gmax)."""
    freqs = np.asarray(freqs, float)
    delta_db = np.asarray(delta_db, float)

    def model(p):
        return (rbj_biquad_mag_db("ls", p[0], p[1], Q, freqs, sr)
                + rbj_biquad_mag_db("hs", p[2], p[3], Q, freqs, sr))
    lo = [low_bounds[0], low_bounds[1], high_bounds[0], high_bounds[1]]
    hi = [low_bounds[2], low_bounds[3], high_bounds[2], high_bounds[3]]
    x0 = [np.sqrt(lo[0] * hi[0]), 0.0, np.sqrt(lo[2] * hi[2]), 0.0]
    r = least_squares(lambda p: model(p) - delta_db, x0, bounds=(lo, hi), max_nfev=6000)
    res = float(np.max(np.abs(model(r.x) - delta_db)))
    return dict(ls_hz=float(r.x[0]), ls_db=float(r.x[1]),
                hs_hz=float(r.x[2]), hs_db=float(r.x[3]), maxres=res)


# ---------------------------------------------------------------------------
# Biquad-cascade fit to an arbitrary FR curve
# ---------------------------------------------------------------------------
# roles: list of (kind, (fmin, fmax)); peaks fit (f, gain, Q), shelves fit (f, gain).
DEFAULT_ROLES = [("ls", (60, 300)), ("pk", (300, 1000)), ("pk", (1000, 3500)),
                 ("pk", (3500, 9000)), ("hs", (4000, 16000))]


def fit_biquad_cascade(target_db, freqs, sr=48000, roles=DEFAULT_ROLES, retries=3):
    """Fit ``gain0 + cascade`` to ``target_db``. Returns
    ``dict(gain, filters=[(kind,f,g,Q)], maxres)``. Fresh random-ish init each
    retry (best kept) to dodge local minima."""
    freqs = np.asarray(freqs, float)
    target_db = np.asarray(target_db, float)

    def unpack(p):
        g0 = p[0]; i = 1; outs = []
        for kind, _ in roles:
            if kind == "pk":
                outs.append((kind, p[i], p[i + 1], p[i + 2])); i += 3
            else:
                outs.append((kind, p[i], p[i + 1], 0.7)); i += 2
        return g0, outs

    def model(p):
        g0, outs = unpack(p)
        out = np.full_like(freqs, g0)
        for kind, f0, g, Q in outs:
            out = out + rbj_biquad_mag_db(kind, f0, g, Q, freqs, sr)
        return out

    lo = [-30.0]; hi = [30.0]; x0 = [-3.0]
    for kind, (flo, fhi) in roles:
        if kind == "pk":
            lo += [flo, -24, 0.3]; hi += [fhi, 24, 3.0]; x0 += [np.sqrt(flo * fhi), 0.0, 1.0]
        else:
            lo += [flo, -30]; hi += [fhi, 30]; x0 += [np.sqrt(flo * fhi), 0.0]
    lo = np.array(lo); hi = np.array(hi); x0 = np.array(x0)

    best = None; best_res = np.inf
    for j in range(max(1, retries)):
        start = x0 if j == 0 else lo + (hi - lo) * ((j * 0.37) % 1.0)
        r = least_squares(lambda p: model(p) - target_db, start, bounds=(lo, hi), max_nfev=8000)
        res = float(np.max(np.abs(model(r.x) - target_db)))
        if res < best_res:
            best_res = res; best = r.x
    g0, outs = unpack(best)
    return dict(gain=float(g0), filters=[(k, float(f), float(g), float(Q)) for k, f, g, Q in outs],
                maxres=best_res)


# ---------------------------------------------------------------------------
# Leaky soft-clip saturation fit
# ---------------------------------------------------------------------------
def softclip(x, drive, n=1.8, leak=0.0):
    """Leaky soft-clip: ``A*s(D*x) + leak*x`` with ``s(u)=u/(1+|u|^n)^(1/n)`` and
    ``A=(1-leak)/drive`` (unity small-signal slope). Vectorised over ``x``."""
    A = (1.0 - leak) / drive
    u = drive * np.asarray(x, float)
    s = u / np.power(1.0 + np.power(np.abs(u), n), 1.0 / n)
    return A * s + leak * np.asarray(x, float)


def _ladder_of(y, freq, sr, harms):
    N = len(y); w = np.hanning(N); S = np.abs(np.fft.rfft(y * w)) * 2 / np.sum(w)

    def amp(f):
        k = int(round(f / sr * N)); k = max(1, min(len(S) - 2, k)); return float(np.max(S[k - 1:k + 2]))
    h = np.array([amp(freq * m) for m in harms])
    return 20.0 * np.log10(np.maximum(h / max(h[0], 1e-12), 1e-9))


def fit_softclip(target_ladder_db, amps, freq=1000.0, sr=48000, n=1.8, harms=(1, 3, 5, 7)):
    """Fit ``(drive, leak)`` of a fixed-``n`` leaky soft-clip so its odd-harmonic
    ladder (dB re H1) matches ``target_ladder_db`` (dict ``{amp: [H1..]dB}``) across
    ``amps``. Returns ``dict(drive, n, leak, maxres)``. Feed the competitor's
    *de-colored* ladder (see ``metrics.decolor``)."""
    harms = list(harms)
    dur_n = int(0.25 * sr)
    t = np.arange(dur_n) / sr

    def eval_ladder(drive, leak):
        out = {}
        for a in amps:
            y = softclip(a * np.sin(2 * np.pi * freq * t), drive, n, leak)
            out[a] = _ladder_of(y, freq, sr, harms)
        return out

    def resid(p):
        drive, leak = p
        lad = eval_ladder(drive, leak)
        r = []
        for a in amps:
            r += list(lad[a][1:] - np.asarray(target_ladder_db[a])[1:])
        return r

    best = None; best_cost = np.inf
    for d0 in (2, 5, 10, 20, 30):
        for b0 in (0.0, 0.15, 0.4, 0.7):
            try:
                s = least_squares(resid, [d0, b0], bounds=([0.5, 0.0], [60, 0.95]), max_nfev=300)
                if s.cost < best_cost:
                    best_cost = s.cost; best = s
            except Exception:  # noqa: BLE001
                pass
    drive, leak = best.x
    lad = eval_ladder(drive, leak)
    res = float(np.mean([np.mean(np.abs(lad[a][1:] - np.asarray(target_ladder_db[a])[1:])) for a in amps]))
    return dict(drive=float(drive), n=float(n), leak=float(leak), maxres=res)


# ---------------------------------------------------------------------------
# Gain law
# ---------------------------------------------------------------------------
def gain_law_fit(x_norm, db_measured):
    """Fit ``db = slope*x + offset``. Returns ``(slope, offset)``."""
    x = np.asarray(x_norm, float); y = np.asarray(db_measured, float)
    A = np.vstack([x, np.ones_like(x)]).T
    slope, offset = np.linalg.lstsq(A, y, rcond=None)[0]
    return float(slope), float(offset)
