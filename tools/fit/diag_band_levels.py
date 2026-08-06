"""各频带的**绝对能量占比**：低频误差在听感与口径上的实际权重。

动机：20–40 Hz 的误差数字最大（max 20 dB），但如果参考在该带的能量
比中频低几十 dB，那这些 bin 接近数值地板，误差的**物理意义**与
300–2000 Hz 的同等数字完全不同。原始逐 bin 度量
（nrev_cand.spectrum_err_db）本来就带 −80 dB 的电平门限，
而平滑口径目前对 20 Hz–20 kHz **一视同仁**，没有门限。

本脚本只报告事实，不改口径：
  1. 参考 IR 各带的能量占比与相对峰值电平；
  2. 各带在「参考自比下界」之上的实际余量；
  3. 若给平滑口径加一个与原始口径同样的 −80 dB 门限，各带还剩多少 bin。

用法：python3 tools/fit/diag_band_levels.py
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
P = dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    ref = r.render(x, params={f"reverb_{k}": v for k, v in P.items()}
                   ).astype(np.float64)[0][BASE_AT + REF_LATENCY:]

    a = np.zeros(NFFT)
    a[:min(len(ref), NFFT)] = ref[:NFFT]
    S = np.abs(np.fft.rfft(a))
    P2 = S ** 2
    tot = P2[(F >= 20) & (F <= 20000)].sum()
    peak_db = 20 * np.log10(max(S.max(), 1e-30))

    print("参考 IR（默认档位）各带的绝对权重：\n")
    print(f"{'频带':>14} {'能量占比':>10} {'峰值电平':>10} {'中位电平':>10} "
          f"{'相对峰值':>10}")
    for lo, hi in BANDS:
        m = (F >= lo) & (F <= hi)
        frac = P2[m].sum() / tot
        pk = 20 * np.log10(max(S[m].max(), 1e-30))
        med = 20 * np.log10(max(np.median(S[m]), 1e-30))
        print(f"{lo:6d}–{hi:<6d} {frac * 100:9.3f}% {pk:10.2f} {med:10.2f} "
              f"{pk - peak_db:10.2f}")

    print(f"\n全谱峰值 = {peak_db:.2f} dB（20 Hz–20 kHz 归一基准）")

    print("\n若沿用原始口径的 −80 dB 门限（相对全谱峰值），各带存活 bin：")
    for lo, hi in BANDS:
        m = (F >= lo) & (F <= hi)
        db = 20 * np.log10(np.maximum(S[m], 1e-30)) - peak_db
        print(f"  {lo:6d}–{hi:<6d} Hz  {int((db > -80).sum()):6d} / {int(m.sum()):6d} "
              f"存活 ({(db > -80).mean() * 100:5.1f}%)   最低 {db.min():7.2f} dB")

    print("\n结论：如果低频带的能量占比与相对电平都不低，那 20 dB 的误差")
    print("      就是实打实的音色偏差，不能用「接近地板」来解释掉。")


if __name__ == "__main__":
    main()
