"""时不变性（shift-invariance）检验 —— 决定 1e-3 逐样点目标是否可达。

为什么必须做这一步：出现了一对看似矛盾的结果。
  * 叠加性成立：IR(a+b) vs IR(a)+IR(b)，相对误差 1.7e-4
  * 齐次性成立：0.5×激励 ×2 vs 1.0×，相对误差 1.3e-4，幅度扫描 1e-3…1.0 恒定
  * **但**用实测 IR 做卷积复现不了参考输出（双冲激用例 nrmse 62%）

线性（叠加+齐次）成立而卷积失败，唯一的解释是**时变**：
系统对「t=T₀ 处的冲激」与「t=T₀+D 处的冲激」响应不同，
即 h(t, τ) 不能写成 h(t−τ)。典型成因是混响内部有 LFO 调制的延迟线
（plate/spring 混响常见，用来打散金属味）。

本脚本直接测：把同一个冲激放在不同时刻，比较各自的响应。
若响应随位置变化 → 时变确证 → **逐样点 1e-3 在原理上不可达**
（除非复现出 LFO 的精确波形、频率与初相，黑箱测量无法唯一确定初相）。

用法：python3 tools/measure/ref_shift_invariance.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
LATENCY = 51
PARAMS = {"reverb_drywet": 1.0, "reverb_predelay": 0.5, "reverb_decay": 0.5}


def ir_at(r, at_samples, tail_sec=4.0):
    """把冲激放在 at_samples 处，返回以冲激为 t=0 对齐的响应。"""
    n = at_samples + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[at_samples] = 1.0
    y = r.render(x, params=PARAMS).astype(np.float64)[0]
    return y[at_samples + LATENCY:]


def compare(a, b, name):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    g = float(np.dot(a, b) / max(np.dot(b, b), 1e-30))
    d = a - g * b
    rel = float(np.sqrt(np.mean(d ** 2)) / max(np.sqrt(np.mean(a ** 2)), 1e-30))
    print(f"    {name:34s} max|Δ|={np.abs(d).max():.3e}"
          f"  nrmse={rel*100:7.3f}%  gain={g:.5f}")
    return rel


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    base_at = int(2.0 * SR)
    base = ir_at(r, base_at)
    print(f"基准：冲激 @ {base_at} 样点（2.000 s），IR 峰值 {np.abs(base).max():.6e}\n")

    print("同一冲激置于不同时刻，与基准响应对比：")
    print("  （若时不变，各行 nrmse 应 ≈ 0）")
    rels = []
    for delta_ms in [1, 5, 10, 50, 100, 500, 1000]:
        at = base_at + int(delta_ms / 1000 * SR)
        rels.append((delta_ms, compare(base, ir_at(r, at), f"Δ = +{delta_ms} ms")))

    worst = max(rr for _, rr in rels)
    print(f"\n最大 nrmse = {worst*100:.3f}%")
    if worst < 1e-3:
        print("→ 时不变成立，混响是纯 LTI；卷积失败必有别的原因（需复查对齐）。")
    else:
        print("→ **时变确证**：响应随冲激位置变化，h(t,τ) ≠ h(t−τ)。")
        print("  成因通常是内部 LFO 调制的延迟线。")
        print("  推论：逐样点 1e-3 需要复现 LFO 的波形/频率/**初相**，")
        print("  而初相在黑箱下不可唯一确定 → 该口径原理上不可达。")

    # 进一步：调制的周期与深度
    print("\n估计调制周期：以 1 ms 步长扫 0–120 ms，看 nrmse 是否呈周期性")
    print("  （若在某个 Δ 处 nrmse 回落，该 Δ 即调制周期的整数倍）")
    curve = []
    for delta_ms in range(0, 121, 10):
        at = base_at + int(delta_ms / 1000 * SR)
        y = ir_at(r, at, tail_sec=2.0)
        n = min(len(base), len(y))
        a, b = base[:n], y[:n]
        g = float(np.dot(a, b) / max(np.dot(b, b), 1e-30))
        rel = float(np.sqrt(np.mean((a - g * b) ** 2)) / max(np.sqrt(np.mean(a ** 2)), 1e-30))
        curve.append((delta_ms, rel))
        print(f"    Δ = {delta_ms:4d} ms   nrmse = {rel*100:7.3f}%")


if __name__ == "__main__":
    main()
