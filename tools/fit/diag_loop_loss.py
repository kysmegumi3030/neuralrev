"""环内高频损耗的**解析**分解：damping 一阶低通 vs Lagrange 插值。

这是纯解析工具（不渲染、不编译，秒级），用来回答三个在拟合之前必须先定的问题：

1. **参考侧的损耗形状是几阶的？**
   若一阶残差最小、提阶反而变差 ⇒ 不能靠串联多级低通去凑形状。
2. **候选的损耗里有多少不是 damping 造成的？**
   把实测的锚定超额损耗减去 damping 的解析预测，残差若只在顶端八度爆发，
   那是 sinc 型插值的指纹（一阶低通在 dB 域对 log f 平滑单调，不可能局部）。
3. **插值阶数提到多少够？**
   给出各阶在顶端八度的每圈损耗，以及减去实测需消除量后的残余。

## 为什么需要它

2026-08-05 那轮的教训：`fit_damping_shape.py` 的实测扫描出现**无解格局**
（fc=17000 时 4k/8k 归零但 18 kHz 到 −18.78；最优折中处 13.5k 与 18k
**符号相反**）。单参数单调族无法让两个同向缺口同时归零 —— 这是
**模型缺项**的信号（教训 5），不是网格不够密。用本工具可以在花几十分钟
跑真渲染扫描**之前**就判断出「fc 无解、要改阶数」。

判据：若某阶把顶端残余压进 ±2 dB/s，则配合重标 fc 可让全带 |D| 落进该量级。
数值口径见 docs/REFERENCE.md §12.9。

用法：
    python3 tools/fit/diag_loop_loss.py
    python3 tools/fit/diag_loop_loss.py --fc 20000     # 指定 damping fc
"""
from __future__ import annotations

import argparse

import numpy as np

SR = 48000.0

# 每秒圈数 = SR / 平均线长。平均线长 2802 样点（kArchLinesA/B 共 16 条的均值）。
# 绝对 dB/s 与「每圈 dB」之间的换算全靠这个常数，改线长表时必须同步。
ROUNDS_PER_SEC = 17.13

ANCHOR_F = 1000.0

# 各带几何中心频率。与 fit_damping_shape.BANDS 的边界一致。
FCB = {
    "2 kHz":    float(np.sqrt(1420 * 2840)),
    "4 kHz":    float(np.sqrt(2840 * 5680)),
    "8 kHz":    float(np.sqrt(5680 * 11360)),
    "13.5 kHz": float(np.sqrt(11360 * 16000)),
    "18 kHz":   float(np.sqrt(16000 * 20000)),
}

# 参考侧锚定超额衰减 E [dB/s]，decay=0.50、双尾长守卫、时间连通掩码。
# 来源：tools/fit/fit_damping_shape.py 的实测输出（REFERENCE §12.9.3）。
E_REF = {
    "2 kHz": 0.49, "4 kHz": 3.31, "8 kHz": 8.98,
    "13.5 kHz": 16.73, "18 kHz": 19.47,
}

# 候选侧「非 damping」残差 [dB/圈]，9 阶插值、7 个 fc 上的均值。
# 来源：REFERENCE §12.9.4。这是要靠提阶消掉的量。
NEED_PER_ROUND = {
    "4 kHz": 0.035, "8 kHz": -0.024, "13.5 kHz": 0.082, "18 kHz": 0.571,
}

TS = np.linspace(0.0, 1.0, 101)


def onepole_loss_db(f, fc):
    """离散一阶低通每次通过的损耗 [dB]。与 ReverbCore.hpp 的 OnePoleLP 逐字同式：
    x = exp(−2π fc/SR)，H(z) = (1−x)/(1 − x z⁻¹)。
    用离散式而不是模拟 1/(1+jf/fc)：fc 已接近 Nyquist，两者差异不可忽略。"""
    x = np.exp(-2.0 * np.pi * max(1.0, float(fc)) / SR)
    a, b = 1.0 - x, x
    w = 2.0 * np.pi * np.asarray(f, dtype=float) / SR
    return -10.0 * np.log10(a * a / (1.0 + b * b - 2.0 * b * np.cos(w)))


def damp_rel_dbs(f, fc):
    """damping 单独造成的**锚定**超额损耗 [dB/s]（相对 1 kHz）。"""
    return (onepole_loss_db(f, fc) - onepole_loss_db(ANCHOR_F, fc)) * ROUNDS_PER_SEC


