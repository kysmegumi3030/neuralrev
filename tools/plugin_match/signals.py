"""Test-signal generators for black-box plugin measurement.

All generators return a mono ``np.ndarray`` (float32) of length ``round(dur*sr)``
unless noted. Feed them to a Renderer (see ``render.py``); mono is broadcast to
stereo there.

Sawtooth/square offer explicit control over the **Gibbs phenomenon** via
``method`` (+ ``sigma``):

    method='naive'                ideal wave (2*(f*t mod 1)-1 / sign(sin)).
                                  Full-bandwidth -> ALIASES when sampled, but has
                                  no reconstruction ringing. Use when Gibbs must
                                  be absent (e.g. a "true" reference edge).
    method='additive'             band-limited Fourier sum up to Nyquist.
                                  ANTI-ALIASED, but shows ~9% Gibbs overshoot at
                                  discontinuities. Default (clean spectra).
    method='additive', sigma=True Lanczos sigma-factors per harmonic -> SUPPRESSES
                                  Gibbs ringing (smoothed band-limited).
    method='polyblep'             time-domain + polyBLEP correction; anti-aliased
                                  with minimal ringing (duty=0.5 only).
"""
from __future__ import annotations
import numpy as np

DEFAULT_SR = 48000


def _n(dur, sr):
    return int(round(dur * sr))


def _t(dur, sr):
    return np.arange(_n(dur, sr), dtype=np.float64) / sr


def sine(freq, dur, amp=0.5, sr=DEFAULT_SR, phase=0.0):
    """Pure sine at ``freq`` Hz."""
    t = _t(dur, sr)
    return (amp * np.sin(2 * np.pi * freq * t + phase)).astype(np.float32)


def dc(dur, amp=1.0, sr=DEFAULT_SR):
    """Constant (DC) level."""
    return np.full(_n(dur, sr), amp, dtype=np.float32)


def impulse(dur=None, n=None, amp=1.0, pos=0, sr=DEFAULT_SR):
    """Unit impulse: single nonzero sample at ``pos``. Give ``dur`` or ``n``."""
    length = n if n is not None else _n(dur if dur is not None else 8192 / sr, sr)
    x = np.zeros(int(length), dtype=np.float32)
    x[int(pos)] = amp
    return x


def white_noise(dur, amp=0.5, sr=DEFAULT_SR, seed=0, dist="uniform"):
    """White noise. ``dist`` = 'uniform' ([-amp,amp]) or 'gauss' (std=amp)."""
    rng = np.random.default_rng(seed)
    n = _n(dur, sr)
    if dist == "gauss":
        x = rng.standard_normal(n) * amp
    else:
        x = rng.uniform(-1.0, 1.0, n) * amp
    return x.astype(np.float32)


def pink_noise(dur, amp=0.5, sr=DEFAULT_SR, seed=0):
    """Pink (~ -3 dB/oct) noise via the Paul Kellet economy filter."""
    rng = np.random.default_rng(seed)
    n = _n(dur, sr)
    w = rng.uniform(-1.0, 1.0, n)
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        wi = w[i]
        b0 = 0.99886 * b0 + wi * 0.0555179
        b1 = 0.99332 * b1 + wi * 0.0750759
        b2 = 0.96900 * b2 + wi * 0.1538520
        b3 = 0.86650 * b3 + wi * 0.3104856
        b4 = 0.55000 * b4 + wi * 0.5329522
        b5 = -0.7616 * b5 - wi * 0.0168980
        out[i] = b0 + b1 + b2 + b3 + b4 + b5 + b6 + wi * 0.5362
        b6 = wi * 0.115926
    out /= np.max(np.abs(out)) + 1e-12
    return (out * amp).astype(np.float32)


def log_sweep(f0, f1, dur, amp=0.5, sr=DEFAULT_SR):
    """Farina exponential sine sweep. Returns ``(x, inverse)`` where convolving
    the output with ``inverse`` yields the (linear) impulse response."""
    n = _n(dur, sr)
    t = np.arange(n) / sr
    L = dur / np.log(f1 / f0)
    x = amp * np.sin(2 * np.pi * f0 * L * (np.exp(t / L) - 1.0))
    # inverse filter: time-reversed sweep with amplitude envelope for exp sweep
    inv = x[::-1] * np.exp(-t / L)
    inv = inv / (np.sum(x * x) + 1e-20)
    return x.astype(np.float32), inv.astype(np.float32)


