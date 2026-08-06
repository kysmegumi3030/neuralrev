"""echo1 的**绝对起点**：逐样点对账，不用任何估计量。

## 为什么不用重心/互相关

`ref_delay_rounds_ab.py` 报出 echo1 的互相关滞后 −4、重心差 −6.0，即候选的
第一次回声比参考**早**几个样点。但这两个都是**估计量**：重心受窗内电平分布
影响，互相关是整数量化的。要定一个「常数偏移」必须用不含估计量的口径。

取 norm=0.00 ⇒ D = 4800 **精确整数**（没有分数延迟搅混起点），`fb=0`
（只有一次回声，不与后续圈重叠），于是 echo1 的**首个非零样点**就是
「延迟线 + 环内固定路径」的总群延迟起点，逐样点可读、无歧义。
§14.9.4 就是用这个口径定下 16 样点预延迟的。

打印两侧从 D 起的前若干样点，并报「首个超过峰值 1e−6 的位置」。
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
AMP = 1e-2          # fb=0 只有一次回声，可以用大一点的幅度换动态范围
                    # （§14.4：静态饱和在 amp>0.03 才弯，1e-2 仍在线性区）
LFO_PHASE = 0.238423

# ⚠️ 档位的选择就是这个量的口径本身。
#
# D=4800（norm=0.00）虽然是**精确整数**（起点无歧义，§14.9.4 就用它定的 16），
# 但那一档的 LFO 净深度是 2A·|sin(π·D/T)| = 2×3.2755×|sin(π×0.1702)| = **3.40 样点**
# —— echo1 的位置本身就随 LFO 相位摆 ±3.4，两侧各摆各的，读不出常数偏移。
#
# 所以测「常数偏移」必须挑 **LFO 零点档 norm=0.65**（实测深度 0.0122 样点，
# 比邻档小两个数量级，见 DelayTuning.h kMeasLfoAmpSamples）。那一档 echo1 的
# 位置与 LFO 相位无关，两侧之差就是纯粹的固定群延迟之差。
# 代价是 D = T·SR = 28204.5 不是整数，起点有半样点模糊 —— 但那远小于要测的量。
NORM_LFO_NULL = 0.65


def onset(seg: np.ndarray, rel: float = 1.0e-6) -> int:
    """首个 |x| 超过 (峰值 × rel) 的下标。"""
    a = np.abs(np.asarray(seg, float))
    thr = a.max() * rel
    idx = np.nonzero(a > thr)[0]
    return int(idx[0]) if len(idx) else -1


def parabolic_peak(seg: np.ndarray) -> float:
    """抛物线插值的峰位（亚样点）。整数 argmax 在双峰核上会跳格。"""
    a = np.abs(np.asarray(seg, float))
    i = int(np.argmax(a))
    if i <= 0 or i >= len(a) - 1:
        return float(i)
    y0, y1, y2 = a[i - 1], a[i], a[i + 1]
    den = y0 - 2.0 * y1 + y2
    return float(i) if abs(den) < 1e-300 else float(i) + 0.5 * (y0 - y2) / den


def run(norm: float, label: str) -> None:
    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    rp = {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
          "delay_feedback": 0.0, "delay_lowpass": 1.0, "delay_highpass": 0.0,
          "delay_mode": 1.0}
    cp = {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
          "d_timel": norm, "d_timer": norm, "d_feedback": 0.0,
          "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
          "d_lfophase": LFO_PHASE}

    # 先粗定 echo1 大致位置：不假设 norm→D 的换算，直接从渲染结果里找。
    n = AT + 200000
    x = np.zeros(n, dtype=np.float64)
    x[AT] = AMP
    yr = np.asarray(ref.render(x, rp)[0], float)
    yc = np.asarray(cand.render(x, cp)[0], float)

    # echo1 的粗位置 = AT 之后的全局峰（fb=0 ⇒ 只有一次回声）
    pr = int(np.argmax(np.abs(yr[AT + 64:]))) + AT + 64
    pc = int(np.argmax(np.abs(yc[AT + 64:]))) + AT + 64
    a0 = min(pr, pc) - 40

    sr_seg = yr[a0:a0 + 220]
    sc_seg = yc[a0:a0 + 220]

    print(f"\n{'=' * 76}")
    print(f"{label}（norm={norm}, fb=0, amp={AMP}, 窗起点 = AT{a0 - AT:+d}）")
    print(f"{'=' * 76}")
    print("  偏移      参考          候选")
    for k in range(0, 56):
        print(f"  {k:4d}  {sr_seg[k]:+.6e}  {sc_seg[k]:+.6e}")

    orf, ocf = onset(sr_seg), onset(sc_seg)
    ppr, ppc = parabolic_peak(sr_seg), parabolic_peak(sc_seg)
    print(f"\n  首个 >峰值1e−6：参考 {orf}，候选 {ocf}  ⇒ 差 {ocf - orf:+d}")
    print(f"  整数峰位：参考 {int(np.argmax(np.abs(sr_seg)))}，"
          f"候选 {int(np.argmax(np.abs(sc_seg)))}")
    print(f"  抛物线峰位：参考 {ppr:.3f}，候选 {ppc:.3f}  ⇒ 差 {ppc - ppr:+.3f}")
    print(f"  重心：参考 {centroid(sr_seg):.3f}，候选 {centroid(sc_seg):.3f}"
          f"  ⇒ 差 {centroid(sc_seg) - centroid(sr_seg):+.3f}")
    print(f"  峰值：参考 {np.abs(sr_seg).max():.6e}，"
          f"候选 {np.abs(sc_seg).max():.6e}  "
          f"（比 {np.abs(sc_seg).max()/np.abs(sr_seg).max():.4f}）")


def centroid(seg: np.ndarray) -> float:
    w = np.abs(np.asarray(seg, float)) ** 2
    s = w.sum()
    return float(np.dot(np.arange(len(w), dtype=float), w) / s) if s > 0 else float("nan")


def main() -> None:
    # 先量 LFO 零点档 —— 这一档的读数才是「常数偏移」
    run(NORM_LFO_NULL, "echo1 逐样点 @ LFO 零点档（位置与 LFO 相位无关）")
    # 再量整数档作对照：它的差里含 ±3.4 样点的 LFO 摆动，不能当常数用
    run(0.0, "echo1 逐样点 @ 整数档 D=4800（含 ±3.4 样点 LFO 摆动，仅作对照）")


if __name__ == "__main__":
    main()
