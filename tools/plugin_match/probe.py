"""Parameter reconnaissance: dump metadata and classify a parameter as
continuous or a stepped switch (with its state values / thresholds).

Many commercial plugins expose a nominally continuous 0..1 parameter that is
really a stepped selector (e.g. a tape-type or noise switch). ``classify_param``
sweeps the parameter, runs a user-supplied ``measure_fn`` (a scalar "fingerprint"
of the effect — a band gain, output level, THD, ...) and detects flat plateaus
separated by jumps.
"""
from __future__ import annotations
import numpy as np


def dump_params(plugin):
    """Return a list of dicts describing every parameter of a pedalboard plugin."""
    rows = []
    for name, p in plugin.parameters.items():
        row = {"name": name}
        for attr in ("min_value", "max_value", "step_size", "num_steps",
                     "default_value", "label", "units"):
            row[attr] = getattr(p, attr, None)
        try:
            row["string_value"] = p.string_value
        except Exception:  # noqa: BLE001
            row["string_value"] = None
        rows.append(row)
    return rows


def sweep_fingerprint(renderer, name, values, measure_fn, base_params=None, stimulus=None):
    """Render at each ``values[i]`` (overriding ``name``) and return the scalar
    ``measure_fn(y)`` per value. ``measure_fn`` takes the (channels,N) output.
    ``stimulus`` is the input signal (default: a low-level broadband multitone;
    pass silence to fingerprint noise/hiss controls)."""
    sig = _probe_signal(renderer) if stimulus is None else stimulus
    fp = []
    for v in values:
        p = dict(base_params or {}); p[name] = float(v)
        fp.append(float(measure_fn(renderer.render(sig, p))))
    return np.asarray(fp)


def _probe_signal(renderer):
    from .signals import multitone
    # broadband-ish low-level multitone; good default fingerprint stimulus
    return multitone([90, 150, 220, 500, 900, 1500, 3000, 7000, 10000],
                     0.5, amp=0.02, sr=renderer.sr)


def classify_param(renderer, name, measure_fn, values=None, base_params=None,
                   jump_db=1.0, npoints=41, stimulus=None):
    """Classify parameter ``name`` as 'stepped' or 'continuous'.

    ``measure_fn(y) -> float`` is a scalar fingerprint (e.g. a band mean in dB, or
    output level). ``stimulus`` overrides the probe input (pass silence for
    noise/hiss controls). Detects plateaus separated by jumps > ``jump_db``.
    Returns::

        dict(kind='stepped'|'continuous', values, fingerprint,
             thresholds=[...], states=[representative values per plateau])

    For a stepped param, ``thresholds`` are the midpoints where the fingerprint
    jumps, and ``states`` are one representative input value per plateau.
    """
    if values is None:
        values = np.round(np.linspace(0.0, 1.0, npoints), 4)
    values = np.asarray(values, float)
    fp = sweep_fingerprint(renderer, name, values, measure_fn, base_params, stimulus)

    d = np.abs(np.diff(fp))
    jump_idx = [i for i, dv in enumerate(d) if dv > jump_db]
    # merge adjacent jump indices (a transition spanning a couple of points)
    thresholds = []
    for i in jump_idx:
        mid = 0.5 * (values[i] + values[i + 1])
        if not thresholds or mid - thresholds[-1] > (values[1] - values[0]) * 1.5:
            thresholds.append(float(mid))

    # a param is "stepped" if there are jumps AND the segments between them are flat
    kind = "continuous"
    states = []
    if thresholds:
        edges = [values[0]] + thresholds + [values[-1]]
        seg_flat = True
        for lo, hi in zip(edges[:-1], edges[1:]):
            m = (values >= lo) & (values <= hi)
            seg = fp[m]
            # representative = middle value of the segment
            mididx = np.where(m)[0]
            states.append(float(values[mididx[len(mididx) // 2]]))
            if seg.size >= 3 and (np.max(seg) - np.min(seg)) > jump_db:
                seg_flat = False
        kind = "stepped" if seg_flat else "continuous"

    return dict(kind=kind, values=values, fingerprint=fp,
                thresholds=thresholds, states=states)
