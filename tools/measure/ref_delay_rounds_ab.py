"""逐圈拆开 fb=1.0 档的残余误差：是时序、是电平、还是形状？

## 为什么需要这个脚本

`ab_delay.py` 报的是整窗一个数（fb=1.0 档现为 21.11 dB）。那个数无法区分
三种完全不同的病：

  * **时序**：每圈滞后累积 ⇒ 晚期回声错位，谱上表现为梳状零点错位；
  * **电平**：每圈增益比不对 ⇒ 逐圈比值单调偏离 1；
  * **形状**：环内滤波器形状不对 ⇒ 单圈就错，且逐圈按 n 次幂放大。

修法互相排斥（时序改延迟量、电平改反馈标量、形状改抽头），所以必须先分开。

## 测法

在 `ab_delay.py` 标定出的**同一个 LFO 起相**上渲染（否则量到的是相位差，
见 §14.6：480 样点错位就给 8.57 dB）。取 D = 4800（norm=0.0，精确整数），
逐圈截窗 [n·D − 64, n·D + 1200)，在窗内：

  * 互相关求整数滞后 ⇒ **时序**；
  * 对齐后解最小二乘标量 ⇒ **电平**；
  * 对齐并配平后的 nrmse ⇒ **形状**（残余）。

关键在于「对齐后再配平、配平后再看残差」这个顺序：不对齐就配平，增益会被
错位压低（fb=1.0 档曾读 gain=0.5009，那不是电平差而是错位的投影）。
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
AT = 19200          # 过起始渐变（§14.10）
AMP = 1e-3          # 线性区（§14.4）
NROUND = 12
PRE = 96
WIN = 1400

# ab_delay.py 第 1 步标定出的全局起相。它是**一个标量**，不随档位变；
# 若改了 DSP 需要重标，这里的值就会与 ab_delay 的输出不一致而暴露出来。
LFO_PHASE = 0.238423


def centroid(seg: np.ndarray) -> float:
    w = np.abs(np.asarray(seg, float)) ** 2
    s = w.sum()
    return float(np.dot(np.arange(len(w), dtype=float), w) / s) if s > 0 else float("nan")


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

    yr = np.asarray(ref.render(x, rp)[0], float)
    yc = np.asarray(cand.render(x, cp)[0], float)

    print(f"\n{'=' * 84}")
    print(f"逐圈拆解（D={d}, fb=1.0, LFO 起相={LFO_PHASE:.6f}）")
    print(f"{'=' * 84}")
    print("  圈   滞后  增益比   nrmse%   参考rms    候选rms   重心差")

    lags, gains, nrmses = [], [], []
    for k in range(1, NROUND + 1):
        a0 = AT + k * d - PRE
        sr_seg = yr[a0:a0 + WIN]
        sc_seg = yc[a0:a0 + WIN]
        if len(sr_seg) < WIN or len(sc_seg) < WIN:
            break

        aa, bb, lag = C.align(sr_seg.copy(), sc_seg.copy())
        g = float(np.dot(aa, bb) / max(np.dot(bb, bb), 1e-30))
        resid = aa - g * bb
        nrmse = 100.0 * float(np.sqrt(np.mean(resid ** 2))
                              / max(np.sqrt(np.mean(aa ** 2)), 1e-30))
        cdiff = centroid(sc_seg) - centroid(sr_seg)

        lags.append(lag); gains.append(g); nrmses.append(nrmse)
        print(f"  {k:2d}  {lag:5d}  {g:6.4f}  {nrmse:7.2f}  "
              f"{np.sqrt(np.mean(sr_seg**2)):.3e}  {np.sqrt(np.mean(sc_seg**2)):.3e}"
              f"  {cdiff:+7.2f}")

    la = np.array(lags, float)
    ga = np.array(gains, float)

    print(f"\n{'=' * 84}")
    print("判读")
    print(f"{'=' * 84}")
    dl = np.diff(la)
    print(f"  滞后逐圈增量：{dl}   均值 {dl.mean():+.3f}")
    print("    → |均值| < 0.5 ⇒ 时序已闭合；否则每圈仍差这么多样点。")
    # 逐圈增益比的**比值**才是「每圈的环路增益之比」；
    # 增益比本身含第 1 圈的一次性因子，不能直接判环路。
    gr = ga[1:] / ga[:-1]
    print(f"\n  增益比逐圈之比：{np.round(gr, 4)}")
    print(f"    均值 {gr.mean():.4f}（1.0 ⇒ 每圈环路增益与参考一致）")
    print(f"    → 偏离 1.0 的量 × 圈数 = 晚期电平差，"
          f"12 圈累计 {20*np.log10(max(gr.mean()**12, 1e-30)):+.2f} dB")
    print(f"\n  nrmse：首圈 {nrmses[0]:.2f}%  末圈 {nrmses[-1]:.2f}%")
    print("    → 若滞后已闭合而 nrmse 仍随圈数增长 ⇒ 形状（抽头/滤波器）问题。")


if __name__ == "__main__":
    main()
