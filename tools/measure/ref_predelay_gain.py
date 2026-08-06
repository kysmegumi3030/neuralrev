"""PRE-DELAY 的真相：逐档测「早期区相对基准的标量增益 + 残差是否为延迟拷贝」。

前两轮的教训：
  * 并联双路模型在两端（pv=0 / pv≈1）残差 ~3e-4，中间档 0.22–0.57 → 不是简单双路。
  * 但 pv=0 的 IR **逐样点** = 2.000004 × pv=1 的 IR（残差 3e-4），这个事实极强，
    说明「两路完全重合 → 2 倍」与「两路完全分离 → 1 倍」都成立，
    错的是中间档「第二路 = w 的纯延迟拷贝」这一步。

本轮改为**不假设**第二路形状，直接把它解出来：

  设  y_pv(t) = α_pv · w(t) + s_pv(t)      t ∈ [0, 9600)
  其中 w(t) = IR(pv=1)(t)（这一段确定只有第一路），
  α_pv 由最小二乘给出，s_pv 是残差 = 「第二路在早期区的贡献」。

然后逐档报告：
  * α_pv 随 pv 的变化（第一路增益是否恒定）
  * s_pv 的起点（第二路何时进场；应 ≈ D(pv)）
  * s_pv 与 w 的最佳延迟拷贝拟合度（第二路是否**就是** w 的延迟）

用法：python3 tools/measure/ref_predelay_gain.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
LATENCY = 51
IMPULSE_AT = int(2.0 * SR)
D_MAX = 9600


def ir(r, params, tail_sec=6.0):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[:, IMPULSE_AT + LATENCY:]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    wet = {"reverb_drywet": 1.0}

    w_full = ir(r, {**wet, "reverb_predelay": 1.0})[0]
    w = w_full[:D_MAX]

    print("逐档：早期区 y ≈ α·w 的 α、残差、残差起点")
    print("    pv   D(样点)      α      残差/rms   残差起点(样点/ms)")
    for pv in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        y = ir(r, {**wet, "reverb_predelay": pv})[0][:D_MAX]
        d_par = int(round(V.predelay_ms(pv) / 1000 * SR))
        alpha = float(np.dot(y, w) / max(np.dot(w, w), 1e-30))
        s = y - alpha * w
        rel = float(np.sqrt(np.mean(s ** 2)) / max(np.sqrt(np.mean(y ** 2)), 1e-30))
        thr = np.abs(s).max() * 1e-2
        nz = np.nonzero(np.abs(s) > thr)[0]
        onset = int(nz[0]) if len(nz) else -1
        print(f"    {pv:.1f}  {d_par:7d}  {alpha:7.4f}   {rel:8.5f}   "
              f"{onset:6d} / {onset/SR*1000 if onset >= 0 else float('nan'):7.2f}")

    # 关键档位：残差是否为 w 的延迟拷贝
    print("\n残差 s 与 w 的延迟拷贝拟合（搜索最佳延迟与增益）：")
    for pv in [0.2, 0.4, 0.6, 0.8]:
        y = ir(r, {**wet, "reverb_predelay": pv})[0][:D_MAX]
        alpha = float(np.dot(y, w) / max(np.dot(w, w), 1e-30))
        s = y - alpha * w
        d_par = int(round(V.predelay_ms(pv) / 1000 * SR))
        best = None
        for cand in range(max(0, d_par - 200), min(D_MAX - 1, d_par + 200) + 1):
            m = np.zeros(D_MAX)
            m[cand:] = w[:D_MAX - cand]
            g = float(np.dot(s, m) / max(np.dot(m, m), 1e-30))
            e = float(np.sqrt(np.mean((s - g * m) ** 2)))
            if best is None or e < best[0]:
                best = (e, cand, g)
        e, cand, g = best
        rel = e / max(float(np.sqrt(np.mean(s ** 2))), 1e-30)
        print(f"    pv={pv:.1f}  参数D={d_par:5d}  拟合D={cand:5d}  增益={g:7.4f}"
              f"  残差比={rel:.5f}")


if __name__ == "__main__":
    main()