def lagrange_rel_db(order, f):
    """Lagrange 插值在 t 均布下的平均每圈损耗 [dB]（相对 1 kHz）。

    节点偏移取 −kHalf … +(order−kHalf)，与 ModulatedDelay::process 一致。
    对 t 取平均而非最坏值：延迟位置由 LFO 连续扫过，t 近似均布。
    """
    n0 = -(order // 2)
    nodes = np.arange(n0, n0 + order + 1, dtype=float)
    f = np.atleast_1d(np.asarray(f, dtype=float))
    acc = np.zeros(len(f))
    acc_a = 0.0
    for t in TS:
        c = np.ones(len(nodes))
        for i in range(len(nodes)):
            for j in range(len(nodes)):
                if i != j:
                    c[i] *= (t - nodes[j]) / (nodes[i] - nodes[j])
        w = 2.0 * np.pi * f / SR
        H = np.sum(c[:, None] * np.exp(-1j * w[None, :] * nodes[:, None]), axis=0)
        acc += -20.0 * np.log10(np.abs(H) + 1e-300)
        wa = 2.0 * np.pi * ANCHOR_F / SR
        Ha = np.sum(c * np.exp(-1j * wa * nodes))
        acc_a += -20.0 * np.log10(abs(Ha) + 1e-300)
    return acc / len(TS) - acc_a / len(TS)


def peak_gain(order):
    """t 扫遍 0…1、频率扫遍 0…Nyquist 时的峰值 |H|。>1 则环内可能自激。"""
    f = np.linspace(1.0, SR / 2, 1500)
    n0 = -(order // 2)
    nodes = np.arange(n0, n0 + order + 1, dtype=float)
    pk = 0.0
    for t in TS:
        c = np.ones(len(nodes))
        for i in range(len(nodes)):
            for j in range(len(nodes)):
                if i != j:
                    c[i] *= (t - nodes[j]) / (nodes[i] - nodes[j])
        w = 2.0 * np.pi * f / SR
        H = np.sum(c[:, None] * np.exp(-1j * w[None, :] * nodes[:, None]), axis=0)
        pk = max(pk, float(np.max(np.abs(H))))
    return pk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fc", type=float, default=20000.0,
                    help="damping 截止频率（默认 = 当前落点 20000）")
    ap.add_argument("--order", type=int, default=15,
                    help="当前插值阶数（默认 = 当前落点 15）")
    a = ap.parse_args()

    names = list(FCB)
    freqs = np.array([FCB[n] for n in names])
    target = np.array([E_REF[n] for n in names])

    print("=== 1. 参考侧损耗形状需要几阶低通 ===")
    print("（把参考侧 E 拟合成 N 级串联一阶低通，看残差随 N 怎么走）")
    print(f"{'N':>3}{'最优fc':>10}{'max残差[dB/圈]':>16}")
    per_round = target / ROUNDS_PER_SEC
    for n in range(1, 5):
        best = None
        for fc in np.arange(1000.0, 40000.0, 25.0):
            pred = n * (onepole_loss_db(freqs, fc) - onepole_loss_db(ANCHOR_F, fc))
            w = float(np.max(np.abs(pred - per_round)))
            if best is None or w < best[0]:
                best = (w, fc)
        print(f"{n:>3}{best[1]:>10.0f}{best[0]:>16.4f}")
    print("→ N=1 最小、提阶变差 ⇒ 参考侧形状本身是一阶，不能靠串联多级去凑。")

    print(f"\n=== 2. 候选损耗分解（fc={a.fc:.0f}，{a.order} 阶插值）===")
    print(f"{'带':<10}{'参考E':>9}{'damping解析':>12}{'插值解析':>10}{'合计':>9}{'  差(=D)':>9}")
    lag = lagrange_rel_db(a.order, freqs) * ROUNDS_PER_SEC
    dmp = damp_rel_dbs(freqs, a.fc)
    for i, nm in enumerate(names):
        tot = dmp[i] + lag[i]
        print(f"{nm:<10}{target[i]:+9.2f}{dmp[i]:+12.2f}{lag[i]:+10.2f}"
              f"{tot:+9.2f}{target[i] - tot:+9.2f}")
    print("→ 「差」是解析预测的剩余缺口，正 = 候选损耗不足（高频偏亮的反面）。")
    print("⚠️ 顶端的「插值解析」是**高估**：本工具按 t 均布取平均，而三角 LFO 在整数")
    print("   延迟位置附近停留更久（那里 t≈0、插值损耗为 0）。实测 18 kHz 的插值损耗")
    print("   约为解析值的一半（10.81 vs 19.4 dB/s @9 阶）。所以本工具只用来定")
    print("   **阶数与方向**，最终 fc 必须用 fit_damping_shape.py 真渲染扫（§12.9.5）。")

    print(f"\n=== 3. 插值阶数的边际收益 ===")
    print(f"{'阶':>4}" + "".join(f"{n:>11}" for n in names)
          + f"{'  峰值|H|':>10}{'  18k残余[dB/s]':>16}")
    for order in (3, 5, 7, 9, 11, 13, 15, 19, 23):
        rel = lagrange_rel_db(order, freqs)
        cur9 = lagrange_rel_db(9, freqs)
        gain18 = cur9[names.index("18 kHz")] - rel[names.index("18 kHz")]
        resid = (NEED_PER_ROUND["18 kHz"] - gain18) * ROUNDS_PER_SEC
        mark = "  ← 当前" if order == a.order else ""
        print(f"{order:>4}" + "".join(f"{v:>11.3f}" for v in rel)
              + f"{peak_gain(order):>10.6f}{resid:>16.2f}{mark}")
    print("→ 残余 = 实测需消除量（9 阶基准）− 提阶消掉的量。首次落进 ±2 dB/s 的阶即够；"
          "\n  再往上会过冲成反向误差，而系数计算是 O(N²)。峰值|H| 恒为 1 ⇒ 加阶不会自激。")

    print(f"\n=== 4. 若插值损耗按比例缩小，最优 fc 能做到多好 ===")
    other = np.array([NEED_PER_ROUND.get(n, 0.0) for n in names]) * ROUNDS_PER_SEC
    print(f"{'k':>6}{'最优fc':>9}{'max|D|':>9}   逐带 D [dB/s]")
    for k in (1.0, 0.5, 0.3, 0.0):
        best = None
        for fc in np.arange(8000.0, 40000.0, 25.0):
            err = (damp_rel_dbs(freqs, fc) + other * k) - target
            w = float(np.max(np.abs(err)))
            if best is None or w < best[0]:
                best = (w, fc, err)
        w, fc, err = best
        print(f"{k:>6.2f}{fc:>9.0f}{w:>9.2f}   "
              + "  ".join(f"{n.split()[0]}:{e:+6.2f}" for n, e in zip(names, err)))
    print("→ k=1 是 9 阶原状、k=0 是插值损耗完全消除。两者的差就是提阶的天花板。")


if __name__ == "__main__":
    main()
