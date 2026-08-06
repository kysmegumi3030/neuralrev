"""可分离性的时域判决（比频谱比值法严格得多）。

频谱比值法的问题：不同 DECAY 档的 IR 有不同的梳状零点，逐 bin 相除会在零点
附近放大噪声（实测未平滑离散度 15 dB，平滑后 0.05 dB —— 两个数字都不能直接
当判据）。

时域判决：
  1. 在 DECAY=d₁ 上用最小二乘解出 LOW CUT 的 **FIR 等效**  h：
         IR(d₁, lo=1) ≈ h * IR(d₁, lo=0)
     （对短 FIR 做正规方程，h 长度取 64，足够表示一个 2 极点 IIR 的前段）
  2. 把**同一个 h** 用到 DECAY=d₂ 上：
         pred = h * IR(d₂, lo=0)
     与实测 IR(d₂, lo=1) 比较，报告相对残差。

若可分离，第 2 步的残差应与第 1 步的拟合残差同量级（都是数值误差级）。
若滤波在反馈环内，第 2 步必然崩掉。

这个判据直接对应用户的验收口径（波形 diff），所以它的结论可以直接用于
决定实现路线。

用法：python3 tools/measure/ref_separability_time.py
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
NTAP = 96          # FIR 等效长度
NFIT = int(1.0 * SR)   # 用于拟合/评估的 IR 长度


def ir(r, params, tail_sec=4.0):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[0][IMPULSE_AT + LATENCY:]


def solve_fir(x, y, ntap=NTAP):
    """最小二乘解 h 使 h*x ≈ y（x, y 等长；构 Toeplitz 正规方程）。"""
    n = min(len(x), len(y), NFIT)
    x, y = x[:n], y[:n]
    # 构造卷积矩阵的正规方程 (X^T X) h = X^T y，用自/互相关高效得到
    rxx = np.correlate(x, x, "full")
    mid = len(x) - 1
    R = np.empty((ntap, ntap))
    for i in range(ntap):
        for j in range(ntap):
            R[i, j] = rxx[mid + (i - j)]
    rxy = np.correlate(y, x, "full")
    p = np.array([rxy[mid + k] for k in range(ntap)])
    h = np.linalg.solve(R + np.eye(ntap) * 1e-12 * R[0, 0], p)
    return h


def apply_fir(h, x):
    return np.convolve(x, h)[:len(x)]


def rel_resid(pred, meas):
    n = min(len(pred), len(meas), NFIT)
    p, m = pred[:n], meas[:n]
    return float(np.sqrt(np.mean((p - m) ** 2)) / max(np.sqrt(np.mean(m ** 2)), 1e-30))


def test(r, name, base_key, base_val, mod_val, decays):
    print(f"\n=== {name} ===")
    wet = {"reverb_drywet": 1.0, "reverb_predelay": 1.0}

    d1 = decays[0]
    x1 = ir(r, {**wet, "reverb_decay": d1, base_key: base_val})
    y1 = ir(r, {**wet, "reverb_decay": d1, base_key: mod_val})
    h = solve_fir(x1, y1)
    fit = rel_resid(apply_fir(h, x1), y1)
    print(f"  在 decay={d1:.2f} 上拟合 {NTAP} 抽头 FIR：拟合残差 = {fit:.6f}")

    print("  用同一个 h 预测其它 decay 档：")
    worst = fit
    for d2 in decays[1:]:
        x2 = ir(r, {**wet, "reverb_decay": d2, base_key: base_val})
        y2 = ir(r, {**wet, "reverb_decay": d2, base_key: mod_val})
        rr = rel_resid(apply_fir(h, x2), y2)
        worst = max(worst, rr)
        print(f"    decay={d2:.2f}  相对残差 = {rr:.6f}"
              f"  ({'一致' if rr < 5 * max(fit, 1e-6) else '崩掉'})")
    verdict = "可分离（后置滤波）" if worst < 0.02 else "不可分离（环内滤波）"
    print(f"  → 最大残差 {worst:.6f} → **{verdict}**")
    return worst


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    decays = [0.0, 0.35, 0.7]
    test(r, "LOW CUT 700 Hz 相对 50 Hz", "reverb_lowcut", 0.0, 1.0, decays)
    test(r, "HIGH CUT 1000 Hz 相对 10 kHz", "reverb_highcut", 1.0, 0.0, decays)


if __name__ == "__main__":
    main()
