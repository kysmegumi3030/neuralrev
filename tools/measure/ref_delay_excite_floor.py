"""验收激励的**猝发长度**该取多少？按地板定，不按读数定。

## 这个问题是怎么冒出来的

`ref_delay_caliber_gap.py` 的 2×2 判明：1100 ms 档上验收读 4.62、诊断读 2.14，
差的**全部**是激励（相位只值 0.01 dB），而且**地板跟着一起动**：

| 激励 | 读数 | 地板(参考自比) | 读数−地板 |
|---|---|---|---|
| Hann 2048（验收现用）| 4.62 | **3.44** | +1.18 |
| 矩形 4800 | 2.14 | **0.94** | +1.20 |
| Hann 4800 | 2.16 | 1.10 | +1.06 |
| 矩形 2048 | 1.96 | 2.57 | −0.61 |

地板 3.44 dB 意味着：**参考与它自己比**，在验收现用的激励下就已经读出 3.44 dB。
判据是 3 dB。仪器本底超过容差 ⇒ 这个激励在这一档上**没有分辨力**，
它报的 4.62 里有多少是失配、有多少是本底，无法区分。

## 为什么可以换激励，而这不是「挑一个好看的数」

换的依据是**地板**，不是我们的读数。地板是参考与自己比出来的，
候选完全不参与 —— 它是仪器的属性。用户定的口径是「65536 点 FFT、
每 bin ≤3 dB」，**没有**规定猝发长度；长度是我自己选的实现细节，
而我当初选 2048 时没有量它的地板。

机制也是清楚的、可预测的：猝发长 L 的谱，相关宽度 ≈ SR/L。
1/6 oct 带（验收侧 oct_frac=1/12 是半宽）在 12 kHz 处宽约 1400 Hz。
L=2048 ⇒ 相关宽度 23.4 Hz ⇒ 带内约 60 个独立样本；
L=2048 且加 Hann ⇒ 等效支撑约 L/2 ⇒ 独立样本再减半。
样本越少，带内 RMS 的估计方差越大，参考自比的散布就越大 ⇒ 地板抬高。
所以「加长猝发 ⇒ 地板下降」是可**预测**的，本脚本验证它是否真的按这条走；
若地板不随长度单调下降，说明我这套机制解释是错的，那就不能换。

## 上界：不能无限加长

分析窗是 y[at : at+65536]，最长档 D=52800。猝发尾巴 + 该档回声必须还在窗内：
at 之后 L + D ≤ 65536 ⇒ L ≤ 12736。取整到 12000 作为扫的上界。

## 同时报什么

1. 各长度的**地板**（参考自比、偏移 1 样点）—— 选长度的唯一依据。
2. 各长度下候选的读数 —— 只用来核对「读数−地板」是否稳定在 ~1.2 dB。
   若某个长度让这个差值突然变大，说明该长度激出了别的东西，要单独看。
3. 对照档 0.65 —— 它本来就过，各长度都应给出小地板。

用法：
    python3 tools/measure/ref_delay_excite_floor.py
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V          # noqa: E402
from plugin_match import nrev_cand as C         # noqa: E402

SR = 48000
AT = 19200
NFFT = 65536
AMP = 1e-3
GATE_DB = -40.0
SEED = 20260806
PHASE = 0.238423

# L ≤ 12736 见模块 docstring（猝发尾 + 最长档回声 52800 必须在 65536 窗内）
LENGTHS = (2048, 3072, 4800, 6144, 8192, 12000)

T_MIN_MS, T_MAX_MS, T_EXP = 100.0, 1100.0, 5.0 / 3.0


def time_ms(norm: float) -> float:
    return T_MIN_MS + (T_MAX_MS - T_MIN_MS) * norm ** T_EXP


def burst(n: int, at: int, length: int, hann: bool) -> np.ndarray:
    x = np.zeros(n)
    rng = np.random.default_rng(SEED)
    w = np.hanning(length) if hann else 1.0
    x[at:at + length] = AMP * w * rng.standard_normal(length)
    return x


def analysis_window(y, at: int, n: int = NFFT) -> np.ndarray:
    y = np.asarray(y, float)
    seg = y[at:at + n]
    if len(seg) < n:
        seg = np.concatenate([seg, np.zeros(n - len(seg))])
    return seg


def ref_params(norm: float, fb: float) -> dict:
    return {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
            "delay_feedback": fb, "delay_lowpass": 1.0, "delay_highpass": 0.0,
            "delay_mode": 1.0}


def cand_params(norm: float, fb: float) -> dict:
    return {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
            "d_timel": norm, "d_timer": norm, "d_feedback": fb,
            "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
            "d_lfophase": PHASE}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--norms", type=float, nargs="*", default=[1.00, 0.90, 0.65])
    ap.add_argument("--fb", type=float, default=0.5)
    ap.add_argument("--hann", type=int, nargs="*", default=[1, 0])
    args = ap.parse_args()

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)
    n = AT + NFFT + SR

    print("按**地板**选验收激励的猝发长度（地板 = 参考自比、激励偏移 1 样点）")
    print("度量一律 nrev_cand.smoothed_spectrum_err_db(floor_db=-40)，判据 3 dB")
    print("预测：地板随长度**单调下降**（相关宽度 ≈SR/L ⇒ 带内独立样本数 ∝L）")
    print("      若不单调，则我的机制解释是错的，不能据此换激励\n")

    for nt in args.norms:
        print("=== norm=%.4f  (%.1f ms) ===" % (nt, time_ms(nt)))
        print("  %-6s %-6s %10s %10s %10s  %s"
              % ("长度", "窗", "地板", "读数", "读数−地板", "地板余量(3dB)"))
        for hn in args.hann:
            for L in LENGTHS:
                x = burst(n, AT, L, bool(hn))
                x1 = burst(n, AT + 1, L, bool(hn))
                yr = analysis_window(ref.render(x, ref_params(nt, args.fb))[0], AT)
                yr1 = analysis_window(
                    ref.render(x1, ref_params(nt, args.fb))[0], AT + 1)
                yc = analysis_window(
                    cand.render(x, cand_params(nt, args.fb))[0], AT)
                fl = C.smoothed_spectrum_err_db(
                    yr, yr1, NFFT, sr=SR, floor_db=GATE_DB)[0]
                gm = C.smoothed_spectrum_err_db(
                    yr, yc, NFFT, sr=SR, floor_db=GATE_DB)[0]
                print("  %-6d %-6s %10.2f %10.2f %10.2f  %s"
                      % (L, "Hann" if hn else "矩形", fl, gm, gm - fl,
                         "×本底超容差" if fl >= 3.0
                         else ("△勉强" if fl >= 1.5 else "✓")))
        print()

    print("选法：取**地板有明确余量**（≤1.5 dB，即容差的一半以内）的最短长度。")
    print("      地板是仪器属性（候选不参与），所以这是按分辨力选，不是按读数选。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
