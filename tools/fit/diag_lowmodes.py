"""低频缺口的第二轮诊断：是**电平**错了，还是**模式位置**错了？

上一轮把 LOW CUT 的拐点从「显示 50 Hz」改成实测的 19.1 Hz 后，
2k–20k 从 7.60 dB 掉到 3.19 dB（HIGH CUT 同步修正的功劳），
但 20–40 Hz 反而从 15.56 涨到 20.29。说明该带的误差**不是**一个
被滤波器压掉的整体电平差 —— 否则放开高通只会变好。

两种可能，处理方式完全不同：
  (L) 电平/斜率错：候选低频整体偏高或偏低 → 继续调滤波器或加权即可；
  (M) 模式位置错：双方都有 ±15 dB 的梳状涟漪，但峰谷**错位** →
      必须对上延迟线长度本身，调电平无用（错位时把电平对齐只是把
      峰对到谷上，误差可能更大）。

判据：
  1. 打印 20–80 Hz 的**逐 bin 细结构**（NFFT 65536 → 0.73 Hz/bin）；
  2. 算「候选−参考」的均值（电平项）与去均值后的 std（错位项）；
  3. 把候选整体平移最优增益后，看残差还剩多少 —— 若几乎不降，即 (M)。
  4. 顺带估模式密度：单位 Hz 内的极值个数，双方对比。

用法：python3 tools/fit/diag_lowmodes.py
"""
from __future__ import annotations

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

P = dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)


def raw_mag(y):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    return np.abs(np.fft.rfft(a))


def smooth(y, of=1 / 12):
    S = raw_mag(y)
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    ref = r.render(x, params={f"reverb_{k}": v for k, v in P.items()}
                   ).astype(np.float64)[0][BASE_AT + REF_LATENCY:]
    cand = NrevRenderer(sr=SR, block=512).render(
        x, params=P).astype(np.float64)[0][BASE_AT:]

    A, B = smooth(ref), smooth(cand)
    dA = 20 * np.log10(np.maximum(A, 1e-30))
    dB_ = 20 * np.log10(np.maximum(B, 1e-30))

    print("NFFT=65536 → 0.73 Hz/bin；1/12 oct 在 25 Hz 只有 ±1.5 Hz ≈ 4 bin，")
    print("即该频段的「平滑」几乎没有抹掉模式位置。\n")
    print(f"{'Hz':>7} {'参考dB':>9} {'候选dB':>9} {'差':>8}")
    m = (F >= 18) & (F <= 82)
    idx = np.where(m)[0]
    for i in idx[::4]:
        print(f"{F[i]:7.2f} {dA[i]:9.2f} {dB_[i]:9.2f} {dB_[i] - dA[i]:8.2f}")

    print("\n—— 分解：电平项 vs 错位项 ——")
    for lo, hi in [(20, 40), (40, 80), (80, 300), (300, 2000)]:
        bm = (F >= lo) & (F <= hi)
        d = dB_[bm] - dA[bm]
        # 最优整体增益即扣掉均值
        print(f"  {lo:5d}–{hi:<5d} Hz  均值 {d.mean():+7.2f} dB   "
              f"去均值后 std {d.std():6.2f}  max|去均值| {np.abs(d - d.mean()).max():6.2f}")

    print("\n判据：若「去均值后 max」仍远大于 3 dB，则错位项主导 ⇒ 必须对模式位置。")

    print("\n—— 模式密度（每 10 Hz 的极值个数，18–120 Hz）——")

    def extrema_per_10hz(curve):
        out = []
        for lo in range(20, 120, 10):
            bm = (F >= lo) & (F < lo + 10)
            c = curve[bm]
            k = int(np.sum((c[1:-1] > c[:-2]) & (c[1:-1] > c[2:])))
            out.append(k)
        return out

    ea, eb = extrema_per_10hz(dA), extrema_per_10hz(dB_)
    print("  频段:  " + " ".join(f"{lo:>4d}" for lo in range(20, 120, 10)))
    print("  参考:  " + " ".join(f"{v:>4d}" for v in ea))
    print("  候选:  " + " ".join(f"{v:>4d}" for v in eb))
    print("\n  参考峰更少 ⇒ 参考的低频模式更稀疏 ⇒ 其最长延迟线比候选**短**；")
    print("  参考峰更多 ⇒ 参考有更长的延迟线（或更多低频模式）。")


if __name__ == "__main__":
    main()
