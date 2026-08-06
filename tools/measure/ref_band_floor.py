"""逐频带的**可达下界**：参考插件与它自己比，在 1/12 oct 平滑口径下的误差。

为什么需要这个：全带下界已测过（1 ms 位移 max 0.10 dB，见 REFERENCE §10），
但那是**全带 max**，被中高频的大量 bin 摊平了。低频的模式极稀
（20–40 Hz 只有一两条模式），1/12 oct 在 25 Hz 处只有约 2 Hz 宽，
**窄于模式间距**，所以平滑在该频段并没有起到「抹掉模式位置」的作用。

若参考自比在 20–40 Hz 就已远超 3 dB，那么该频段的 3 dB 目标对任何
独立实现都不可达（与原始逐 bin 口径被否掉的理由完全同构），
应当照实报告而不是继续拟合。

做法：同插件、同参数，只把冲激位置挪 1 样点 / 16 样点 / 1 ms / 100 ms，
逐频带统计平滑后误差。挪激励只改变 LFO 相位，不改变系统本身。

用法：python3 tools/measure/ref_band_floor.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V                              # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
NFFT = 65536
F = np.fft.rfftfreq(NFFT, 1.0 / SR)

BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000), (2000, 20000)]
SHIFTS = [1, 16, 48, 4800]

P = dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)


def smooth(y, of=1 / 12):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def ir(r, at):
    n = at + int(4.5 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[at] = 1.0
    y = r.render(x, params={f"reverb_{k}": v for k, v in P.items()})
    return y.astype(np.float64)[0][at + REF_LATENCY:]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)

    base = smooth(ir(r, BASE_AT))

    print("参考插件与**它自己**比（只挪冲激位置 → 只差 LFO 相位），")
    print("1/12 oct 平滑后逐频带误差（dB）。这是任何独立实现的下界。\n")
    hdr = f"{'位移':>10} " + " ".join(f"{lo}-{hi}Hz".rjust(13) for lo, hi in BANDS)
    print(hdr)
    print(f"{'':>10} " + " ".join("max / p95".rjust(13) for _ in BANDS))

    floors = np.zeros(len(BANDS))
    for sh in SHIFTS:
        cur = smooth(ir(r, BASE_AT + sh))
        err = np.abs(20 * np.log10(np.maximum(cur, 1e-30) / np.maximum(base, 1e-30)))
        cells = []
        for i, (lo, hi) in enumerate(BANDS):
            m = (F >= lo) & (F <= hi)
            mx, p95 = err[m].max(), np.percentile(err[m], 95)
            floors[i] = max(floors[i], mx)
            cells.append(f"{mx:5.2f} /{p95:5.2f}")
        ms = sh / SR * 1000.0
        print(f"{sh:6d} ({ms:5.2f}ms) " + " ".join(c.rjust(13) for c in cells))

    print("\n结论（取各带最差位移的 max 作为下界）：")
    for (lo, hi), fl in zip(BANDS, floors):
        verdict = "3 dB 目标不可达" if fl > 3.0 else "3 dB 目标有余量"
        print(f"  {lo:5d}–{hi:<6d} Hz  下界 {fl:6.2f} dB   {verdict}")


if __name__ == "__main__":
    main()
