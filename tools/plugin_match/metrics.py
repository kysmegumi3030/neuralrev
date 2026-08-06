"""Measurement metrics for black-box plugin matching (competitor-agnostic).

Frequency response, THD / harmonic structure, THD-vs-level, compression curve,
delay-aligned waveform NRMSE, spectrum tilt, and harmonic de-coloring. Functions
take either raw arrays or a ``Renderer`` (see ``render.py``).

Steady-tone measurements use a single-sided Hann-windowed rFFT and read the peak
of the 3 bins around each target frequency (robust to small frequency drift).
Nonlinear/time-variant plugins make broadband sweeps unreliable for FR, so prefer
``fr_stepped`` (one small-signal sine per point).
"""
from __future__ import annotations
import numpy as np

from .signals import sine


def rms(x):
    x = np.asarray(x, dtype=np.float64)
    return float(np.sqrt(np.mean(x * x) + 1e-30))


def db(x):
    return 20.0 * np.log10(np.maximum(np.abs(np.asarray(x, float)), 1e-12))


def _mono(y):
    y = np.asarray(y, dtype=np.float64)
    return y[0] if y.ndim > 1 else y


def bin_amp(y, freq, sr=48000, half_bins=1):
    """Peak amplitude near ``freq`` (Hann, single-sided, amplitude-normalised)."""
    y = _mono(y)
    n = len(y)
    w = np.hanning(n)
    S = np.abs(np.fft.rfft(y * w)) * 2.0 / np.sum(w)
    k = int(round(freq / sr * n))
    k = max(half_bins, min(len(S) - half_bins - 1, k))
    return float(np.max(S[k - half_bins:k + half_bins + 1]))


def fr_stepped(renderer, freqs, amp=0.02, dur=0.3, skip=0.12, params=None):
    """Stepped-sine frequency response. Returns dict ``{freq: gain_dB}``.

    Small ``amp`` keeps the plugin ~linear; ``skip`` discards startup transient.
    """
    out = {}
    for f in freqs:
        y = _mono(renderer.render(sine(float(f), dur, amp, renderer.sr), params))
        y = y[int(skip * renderer.sr):]
        out[float(f)] = 20.0 * np.log10(bin_amp(y, f, renderer.sr) / amp + 1e-12)
    return out


def fr_broadband(x_in, y_out, sr=48000, nfft=1 << 15):
    """Transfer magnitude via averaged periodogram ratio (LTI systems only)."""
    from scipy.signal import welch
    f, Pxx = welch(_mono(x_in), sr, nperseg=nfft, noverlap=nfft // 2)
    _, Pyy = welch(_mono(y_out), sr, nperseg=nfft, noverlap=nfft // 2)
    return f, np.sqrt(Pyy / np.maximum(Pxx, 1e-20))


def harmonic_profile(y, freq, sr=48000, nharm=8):
    """Amplitudes of harmonics 1..nharm as ``{h: amp}``."""
    y = _mono(y)
    return {h: bin_amp(y, freq * h, sr) for h in range(1, nharm + 1)}


def thd(y, freq, sr=48000, nharm=8):
    """Total harmonic distortion. Returns ``(ratio, fundamental, harm_rms)``."""
    prof = harmonic_profile(y, freq, sr, nharm)
    fund = max(prof[1], 1e-12)
    harm = np.sqrt(sum(prof[h] ** 2 for h in range(2, nharm + 1)))
    return float(harm / fund), fund, float(harm)


def harmonic_ladder_db(y, freq, sr=48000, nharm=8):
    """Harmonics 1..nharm in dB relative to the fundamental (H1=0). FR of the
    fundamental cancels, so this compares distortion *character* across renderers."""
    prof = harmonic_profile(y, freq, sr, nharm)
    h1 = max(prof[1], 1e-12)
    return {h: 20.0 * np.log10(max(prof[h], 1e-12) / h1) for h in range(1, nharm + 1)}


def thd_vs_level(renderer, freq, amps, params=None, dur=0.3, skip=0.12, nharm=8):
    """THD at ``freq`` for each input amplitude. Returns ``{amp: thd_ratio}``."""
    out = {}
    for a in amps:
        y = _mono(renderer.render(sine(freq, dur, a, renderer.sr), params))
        out[a] = thd(y[int(skip * renderer.sr):], freq, renderer.sr, nharm)[0]
    return out


def compression_curve(renderer, freq, amps, params=None, dur=0.3, skip=0.12):
    """Output RMS and gain (dB) vs input amplitude. Returns
    ``{amp: (in_dB, out_dB, gain_dB)}`` — reveals limiting/compression."""
    out = {}
    for a in amps:
        x = sine(freq, dur, a, renderer.sr)
        y = _mono(renderer.render(x, params))[int(skip * renderer.sr):]
        indb = 20.0 * np.log10(rms(x) + 1e-30)
        odb = 20.0 * np.log10(rms(y) + 1e-30)
        out[a] = (indb, odb, odb - indb)
    return out


def align_nrmse(a, b):
    """Cross-correlation-align ``b`` to ``a``, best-fit scalar gain, then NRMSE.
    Returns ``(lag_samples, gain, nrmse_percent)``. Use on deterministic paths
    (noise/modulation off) — good for confirming waveform-level agreement."""
    from scipy.signal import correlate
    a = _mono(a)
    b = _mono(b)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    lag = int(np.argmax(correlate(a, b, mode="full")) - (n - 1))
    if lag > 0:
        a2, b2 = a[lag:], b[:n - lag]
    elif lag < 0:
        a2, b2 = a[:n + lag], b[-lag:]
    else:
        a2, b2 = a, b
    m = min(len(a2), len(b2))
    a2, b2 = a2[m // 4:m], b2[m // 4:m]  # drop startup quarter
    g = float(np.dot(a2, b2) / (np.dot(b2, b2) + 1e-20))
    err = np.sqrt(np.mean((a2 - g * b2) ** 2))
    ref = np.sqrt(np.mean(a2 ** 2))
    return lag, g, float(err / (ref + 1e-20) * 100.0)


def band_mean_db(y, x, sr, lo, hi):
    """Mean transfer magnitude (dB) of ``y`` relative to ``x`` over [lo,hi] Hz."""
    y = _mono(y); x = _mono(x)
    n = len(y)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    Sy = np.abs(np.fft.rfft(y)); Sx = np.abs(np.fft.rfft(x))
    m = (f >= lo) & (f <= hi)
    return 20.0 * np.log10(np.mean(Sy[m]) / max(np.mean(Sx[m]), 1e-9))


def spectrum_tilt(y, sr, bands):
    """Mean magnitude (dB) of ``y`` in each (lo,hi) band — coarse spectral shape
    (e.g. white vs pink noise). Returns list of dB, one per band."""
    y = _mono(y)
    n = len(y)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    S = np.abs(np.fft.rfft(y * np.hanning(n)))
    return [20.0 * np.log10(np.mean(S[(f >= lo) & (f <= hi)]) + 1e-12) for lo, hi in bands]


def decolor(harm_amps, fr_gain):
    """Remove a renderer's linear FR from measured harmonic amplitudes, isolating
    the nonlinearity's *pre-filter* harmonic ladder. ``harm_amps`` and ``fr_gain``
    are dicts keyed by harmonic number (fr_gain = linear gain at that harmonic's
    frequency). Returns de-colored ``{h: amp}``."""
    return {h: harm_amps[h] / max(fr_gain.get(h, 1.0), 1e-9) for h in harm_amps}
