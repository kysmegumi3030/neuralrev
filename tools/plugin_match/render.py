"""Renderer abstraction: run a signal through either the competitor VST
(``VstRenderer``, via pedalboard) or your candidate DSP (``OfflineRenderer``,
via a small stdin/stdout exe). A/B code (see ``abtest.py``) is renderer-agnostic.

Convention: signals are ``(channels, N)`` internally; a mono ``(N,)`` input is
broadcast to stereo. Params are dicts of *normalized* values (0..1 for VST3
``raw_value``); each renderer holds a ``defaults`` dict merged with per-call
overrides.
"""
from __future__ import annotations
import abc
import functools
import os
import subprocess
import numpy as np


def _as_stereo(x):
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = np.stack([x, x])
    return x


class Renderer(abc.ABC):
    """Common interface. ``sr`` is the working sample rate."""

    def __init__(self, sr=48000, defaults=None):
        self.sr = int(sr)
        self.defaults = dict(defaults or {})

    def _merge(self, params):
        p = dict(self.defaults)
        if params:
            p.update(params)
        return p

    @abc.abstractmethod
    def render(self, x, params=None):
        """Return ``(channels, N)`` float32 for input ``x`` ((N,) or (2,N))."""

    @abc.abstractmethod
    def param_names(self):
        """List of parameter names this renderer accepts."""

    def reset(self):
        """Optional: clear internal state between renders."""


class VstRenderer(Renderer):
    """Loads a VST3/AU/VST via pedalboard and renders through it.

    ``defaults`` sets baseline normalized ``raw_value`` for every named param;
    override per call in ``render(..., params=...)``.
    """

    def __init__(self, vst_path, sr=48000, defaults=None, reset_each=True):
        super().__init__(sr, defaults)
        self.vst_path = vst_path
        self.reset_each = reset_each
        self._plugin = None

    @property
    def plugin(self):
        if self._plugin is None:
            from pedalboard import load_plugin
            self._plugin = load_plugin(self.vst_path)
        return self._plugin

    def param_names(self):
        return list(self.plugin.parameters.keys())

    def reset(self):
        try:
            self.plugin.reset()
        except Exception:
            pass

    def render(self, x, params=None):
        fx = self.plugin
        for k, v in self._merge(params).items():
            try:
                fx.parameters[k].raw_value = float(v)
            except Exception as e:  # noqa: BLE001
                raise KeyError(f"VST param {k!r}: {e}") from e
        if self.reset_each:
            self.reset()
        xs = _as_stereo(x)
        y = fx(xs, self.sr, reset=self.reset_each)
        return np.asarray(y, dtype=np.float32)


class OfflineRenderer(Renderer):
    """Drives a candidate-DSP exe over the f32 stdin/stdout protocol.

    Protocol (see ``templates/offline_renderer.cpp``):
        argv:   exe  <sr>  <block>  <nch>  <p0> <p1> ... <pN>
        stdin:  interleaved float32 frames  (nch per frame)
        stdout: interleaved float32 frames  (nch per frame)

    ``param_order`` fixes the argv order; ``defaults`` + per-call overrides fill
    the values by name.
    """

    def __init__(self, exe_path, param_order, sr=48000, block=512, defaults=None):
        super().__init__(sr, defaults)
        self.exe_path = exe_path
        self.param_order = list(param_order)
        self.block = int(block)

    def param_names(self):
        return list(self.param_order)

    def render(self, x, params=None):
        p = self._merge(params)
        missing = [k for k in self.param_order if k not in p]
        if missing:
            raise KeyError(f"OfflineRenderer missing params: {missing}")
        xs = _as_stereo(x)
        nch = xs.shape[0]
        inter = xs.T.reshape(-1).astype("<f4")
        args = [self.exe_path, str(self.sr), str(self.block), str(nch)]
        args += [f"{float(p[k]):.6f}" for k in self.param_order]
        r = subprocess.run(args, input=inter.tobytes(), stdout=subprocess.PIPE, check=True)
        y = np.frombuffer(r.stdout, dtype="<f4").reshape(-1, nch).T
        return np.ascontiguousarray(y, dtype=np.float32)
