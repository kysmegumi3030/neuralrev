"""PRE-DELAY 的拓扑判定：它到底作用在信号链的哪一段。

实测线索（见 docs/REFERENCE.md §3）：
  * 湿声起点恒为 477 样点（9.94 ms），与 PRE-DELAY 无关；
  * pv=1.0 的 IR 在 0–200 ms 区间**恰好等于** pv=0.0 的 0.500 倍
    （逐 1 ms 检查，比值 0.500±0.001，波形形状完全一致）；
  * 能量重心随 pv 单调后移（181 ms → 265 ms）。

由此提出「双路并联」模型：

    wet(t) = g · [ w(t) + w(t − D) ]

其中 w 是「单路湿声」（自身起点 477 样点），D = PRE-DELAY 的样点数，
g 是归一化系数。pv=0 时 D=48（1 ms），两路几乎重合 → 早期约 2 倍；
pv=1 时 D=9600（200 ms），前 200 ms 只有一路 → 恰好 0.5 倍。这正是实测。

本脚本定量检验该模型，并给出残差。

用法：python3 tools/measure/ref_predelay_model.py
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


def ir(r, params, tail_sec=5.0):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[:, IMPULSE_AT + LATENCY:]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    wet = {"reverb_drywet": 1.0}

    a = ir(r, {**wet, "reverb_predelay": 0.0})[0]   # D = 1 ms   = 48 样点
    b = ir(r, {**wet, "reverb_predelay": 1.0})[0]   # D = 200 ms = 9600 样点
    d0 = int(round(V.predelay_ms(0.0) / 1000 * SR))
    d1 = int(round(V.predelay_ms(1.0) / 1000 * SR))
    print(f"D(pv=0) = {d0} 样点，D(pv=1) = {d1} 样点")

    # ---- 1. pv=1 的前 200 ms 是单路 → w = 2·b ----
    w = 2.0 * b

    # ---- 2. 用 w 重建 pv=0：a ?= 0.5·[w(t) + w(t−48)] = b(t) + b(t−48) ----
    pred = b.copy()
    pred[d0:] += b[:-d0]
    n = d1 - d0                    # 只在 b 仍是单路的区间内比较
    err = float(np.abs(a[:n] - pred[:n]).max())
    ref = float(np.abs(a[:n]).max())
    print(f"\n[模型 1] a ?= b + b(−{d0})  在 [0, {n}) 上："
          f"max|err| = {err:.3e}，max|a| = {ref:.3e}，相对 = {err/ref:.5f}")

    # ---- 3. 用 w 重建 pv=1 的后段（应出现第二份拷贝）----
    recon = 0.5 * w.copy()
    recon[d1:] += 0.5 * w[:-d1]
    print(f"\n[模型 2] b ?= 0.5·w + 0.5·w(−{d1})，分段相对残差：")
    for lo, hi in [(d1, d1 + 2400), (d1 + 2400, d1 + 10000), (d1 + 10000, d1 + 30000)]:
        if hi > len(b):
            break
        e = float(np.abs(b[lo:hi] - recon[lo:hi]).max())
        m = float(np.abs(b[lo:hi]).max())
        print(f"    [{lo:6d}:{hi:6d}]  相对残差 = {e/max(m,1e-30):.5f}"
              f"  (err {e:.3e} / peak {m:.3e})")

    # ---- 4. 中间档位：逐档拟合 D，看是否等于参数值 ----
    print("\n[模型 3] 各档位反解 D（用 pv=1 的单路 w 作基，最小化残差）：")
    print("    pv   参数 D(样点)   拟合 D(样点)   相对残差")
    for pv in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        y = ir(r, {**wet, "reverb_predelay": pv})[0]
        dpar = int(round(V.predelay_ms(pv) / 1000 * SR))
        best, bestd = None, None
        for cand in range(max(0, dpar - 400), dpar + 401, 8):
            m = 0.5 * w.copy()
            if cand > 0:
                m[cand:] += 0.5 * w[:-cand]
            else:
                m += 0.5 * w
            k = min(len(y), len(m), int(1.2 * SR))
            e = float(np.sqrt(np.mean((y[:k] - m[:k]) ** 2)))
            if best is None or e < best:
                best, bestd = e, cand
        rel = best / float(np.sqrt(np.mean(y[:int(1.2 * SR)] ** 2)))
        print(f"    {pv:.1f}  {dpar:8d}   {bestd:11d}   {rel:.5f}")


if __name__ == "__main__":
    main()
