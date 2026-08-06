"""逐频带误差速报（不改任何常数，只测当前落点）。

主口径：1/12 oct 平滑后逐 bin 误差（dB），见 nrev_cand.smoothed_spectrum_err_db。
逐带下界（参考自比）由 tools/measure/ref_band_floor.py 给出：
    20–40 Hz 0.35 / 40–80 1.15 / 80–300 1.38 / 300–2k 1.72 / 2k–20k 1.05 dB
故 3 dB 目标在每个带上都有余量，缺口都是实现问题。

用法：python3 tools/fit/band_report.py [--points default,decay-min,decay-hi,...]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
NFFT = 65536
F = np.fft.rfftfreq(NFFT, 1.0 / SR)

BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000), (2000, 20000)]
FLOOR = [0.35, 1.15, 1.38, 1.72, 1.05]

POINTS = {
    "default":    dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0),
    "decay-min":  dict(drywet=1.0, predelay=0.5, decay=0.0, lowcut=0.0, highcut=1.0),
    "decay-hi":   dict(drywet=1.0, predelay=0.5, decay=0.8, lowcut=0.0, highcut=1.0),
    "lowcut-mid": dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.5, highcut=1.0),
    "highcut-mid": dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=0.5),
    "predelay-hi": dict(drywet=1.0, predelay=0.9, decay=0.5, lowcut=0.0, highcut=1.0),
}

_ref: dict[str, np.ndarray] = {}


def smooth(y, of=1 / 12):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def ref_ir(r, name, p):
    if name not in _ref:
        n = BASE_AT + int(4.0 * SR)
        x = np.zeros(n, dtype=np.float32)
        x[BASE_AT] = 1.0
        y = r.render(x, params={f"reverb_{k}": v for k, v in p.items()})
        _ref[name] = y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]
    return _ref[name]


def cand_ir(p):
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return c.render(x, params=p).astype(np.float64)[0][BASE_AT:]


def main():
    ap = argparse.ArgumentParser()
    # 缺省 = POINTS 全集，不写死名单。
    #
    # 这里曾写死过一份 5 档名单（漏 predelay-hi），而 predelay-hi 恰是
    # 40–80 Hz 的最差档（13.53 vs 次差 12.92）⇒ 所有标称「6 档最差」的
    # 数字都系统性偏乐观，且 fit_tilt.py 的守卫在看不见风险的地方空转。
    # 那次是靠人工补 --points 修的，于是同一个洞在下一轮又出现了一次。
    # 用全集当缺省是唯一不会随 POINTS 增长而再次失配的写法：
    # 以后往 POINTS 里加档位会自动进入验收，不需要记得改这一行。
    ap.add_argument("--points", default=",".join(POINTS))
    a = ap.parse_args()
    names = [s.strip() for s in a.points.split(",") if s.strip()]

    r = V.Vst3RefRenderer(sr=SR, block=512)

    print(f"{'档位':>13} {'全带max':>8} " +
          " ".join(f"{lo}-{hi}".rjust(13) for lo, hi in BANDS))
    print(f"{'':>13} {'':>8} " + " ".join("max / p95".rjust(13) for _ in BANDS))

    agg = np.zeros(len(BANDS))
    allmax = 0.0
    for nm in names:
        p = POINTS[nm]
        A, B = smooth(ref_ir(r, nm, p)), smooth(cand_ir(p))
        err = np.abs(20 * np.log10(np.maximum(B, 1e-30) / np.maximum(A, 1e-30)))
        m = (F >= 20) & (F <= 20000)
        gmax = err[m].max()
        allmax = max(allmax, gmax)
        cells = []
        for i, (lo, hi) in enumerate(BANDS):
            bm = (F >= lo) & (F <= hi)
            mx, p95 = err[bm].max(), np.percentile(err[bm], 95)
            agg[i] = max(agg[i], mx)
            cells.append(f"{mx:5.2f} /{p95:5.2f}")
        flag = "✓" if gmax <= 3.0 else "✗"
        print(f"{nm:>13} {gmax:7.2f}{flag} " + " ".join(c.rjust(13) for c in cells))

    print(f"\n各带最差 max vs 可达下界：")
    for (lo, hi), v, fl in zip(BANDS, agg, FLOOR):
        gap = v - fl
        print(f"  {lo:5d}–{hi:<6d} Hz  实测 {v:6.2f}  下界 {fl:4.2f}  "
              f"余量缺口 {gap:+6.2f}  {'✓' if v <= 3.0 else '✗'}")
    print(f"\n全档全带 max = {allmax:.2f} dB  （口径 ≤3 dB）")


if __name__ == "__main__":
    main()
