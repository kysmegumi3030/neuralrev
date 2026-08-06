"""DECAY 律的闭式确定 + 环内 damping 的量化。

上一轮（ref_decay.py）给出：
  * 1/T60 线性于 DECAY 参数，r² = 0.9953（等价地：EDC 斜率线性于参数）
  * 8 kHz 频带的衰减明显快于中低频（norm=0.8 时 −16.4 vs −7.3 dB/s）
    → **反馈环内有低通 damping**（与「LOW/HIGH CUT 参数可分离」不矛盾：
      可分离说的是那两个用户参数，环内 damping 是固定的结构）

r²=0.9953 说明「线性」只是近似（两端有系统偏差）。本轮做两件事：

  A) 用**中段**（norm 0.1–0.9，避开两端的钳位/极限）重新拟合，
     并检验加一个二次项能否把残差压到测量噪声以下。
     同时给出「每圈反馈系数 g」的形式：
         斜率(dB/s) = 20·log₁₀(g) · SR / D̄
     其中 D̄ 是平均延迟线长度（未知）。但注意：**实现时要对上的是斜率本身**，
     g 与 D̄ 只在选定结构后才分离，所以这里输出斜率的闭式即可。

  B) 量化 damping：逐 1/3 倍频程测 EDC 斜率，得到「斜率(f)」曲线。
     若环内 damping 是一阶低通，则每圈增益 g(f) = g₀·|H_lp(f)|，
     斜率(f) = 20·log₁₀(g₀·|H_lp(f)|)·SR/D̄ = 斜率₀ + 20·log₁₀|H_lp(f)|·SR/D̄。
     即 **斜率(f) − 斜率(低频) 与 log|H_lp(f)| 成正比**，比例系数 SR/D̄ 与 DECAY 无关。
     检验这条比例关系跨 DECAY 档是否一致，可确认 damping 在环内且为固定滤波器。

用法：python3 tools/measure/ref_decay_law.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
LATENCY = 51
IMPULSE_AT = int(1.0 * SR)

# 1/3 倍频程中心频率
THIRD_OCT = [125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
             2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000]


def ir(r, params, tail_sec):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[0][IMPULSE_AT + LATENCY:]


def edc_slope(y, lo_db=-5.0, hi_db=-35.0, win=4096):
    e = np.sqrt(np.convolve(y ** 2, np.ones(win) / win, "same"))
    edb = 20 * np.log10(np.maximum(e, 1e-300) / e.max())
    idx = np.nonzero((edb < lo_db) & (edb > hi_db))[0]
    if len(idx) < 200:
        return float("nan")
    t = idx / SR
    A = np.vstack([t, np.ones_like(t)]).T
    return float(np.linalg.lstsq(A, edb[idx], rcond=None)[0][0])


def band(y, fc, frac=1 / 3):
    from scipy.signal import butter, sosfiltfilt
    lo = fc * 2 ** (-frac / 2) / (SR / 2)
    hi = min(fc * 2 ** (frac / 2) / (SR / 2), 0.985)
    if lo >= hi:
        return None
    sos = butter(4, [lo, hi], btype="band", output="sos")
    return sosfiltfilt(sos, y)


def tail_for(dv):
    return 6.0 if dv < 0.5 else (16.0 if dv < 0.85 else 30.0)


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    wet = {"reverb_drywet": 1.0, "reverb_predelay": 0.0}

    # ---- A) 中段重新拟合 ----
    print("=== A) 斜率(dB/s) vs DECAY 参数：线性 vs 二次 ===")
    norms = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    ps, ss = [], []
    for dv in norms:
        y = ir(r, {**wet, "reverb_decay": dv}, tail_for(dv))
        sl = edc_slope(y)
        ps.append(V.decay_seconds(dv))
        ss.append(sl)
    ps, ss = np.array(ps), np.array(ss)

    for deg, name in [(1, "线性"), (2, "二次")]:
        c = np.polyfit(ps, ss, deg)
        pred = np.polyval(c, ps)
        resid = np.max(np.abs(pred - ss))
        r2 = 1 - np.sum((ss - pred) ** 2) / np.sum((ss - ss.mean()) ** 2)
        print(f"  {name}拟合 r² = {r2:.7f}，最大残差 {resid:.4f} dB/s")
        print(f"    系数 = {np.array2string(c, precision=6)}")

    print("\n  逐点对比（线性模型）：")
    c1 = np.polyfit(ps, ss, 1)
    for p, s in zip(ps, ss):
        print(f"    参数={p:5.2f}  实测={s:9.3f}  线性={np.polyval(c1,p):9.3f}"
              f"  Δ={s-np.polyval(c1,p):+7.3f}")

    # 1/T60 形式（更直观）
    print("\n  等价的 1/T60 形式：1/T60 = a·(参数) + b")
    inv = -ss / 60.0
    ci = np.polyfit(ps, inv, 1)
    print(f"    a = {ci[0]:+.6f} /s²，b = {ci[1]:+.6f} /s")
    print(f"    → T60(参数) = 1 / ({ci[0]:+.6f}·参数 {ci[1]:+.6f})")
    print(f"    参数=8.0 时 T60 = {1/max(np.polyval(ci,8.0),1e-9):.1f} s"
          f"（实测 321 s，接近无限延音）")

    # ---- B) damping ----
    print("\n=== B) 分频带斜率（1/3 oct）与 damping 的一致性 ===")
    curves = {}
    for dv in [0.2, 0.5, 0.8]:
        y = ir(r, {**wet, "reverb_decay": dv}, tail_for(dv))
        row = []
        for fc in THIRD_OCT:
            b = band(y, fc)
            row.append(edc_slope(b) if b is not None else float("nan"))
        curves[dv] = np.array(row)

    print("     fc(Hz)" + "".join(f"  d={d:.1f}" for d in curves))
    for i, fc in enumerate(THIRD_OCT):
        print(f"    {fc:7d}" + "".join(f"{curves[d][i]:8.2f}" for d in curves))

    # 相对低频的超额衰减，除以各档自身的 |斜率(低频)|
    print("\n  超额衰减 Δ(f) = 斜率(f) − 斜率(250Hz)，再除以 (SR/D̄) 的比例检验：")
    print("  若 damping 在环内且固定，则各档的 Δ(f) 应彼此成比例，")
    print("  比例因子 = 斜率(250Hz) 之比。归一后应重合：")
    i250 = THIRD_OCT.index(250)
    print("     fc(Hz)" + "".join(f"  d={d:.1f}" for d in curves))
    for i, fc in enumerate(THIRD_OCT):
        vals = []
        for d in curves:
            base = curves[d][i250]
            vals.append((curves[d][i] - base) / abs(base) if np.isfinite(base) else float("nan"))
        print(f"    {fc:7d}" + "".join(f"{v:8.4f}" for v in vals))


if __name__ == "__main__":
    main()
