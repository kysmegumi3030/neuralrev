"""反馈系数被**显示量化**捕获：环内用的是显示串上那个两位小数。

## 线索

`ref_delay_fb2.py` 的路径 B 把环内损耗 L(f) 干净地消掉了（12 个频点上比值
散布只有 5e-6），但消掉之后剩下的比值**不等于** norm 之比：

    norm      实测比值      norm 之比
    0.25      0.259999      0.250
    0.50      0.499997      0.500
    0.75      0.760000      0.750

偏差 +4.0% / −0.001% / +1.3% —— 不是单调的，所以不可能是任何仿射律
（仿射律给出的偏差必须单调）。但 0.26 / 0.50 / 0.76 这三个数一眼可认：

    round(0.5 × 0.25, 2) / 0.5 = 0.13 / 0.5 = 0.26   ✓
    round(0.5 × 0.50, 2) / 0.5 = 0.25 / 0.5 = 0.50   ✓
    round(0.5 × 0.75, 2) / 0.5 = 0.38 / 0.5 = 0.76   ✓

即**环内系数正比于「显示串上四舍五入到两位小数的那个值」**，而不是连续的
归一值。归一域的量化步长 = 0.01/0.5 = **1/50 = 0.02**。

这与混响段的 DRY/WET 是同一个现象（§2 已记 kMeasDryWetQuant = 0.01）——
这家插件把用户可见的两位小数直接当作 DSP 系数。

## 判据（阶梯的三条硬特征）

量化的预言是**阶梯**，与任何连续律都不同：

1. **平台**：同一格内的 norm 给出**完全相同**的系数。取 0.295 与 0.305
   （50·n = 14.75 与 15.25，同属第 15 格），比值应当是 1.000000 而不是
   0.305/0.295 = 1.0339。这一条最硬 —— 连续律无法产生恒定平台。
2. **跳变**：跨格时比值跳整整 1/15（第 15 格 → 第 16 格）。
   取 0.309 与 0.311（50·n = 15.45 / 15.55）⇒ 应当跳 16/15 = 1.0667。
3. **格数**：全程 norm 0…1 共 51 格（0…50）。密扫 0.30…0.34 步长 0.005
   应当看到恰好两次跳变，位置在 50·n = 15.5 与 16.5。

三条都用**同一频点**（350 Hz，环内损耗极小处）与**同一延迟**读，
所以 L(f) 与 D 完全相消，剩下的只有量化。

## 顺带定 fb 上限的真值

在损耗极小的平台（100–350 Hz，L 平到 5e-5）读出 r(1.0) = 0.79640。
若真值是 0.80，则平台处残余损耗 −0.031 dB —— 与 1 kHz 处已达 −0.137 dB
的那条平滑曲线完全自洽。本脚本把 100/200/350 Hz 三点的读数一起报出来，
看它们是否都指向同一个上限。

用法：
    python3 tools/measure/ref_delay_fb3.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_fb import NT, SR  # noqa: E402
from measure.ref_delay_fb2 import ratios  # noqa: E402

FREQ = 350.0          # 环内损耗极小处（实测 L = 1.00000）
PLATEAU = (100.0, 200.0, 350.0)
GRID = 50             # 归一域的量化格数（= 0.5 显示上限 / 0.01 显示步长）


def grid_of(norm: float) -> int:
    """round-half-up(GRID·norm)，但**避开二进制表示误差**。

    为什么需要这个而不是 floor(50·nv + 0.5)：norm=0.290 在 float 里是
    0.28999999999999998，50·norm = 14.499999999999998，+0.5 后 floor 给出 **14**，
    而实测该点与 0.295…0.309（第 15 格）读数完全相同 ⇒ 它属于第 15 格。
    边界点被错标一个格，会让「coeff ∝ 格号」的过原点拟合从 0.0001% 恶化到 6.3%
    —— 那不是模型错，是标签错。先四舍五入到 3 位小数再乘，边界就落在整数上。
    """
    return int(np.floor(round(float(norm), 3) * GRID + 0.5 + 1e-9))


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    n = 10 * SR
    D = int(round(V.delay_time_ms(NT) * SR / 1000.0))

    hdr(f"判据 1+2+3：norm 0.29…0.35 密扫 @{FREQ:.0f} Hz（阶梯 vs 连续）")
    print("  量化预言：格 = round(50·norm)，同格内读数**完全相同**，")
    print("  跨格时按 格号 之比跳变。连续律预言读数正比于 norm。")
    print(f"  {'norm':>7} {'50·norm':>9} {'预测格':>7} {'实测比值':>11} "
          f"{'/首个':>10} {'格之比':>9} {'norm 之比':>10}")

    scan = [0.290, 0.295, 0.300, 0.305, 0.309, 0.311, 0.315, 0.320,
            0.325, 0.330, 0.335, 0.340, 0.345, 0.350]
    vals = []
    for nv in scan:
        vals.append(ratios(r, FREQ, nv, D, n))

    v0, n0 = vals[0], scan[0]
    g0 = grid_of(n0)
    for nv, v in zip(scan, vals):
        g = grid_of(nv)
        print(f"  {nv:7.3f} {50.0 * nv:9.3f} {g:7d} {v:11.6f} "
              f"{v / v0:10.6f} {g / g0:9.6f} {nv / n0:10.6f}")

    # 同格平台：把 scan 按预测格分组，看组内极差
    hdr("判据 1：同格内是否是平台（组内极差应当 ≈ 0）")
    groups: dict[int, list] = {}
    for nv, v in zip(scan, vals):
        groups.setdefault(grid_of(nv), []).append((nv, v))
    print(f"  {'格':>4} {'成员 norm':>28} {'组内极差':>12} {'相对':>10}")
    worst = 0.0
    for g in sorted(groups):
        mem = groups[g]
        vs = np.array([m[1] for m in mem])
        rng = float(vs.max() - vs.min())
        rel = rng / float(vs.mean())
        worst = max(worst, rel)
        names = " ".join(f"{m[0]:.3f}" for m in mem)
        print(f"  {g:4d} {names:>28} {rng:12.3e} {rel * 100:9.4f}%")
    print(f"\n  组内最差相对极差 = {worst * 100:.4f}%"
          f"   {'✓ 是平台 ⇒ 量化成立' if worst < 0.002 else '✗ 不是平台'}")

    # 与两个模型比
    hdr("判据 2/3：量化模型 vs 连续模型，谁对得上")
    q = np.array([grid_of(nv) for nv in scan], dtype=float)
    a = np.array(vals)
    # 各自过原点拟合
    sq = float(np.dot(q, a) / np.dot(q, q))
    sc = float(np.dot(np.array(scan), a) / np.dot(np.array(scan), np.array(scan)))
    rq = np.abs(sq * q - a) / a
    rc = np.abs(sc * np.array(scan) - a) / a
    print(f"  量化模型 coeff ∝ round(50·norm):  最差相对偏差 = {rq.max() * 100:.4f}%")
    print(f"  连续模型 coeff ∝ norm:            最差相对偏差 = {rc.max() * 100:.4f}%")
    print(f"  ⇒ {'量化模型胜出 %.0f 倍' % (rc.max() / max(rq.max(), 1e-12))}")

    hdr("fb 上限的真值：在损耗极小平台上读 norm=1.0")
    print(f"  {'频率':>7} {'r(fb=1)':>10} {'/0.80':>9} {'损耗 dB':>9}")
    for f in PLATEAU:
        v = ratios(r, f, 1.0, D, n)
        print(f"  {f:7.0f} {v:10.5f} {v / 0.80:9.5f} {20 * np.log10(v / 0.80):9.4f}")
    print("\n  若三点都给出 ≈0.796 且 /0.80 一致，则上限真值 0.80、")
    print("  平台残余损耗 ≈ −0.03 dB —— 与 1 kHz 的 −0.137 dB 同一条平滑曲线自洽。")


if __name__ == "__main__":
    main()
