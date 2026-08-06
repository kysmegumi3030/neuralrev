"""深度与初相是**同一个现象**：LFO 被读指针采样 ⇒ 相位随延迟走。

## 线索

`ref_delay_lfo_demod.py` 的连续解调给出一张关键表（幅度与冲激列独立吻合到
三位数，所以两个仪器都没错）：

    norm   0.00   0.10   0.20   0.30   0.40   0.50   0.60 | 0.70   0.80   0.90   1.00
    幅度   3.32   3.95   5.11   6.22   6.50   5.24   2.11 | 2.19   5.78   6.25   2.57
    初相 -132.7 -126.3 -112.4  -91.6  -66.7  -36.2   -2.2 |-144.2 -101.4  -55.8   -6.7

初相在 0.0…0.6 上**单调上升** −132.7° → −2.2°，然后在 0.7 处**跳回** −144.2°
再重新单调上升到 −6.7°。这是相位缠绕（wrap）的形状，不是随机。

更要紧的是：两个「深度塌陷」的档（0.60 幅度 2.11、1.00 幅度 2.57）**正好**是
初相最接近 0° 的两档（−2.2°、−6.7°）。深度低与相位过零同时发生 —— 两个反常
是同一件事。

## 机制假设

若 LFO 是一个**全局、固定相位**的振荡器（不随每条延迟线重启），而延迟线的读
指针在写入后 D 个样点才取值，那么被读出的调制量是 LFO 在 **t − D/SR** 时刻的值。
两条推论：

1. **相位**随 D 线性变化，斜率 −2πf·D/SR ⇒ 与实测的单调上升+缠绕一致；
2. 若延迟线上**同时存在两个抽头**（例如读指针与写指针各自被调制，或调制
   同时作用于时长与某个内插相位），两路同频不同相的正弦叠加后，**合幅度**
   随 D 呈 |cos| 形状 —— 这正好给出「非单调、有塌陷点」的深度。

上一个脚本试过「相位 = 2πfD/SR」并被否掉（残差 std 47°），但那个检验有个缺陷：
它固定了系数为 1 且没有考虑缠绕。本脚本改为**最小二乘拟合斜率与截距**，
在缠绕意义下（对 sin/cos 拟合，不对角度本身拟合）求解，并同时检验推论 2。

## 检验设计

* **相位律**：拟合 phase(D) = φ0 − 360·k·D/SR（k 为等效频率），看 k 是否 ≈ 1.70186
  Hz。若是，说明相位参考点在**固定时间原点**，读出时刻延后 D ⇒ 机制确认。
* **深度律**：在确认相位律后，用 φ(D) 预测两路叠加的合幅度
  |A1 + A2·e^{iθ(D)}|，拟合 A1/A2 与相对相位，看能否同时对上 11 个深度点。
* **加密**：深度塌陷点附近（norm 0.55…0.65）加密到 0.01 步长，看塌陷是否是
  一个真正的零点（幅度过零后相位跳 180°），那是两路叠加最硬的证据。

用法：
    python3 tools/measure/ref_delay_lfo_phase.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_lfo_demod import measure  # noqa: E402

SR = 48000

# 粗扫（复用上一轮的档）+ 塌陷点附近加密
COARSE = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
FINE = (0.55, 0.57, 0.59, 0.61, 0.63, 0.65)


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")

    rows = []
    for nv in sorted(set(COARSE) | set(FINE)):
        rows.append(measure(r, nv))

    hdr("加密后的深度与相位（塌陷点附近 0.02 步长）")
    print(f"  {'norm':>6} {'ms':>8} {'D':>7} {'幅度':>8} {'初相°':>9} {'速率 Hz':>9} {'残差':>8}")
    for q in rows:
        print(f"  {q['nv']:6.2f} {V.delay_time_ms(q['nv']):8.1f} {q['d0']:7d} "
              f"{q['amp']:8.4f} {q['phase']:+9.2f} {q['f0']:9.5f} {q['res'] * 100:7.3f}%")

    d0 = np.array([q["d0"] for q in rows], dtype=float)
    amp = np.array([q["amp"] for q in rows])
    ph = np.radians(np.array([q["phase"] for q in rows]))
    f0 = float(np.mean([q["f0"] for q in rows]))

    # ---------------------------------------------------------------- 相位律
    hdr("相位律：phase(D) = φ0 − 2π·k·D/SR，拟合等效频率 k")
    # 在缠绕意义下拟合：对 k 扫描，取「相位残差的圆均值长度」最大者
    ks = np.arange(0.5, 6.0, 0.00002)
    best_k, best_r, best_p0 = 0.0, -1.0, 0.0
    for k in ks:
        z = np.exp(1j * (ph + 2 * np.pi * k * d0 / SR))
        m = np.abs(z.mean())
        if m > best_r:
            best_r, best_k, best_p0 = float(m), float(k), float(np.angle(z.mean()))
    print(f"  最优 k = {best_k:.5f} Hz   圆均值长度 R = {best_r:.6f}"
          f"   （R→1 表示完全对上）")
    print(f"  LFO 实测速率 = {f0:.5f} Hz   比值 k/f = {best_k / f0:.6f}")
    pred = (best_p0 - 2 * np.pi * best_k * d0 / SR)
    resid = np.degrees((ph - pred + np.pi) % (2 * np.pi) - np.pi)
    print(f"  相位残差: std = {np.std(resid):.2f}°   max|·| = {np.max(np.abs(resid)):.2f}°")
    for q, rr in zip(rows, resid):
        print(f"    norm={q['nv']:.2f}  D={q['d0']:6d}  残差 {rr:+7.2f}°")

    # ------------------------------------------------------- 深度律（两路叠加）
    hdr("深度律：两路同频不同相叠加 |A1 + A2·e^{iθ}|，θ = 2π·k2·D/SR")
    best = None
    for k2 in np.arange(0.5, 6.0, 0.001):
        th = 2 * np.pi * k2 * d0 / SR
        # 对 (A1, A2r, A2i) 线性最小二乘拟合复合幅度的平方更稳：
        # |A1 + A2 e^{iθ}|² = A1² + A2² + 2·A1·A2·cos(θ+ψ)
        # ⇒ amp² = c0 + c1·cos θ + c2·sin θ
        A = np.column_stack([np.ones_like(th), np.cos(th), np.sin(th)])
        c, *_ = np.linalg.lstsq(A, amp ** 2, rcond=None)
        fit = A @ c
        if np.any(fit < 0):
            continue
        rel = np.abs(np.sqrt(fit) - amp) / (amp + 1e-30)
        if best is None or rel.max() < best[1]:
            best = (k2, float(rel.max()), c)
    if best:
        k2, worst, c = best
        th = 2 * np.pi * k2 * d0 / SR
        A = np.column_stack([np.ones_like(th), np.cos(th), np.sin(th)])
        fit = np.sqrt(np.maximum(A @ c, 0.0))
        print(f"  最优 k2 = {k2:.4f} Hz   最差相对偏差 = {worst * 100:.2f}%"
              f"   {'✓' if worst < 0.05 else ''}")
        print(f"  {'norm':>6} {'D':>7} {'实测幅度':>10} {'模型':>10} {'相对偏差':>10}")
        for q, a0, f1 in zip(rows, amp, fit):
            print(f"  {q['nv']:6.2f} {q['d0']:7d} {a0:10.4f} {f1:10.4f} "
                  f"{abs(f1 - a0) / a0 * 100:9.2f}%")

    hdr("判读")
    print("  相位律 R→1 且 k ≈ LFO 速率 ⇒ **LFO 是全局固定相位，被读指针延后 D 采样**。")
    print("  这条一旦成立，实现上就不需要拟合深度的闭式解：深度是这个机制的**推论**。")


if __name__ == "__main__":
    main()