def multitone(freqs, dur, amp=0.5, sr=DEFAULT_SR, phase="schroeder"):
    """Sum of sines. ``phase`` = 'schroeder' (low crest factor) or 'random'."""
    freqs = list(freqs)
    t = _t(dur, sr)
    m = len(freqs)
    if phase == "random":
        ph = np.random.default_rng(0).uniform(0, 2 * np.pi, m)
    else:  # Schroeder phases minimise peak-to-average
        ph = np.array([-np.pi * k * (k - 1) / m for k in range(m)])
    x = sum(np.sin(2 * np.pi * f * t + ph[i]) for i, f in enumerate(freqs))
    x = x / (np.max(np.abs(x)) + 1e-12) * amp
    return x.astype(np.float32)


# ---------------------------------------------------------------------------
# Sawtooth / square with Gibbs control
# ---------------------------------------------------------------------------
def _sigma_factors(k):
    """Lanczos sigma factors for harmonics 1..K (k = array of harmonic numbers)."""
    K = k[-1]
    return np.sinc(k / (K + 1.0))  # np.sinc(x) = sin(pi x)/(pi x)


def _max_harm(freq, sr, nharm):
    kmax = int(np.floor((sr / 2.0) / freq))
    kmax = max(1, kmax)
    return kmax if nharm is None else min(kmax, int(nharm))


def sawtooth(freq, dur, amp=0.5, sr=DEFAULT_SR, method="additive",
             nharm=None, sigma=False, phase=0.0):
    """Sawtooth. See module docstring for ``method``/``sigma`` (Gibbs control)."""
    t = _t(dur, sr)
    if method == "naive":
        ph = (freq * t + phase / (2 * np.pi)) % 1.0
        x = 2.0 * ph - 1.0
    elif method == "polyblep":
        x = _polyblep_saw(freq, t, sr, phase)
    elif method == "additive":
        K = _max_harm(freq, sr, nharm)
        k = np.arange(1, K + 1)
        w = _sigma_factors(k) if sigma else np.ones_like(k, dtype=float)
        # saw = -(2/pi) * sum (-1)^k sin(2 pi k f t)/k
        harm = (((-1.0) ** k) * w / k)[:, None] * np.sin(
            2 * np.pi * k[:, None] * freq * t[None, :] + phase)
        x = -(2.0 / np.pi) * harm.sum(axis=0)
    else:
        raise ValueError(f"unknown method {method!r}")
    return (amp * x).astype(np.float32)


def square(freq, dur, amp=0.5, sr=DEFAULT_SR, duty=0.5, method="additive",
           nharm=None, sigma=False, phase=0.0):
    """Square / rectangular pulse. See module docstring for Gibbs control.
    ``duty`` != 0.5 only supported for 'naive' and 'additive'."""
    t = _t(dur, sr)
    if method == "naive":
        ph = (freq * t + phase / (2 * np.pi)) % 1.0
        x = np.where(ph < duty, 1.0, -1.0)
    elif method == "polyblep":
        if abs(duty - 0.5) > 1e-6:
            raise ValueError("polyblep square supports duty=0.5 only")
        x = _polyblep_square(freq, t, sr, phase)
    elif method == "additive":
        K = _max_harm(freq, sr, nharm)
        k = np.arange(1, K + 1)
        w = _sigma_factors(k) if sigma else np.ones_like(k, dtype=float)
        # general pulse Fourier series: (2/pi) sum sin(pi k duty)/k * cos(2 pi k f t)
        # + DC (2*duty-1). For duty=0.5 -> odd-harmonic (4/pi) sum sin/k.
        amp_k = (2.0 / (np.pi * k)) * np.sin(np.pi * k * duty) * w
        harm = amp_k[:, None] * np.cos(2 * np.pi * k[:, None] * freq * t[None, :] + phase)
        x = (2.0 * duty - 1.0) + 2.0 * harm.sum(axis=0)
    else:
        raise ValueError(f"unknown method {method!r}")
    return (amp * x).astype(np.float32)


def _polyblep(t, dt):
    """polyBLEP residual for a normalised phase ``t`` in [0,1) with step ``dt``."""
    y = np.zeros_like(t)
    # start of period
    m = t < dt
    tt = t[m] / dt
    y[m] = tt + tt - tt * tt - 1.0
    # end of period
    m = t > 1.0 - dt
    tt = (t[m] - 1.0) / dt
    y[m] = tt * tt + tt + tt + 1.0
    return y


def _polyblep_saw(freq, t, sr, phase):
    dt = freq / sr
    ph = (freq * t + phase / (2 * np.pi)) % 1.0
    x = 2.0 * ph - 1.0
    x -= _polyblep(ph, dt)
    return x


def _polyblep_square(freq, t, sr, phase):
    dt = freq / sr
    ph = (freq * t + phase / (2 * np.pi)) % 1.0
    x = np.where(ph < 0.5, 1.0, -1.0)
    x += _polyblep(ph, dt)
    x -= _polyblep((ph + 0.5) % 1.0, dt)
    return x
