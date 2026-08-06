"""PRE-DELAY = 并联双路（干净版检验）。

上一轮「模型 3」失败的原因是**基函数取错了**：它用 w = 2·IR(pv=1) 作为「单路湿声」，
但这个等式只在 t < D(pv=1) = 9600 样点内成立——超过 9600 样点后，IR(pv=1) 自己
也叠上了第二路，w 就不再是单路。用越界的 w 去重建，残差必然爆掉（实测 1.2）。

本轮只在**合法区间**内检验：

  单路基函数   w(t) = 2 · IR(pv=1)(t)      仅 t ∈ [0, 9600) 有效
  待验证模型   IR(pv)(t) = ½·[ w(t) + w(t − D(pv)) ]   仅 t ∈ [0, 9600) 内比较

支撑本模型的独立事实（ref_predelay_diag.py [A]）：
  IR(pv=0) 在 [0, 9552) 上 = **2.000004 ×** IR(pv=1)，逐样点残差 3.2e-4。
  即 pv=0 时两路完全重合（D≈0），pv=1 时前 200 ms 只有一路 → 恰好 2 倍。

用法：python3 tools/measure/ref_predelay_twopath.py
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
D_MAX = 9600  # = 200 ms，pv=1 的延迟；w 的有效区间上界


def ir(r, params, tail_sec=6.0):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[:, IMPULSE_AT + LATENCY:]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    wet = {"reverb_drywet": 1.0}

    # 单路基函数：只有前 9600 样点可信
    w = 2.0 * ir(r, {**wet, "reverb_predelay": 1.0})[0][:D_MAX]
    print(f"单路基函数 w：长度 {len(w)} 样点（{len(w)/SR*1000:.1f} ms），"
          f"峰值 {np.abs(w).max():.6f}")

    print("\n在合法区间 [0, 9600) 内检验 IR(pv) = ½·[w(t) + w(t−D)]：")
    print("    pv   参数D(样点)  拟合D(样点)  相对残差   最佳增益")
    rows = []
    for pv in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        y = ir(r, {**wet, "reverb_predelay": pv})[0][:D_MAX]
        d_par = int(round(V.predelay_ms(pv) / 1000 * SR))

        best = None
        # 在参数值附近 ±120 样点逐样点搜（模型若对，最优点应落在参数值上）
        lo = max(0, d_par - 120)
        hi = min(D_MAX - 1, d_par + 120)
        for cand in range(lo, hi + 1):
            m = np.zeros(D_MAX)
            m[:] = 0.5 * w
            if cand > 0:
                m[cand:] += 0.5 * w[:D_MAX - cand]
            else:
                m += 0.5 * w
            g = float(np.dot(y, m) / max(np.dot(m, m), 1e-30))
            e = float(np.sqrt(np.mean((y - g * m) ** 2)))
            if best is None or e < best[0]:
                best = (e, cand, g)
        e, cand, g = best
        rel = e / float(np.sqrt(np.mean(y ** 2)))
        rows.append((pv, d_par, cand, rel, g))
        print(f"    {pv:.1f}  {d_par:9d}  {cand:10d}   {rel:.6f}   {g:.5f}")

    # 拟合 D 与参数 D 的一致性
    dp = np.array([r_[1] for r_ in rows], float)
    df = np.array([r_[2] for r_ in rows], float)
    print(f"\n拟合 D 与参数 D：最大偏差 {np.max(np.abs(df - dp)):.0f} 样点"
          f"（{np.max(np.abs(df - dp))/SR*1000:.3f} ms）")
    worst = max(r_[3] for r_ in rows)
    print(f"全档最大相对残差 = {worst:.6f}"
          f" → {'模型成立' if worst < 0.02 else '模型仍不成立，需换假设'}")


if __name__ == "__main__":
    main()
