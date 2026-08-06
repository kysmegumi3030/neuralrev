"""DRY/WET 混合律的精细测定。

粗测（ref_filters.py）显示一个**非常规**的形状：
    dw     0.00  0.10  0.25  0.50  0.75  0.90  1.00
    干系数  1.00  1.00  1.00  1.00  0.875 0.380 0.000
    湿能量  0     6e-4  2e-2  0.37  1.47  1.47  1.47
干系数在 dw≤0.5 恒为 1，湿能量在 dw≥0.75 饱和 —— 这不像单个交叉淡化器。

怀疑是参数**平滑/斜坡**造成的假象：本工具的渲染每次都是新进程，
参数在块首一次性设定，但插件内部可能对 dry/wet 做长时间平滑，
于是「冲激时刻的瞬时系数」并不等于稳态系数。

本脚本用两种独立测法交叉验证：
  A) 稳态法：长正弦（3 s），只取**最后 1 s** 统计 → 平滑必然已收敛。
     干路系数由「湿声几乎为零的高频点」估计不可靠，改用相位相干法：
     把输出投影到输入上得干成分，残差能量得湿成分。
  B) 冲激法（原法）：冲激放在 2.5 s 处，前面留足平滑收敛时间。

用法：python3 tools/measure/ref_drywet.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
LATENCY = 51

DWS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def steady_state(r):
    """A) 稳态法：白噪声 3 s，取最后 1 s；投影分离干/湿。"""
    rng = np.random.default_rng(7)
    n = int(3.0 * SR)
    x = (0.2 * rng.standard_normal(n)).astype(np.float32)
    print("A) 稳态法（白噪声 3 s，取末 1 s，投影分离）")
    print("    dw     干增益     湿 rms     干+湿 能量和")
    rows = []
    for dw in DWS:
        y = r.render(x, params={"reverb_drywet": dw}).astype(np.float64)[0]
        # 对齐固有延迟
        y = y[LATENCY:LATENCY + n]
        a = x.astype(np.float64)[:len(y)]
        s = slice(int(2.0 * SR), len(y))       # 末 1 s
        aa, yy = a[s], y[s]
        g = float(np.dot(aa, yy) / max(np.dot(aa, aa), 1e-30))
        resid = yy - g * aa
        wet_rms = float(np.sqrt(np.mean(resid ** 2)))
        dry_rms = float(np.sqrt(np.mean((g * aa) ** 2)))
        rows.append((dw, g, wet_rms))
        print(f"    {dw:.1f}   {g:8.5f}   {wet_rms:8.5f}   "
              f"{dry_rms**2 + wet_rms**2:.6f}")
    return rows


def impulse_late(r):
    """B) 冲激法，冲激放在 2.5 s（平滑已收敛）。"""
    at = int(2.5 * SR)
    n = at + int(3.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[at] = 1.0
    print("\nB) 冲激法（冲激置于 2.5 s，平滑已收敛）")
    print("    dw     干系数     湿能量")
    rows = []
    for dw in DWS:
        y = r.render(x, params={"reverb_drywet": dw}).astype(np.float64)[0]
        y = y[at + LATENCY:]
        dry_c = float(y[0])
        wet_e = float(np.sum(y[200:] ** 2))
        rows.append((dw, dry_c, wet_e))
        print(f"    {dw:.1f}   {dry_c:8.5f}   {wet_e:.6e}")
    return rows


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    a = steady_state(r)
    b = impulse_late(r)

    # ---- 分段模型检验 ----
    # 实测形状（两法一致）：
    #   干增益  dw ≤ 0.7 恒为 1；dw > 0.7 线性降到 0（0.8→0.72, 0.9→0.38, 1.0→0）
    #   湿增益  dw ≤ 0.7 约 ∝ dw²；dw > 0.7 饱和不变
    print("\n=== 干增益：分段模型 ===")
    print("    dw    实测(A)  实测(B)   模型 max(0, (1−dw)/0.3)·… 见下")
    bmap = {dw: c for dw, c, _ in b}
    for dw, g, _ in a:
        print(f"    {dw:.1f}  {g:8.5f}  {bmap[dw]:8.5f}")

    # 干增益在 dw>0.7 段的线性拟合
    seg = [(dw, c) for dw, c, _ in b if dw > 0.7]
    if len(seg) >= 2:
        xs = np.array([s[0] for s in seg])
        ys = np.array([s[1] for s in seg])
        k, c0 = np.polyfit(xs, ys, 1)
        print(f"\n  dw>0.7 段线性拟合：dry = {k:.4f}·dw + {c0:.4f}"
              f"  → 零点在 dw = {-c0/k:.4f}")
        print(f"  等价写法：dry = clamp((1 − dw)/{-1/k:.4f}, 0, 1)"
              f"，即在 dw = {1 + 1/k:.3f} 起下降")

    print("\n=== 湿增益（幅度，= √湿能量，相对 dw=1 归一）===")
    wmax = max(np.sqrt(w) for _, _, w in b) or 1.0
    print("    dw    实测(B)      dw²      dw")
    for dw, _, w in b:
        print(f"    {dw:.1f}  {np.sqrt(w)/wmax:8.5f}  {dw**2:8.4f}  {dw:6.3f}")


if __name__ == "__main__":
    main()
