"""PRE-DELAY 拓扑的进一步诊断（模型 1/2 被否后的下一轮）。

已否证：「双路并联 wet = g·[w(t) + w(t−D)]」——残差 0.5–2.4，不成立。

仍待解释的硬事实：
  * pv=1.0 的 IR 在 0–200 ms 区间，逐 1 ms 的 absmax **恰好**是 pv=0.0 的 0.500 倍
    （21 个 1 ms 窗全部 0.500±0.001）；
  * 湿声起点恒 477 样点，与 pv 无关；
  * 能量重心随 pv 单调后移。

本轮检验的候选：
  A) 早期区是否**逐样点**成 0.5 倍（而非仅包络成比例）
     → 若是，说明 pv 只改了一个增益 + 后段结构，早期波形本身不变。
  B) PRE-DELAY 是否在**反馈环内**（递归延迟）
     → 特征：IR 呈现间隔为 D 的重复簇。用自相关找周期。
  C) 湿声总能量是否守恒（pv 只搬移能量，不改总量）
  D) 各 pv 档之间是否互为「时间拉伸」而非「平移」

用法：python3 tools/measure/ref_predelay_diag.py
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


def ir(r, params, tail_sec=6.0):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[:, IMPULSE_AT + LATENCY:]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    wet = {"reverb_drywet": 1.0}

    irs = {pv: ir(r, {**wet, "reverb_predelay": pv})[0]
           for pv in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]}
    a, b = irs[0.0], irs[1.0]

    # ---- A) 早期区是否逐样点 0.5 倍 ----
    n = 9552  # = D(pv=1) − D(pv=0)，b 在此区间内应「只有一路」
    scale = float(np.dot(a[:n], b[:n]) / np.dot(b[:n], b[:n]))
    resid = float(np.sqrt(np.mean((a[:n] - scale * b[:n]) ** 2)))
    rms_a = float(np.sqrt(np.mean(a[:n] ** 2)))
    print(f"[A] 早期区 [0,{n}) 最佳标量增益 a≈k·b：k = {scale:.6f}")
    print(f"    残差 rms = {resid:.3e}，a 的 rms = {rms_a:.3e}，相对 = {resid/rms_a:.6f}")
    print(f"    → {'逐样点成比例（同一波形，仅增益不同）' if resid/rms_a < 1e-3 else '不是纯增益关系'}")

    # ---- B) 反馈环内延迟？找 IR 的周期性 ----
    print("\n[B] IR 包络自相关的主周期（找是否有间隔 D 的重复簇）：")
    for pv in [0.0, 0.5, 1.0]:
        y = irs.get(pv)
        if y is None:
            y = ir(r, {**wet, "reverb_predelay": pv})[0]
        e = np.sqrt(np.convolve(y[:int(2.0 * SR)] ** 2, np.ones(256) / 256, "same"))
        e = e - e.mean()
        ac = np.correlate(e, e, "full")[len(e) - 1:]
        ac /= max(ac[0], 1e-30)
        # 找第一个显著局部峰（跳过 0 lag 附近）
        lo = 200
        pk = lo + int(np.argmax(ac[lo:int(0.5 * SR)]))
        print(f"    pv={pv:.1f} 参数D={V.predelay_ms(pv)/1000*SR:7.0f} 样点 | "
              f"自相关首峰 lag={pk} ({pk/SR*1000:.2f} ms)，高度 {ac[pk]:.3f}")

    # ---- C) 能量守恒？----
    print("\n[C] 湿声总能量随 pv 的变化：")
    for pv, y in sorted(irs.items()):
        e = float(np.sum(y ** 2))
        print(f"    pv={pv:.1f}  总能量 = {e:.6f}  (相对 pv=0: {e/np.sum(irs[0.0]**2):.4f})")

    # ---- D) 平移 vs 拉伸：各档与 pv=0 的最佳时间缩放 ----
    print("\n[D] 各档相对 pv=0 的包络：最佳整数平移 / 最佳时间缩放：")
    e0 = np.sqrt(np.convolve(a[:int(1.5 * SR)] ** 2, np.ones(512) / 512, "same"))
    for pv, y in sorted(irs.items()):
        e1 = np.sqrt(np.convolve(y[:int(1.5 * SR)] ** 2, np.ones(512) / 512, "same"))
        # 最佳平移
        c = np.correlate(e1 - e1.mean(), e0 - e0.mean(), "full")
        shift = int(np.argmax(c) - (len(e0) - 1))
        # 最佳时间缩放（在 0.8..2.0 之间搜）
        best, bestk = None, None
        idx = np.arange(len(e0))
        for k in np.arange(0.80, 2.005, 0.01):
            src = np.clip((idx / k).astype(int), 0, len(e1) - 1)
            w = e1[src]
            err = float(np.sqrt(np.mean((e0 - w * (np.dot(e0, w) / max(np.dot(w, w), 1e-30))) ** 2)))
            if best is None or err < best:
                best, bestk = err, k
        print(f"    pv={pv:.1f}  最佳平移 = {shift:6d} 样点 ({shift/SR*1000:7.2f} ms)"
              f" | 最佳时间缩放 = {bestk:.2f}×  残差 {best/np.sqrt(np.mean(e0**2)):.4f}")


if __name__ == "__main__":
    main()
