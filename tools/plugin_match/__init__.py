"""plugin_match — reusable black-box toolchain for cloning a commercial plugin's
behavior (measure the competitor, fit your own DSP to match). Plugin-agnostic.

Quickstart::

    from plugin_match import VstRenderer, OfflineRenderer, signals, metrics, probe, abtest

    comp = VstRenderer(r"C:\\Program Files\\Common Files\\vst3\\Vendor\\Thing.vst3")
    fr = metrics.fr_stepped(comp, [100, 1000, 10000])          # measure competitor FR
    cand = OfflineRenderer("build/my_dsp.exe", param_order=[...])  # your DSP
    abtest.run_report(comp, cand, freqs=[...], points=[("default", {})])

See README.md for the full workflow and the signal/Gibbs reference.
"""
from . import signals, metrics, fit, probe, abtest  # noqa: F401
from .render import Renderer, VstRenderer, OfflineRenderer  # noqa: F401

__all__ = [
    "signals", "metrics", "fit", "probe", "abtest",
    "Renderer", "VstRenderer", "OfflineRenderer",
]
