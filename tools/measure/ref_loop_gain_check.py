"""环路增益的**直读**：每圈的能量比，参考 vs 候选。

## 要判定的事

`ref_delay_rounds_ab.py` 报出逐圈增益比之比 **0.9952**（g = 参考/候选，
逐圈下降 ⇒ 候选每圈比参考**高** 0.48%）。而 `kMeasLoopFlatGain`（曾名
kFitLoopFlatGain）= **0.995283**，即 0.47% 的平损 —— 两个数吻合到 0.01%。

那个常数当前**没有乘**，理由写在 DelayTuning.h：「FIR 的 DC 增益里已经含了它
（sum ≈ 1.5959 而不是 1.6 = 2×0.8）」。但抽头**已归一到 DC 增益 = 1**
（同一段注释的下一行就这么写着）—— 归一化恰恰把那 0.45% 除掉了。
所以「已经含了」对**原始**抽头成立，对**归一后**的抽头不成立。

## 测法（不依赖任何拟合）

单冲激、fb=1.0、D=4800 整数档、LFO 起相已标定。逐圈取窗算 **rms**，
再取相邻圈的比值 = 该圈的环路增益。参考与候选各自算，然后比。

  * 参考逐圈环路增益应 ≈ 0.80 × 0.995283 = 0.796227（§14.9 的两次独立读数）
  * 候选若少乘那一项，应 ≈ 0.80 × 1.0 = 0.800

这两个预测相差 0.47%，而逐圈比值能读到 0.01% —— 足以判定。
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V          # noqa: E402
from plugin_match import nrev_cand as C         # noqa: E402

SR = 48000
AT = 19200
AMP = 1e-3          # 线性区（§14.4）
LFO_PHASE = 0.238423
NROUND = 14
PRE = 96
WIN = 1400


def loop_gains(y: np.ndarray, d: int) -> np.ndarray:
    """逐圈 rms，再取相邻比值 = 每圈环路增益。"""
    rms = []
    for k in range(1, NROUND + 1):
        a = AT + k * d - PRE
        seg = np.asarray(y, float)[a:a + WIN]
        if len(seg) < WIN:
            break
        rms.append(float(np.sqrt(np.mean(seg ** 2))))
    r = np.array(rms)
    return r[1:] / np.maximum(r[:-1], 1e-300)


def main() -> None:
    d = 4800
    n = AT + (NROUND + 2) * d
    x = np.zeros(n, dtype=np.float64)
    x[AT] = AMP

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    rp = {"delay_drywet": 1.0, "delay_time_l": 0.0, "delay_time_r": 0.0,
          "delay_feedback": 1.0, "delay_lowpass": 1.0, "delay_highpass": 0.0,
          "delay_mode": 1.0}
    cp = {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
          "d_timel": 0.0, "d_timer": 0.0, "d_feedback": 1.0,
          "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
          "d_lfophase": LFO_PHASE}

    gr = loop_gains(np.asarray(ref.render(x, rp)[0], float), d)
    gc = loop_gains(np.asarray(cand.render(x, cp)[0], float), d)

    print(f"\n{'=' * 76}")
    print("逐圈环路增益（rms 相邻比），D=4800, fb=1.0")
    print(f"{'=' * 76}")
    print("  圈→圈    参考       候选      候选/参考")
    for i, (a, b) in enumerate(zip(gr, gc), start=1):
        print(f"  {i:2d}→{i+1:<2d}  {a:.6f}  {b:.6f}  {b / a:.6f}")

    # 稳定段：头两圈受起振/首圈常数影响，取第 3 圈起
    ra, rb = gr[2:], gc[2:]
    print(f"\n  稳定段均值：参考 {ra.mean():.6f}  候选 {rb.mean():.6f}")
    print(f"  比值 {rb.mean() / ra.mean():.6f}"
          f"  ⇒ 候选每圈高 {100 * (rb.mean() / ra.mean() - 1):+.3f}%")

    print(f"\n{'=' * 76}")
    print("与两个既有常数对账")
    print(f"{'=' * 76}")
    print(f"  预测（参考）  0.80 × 0.995283 = {0.80 * 0.995283:.6f}"
          f"   实测 {ra.mean():.6f}")
    print(f"  预测（候选少乘平项）0.80 × 1.0 = {0.80:.6f}"
          f"   实测 {rb.mean():.6f}")
    print(f"\n  若候选实测更靠近 0.800 而参考更靠近 0.7962 ⇒ 平项确实漏乘，")
    print(f"  且它的值就是 {ra.mean() / max(rb.mean(), 1e-300):.6f}"
          f"（对照 kMeasLoopFlatGain = 0.995283）。")


if __name__ == "__main__":
    main()
