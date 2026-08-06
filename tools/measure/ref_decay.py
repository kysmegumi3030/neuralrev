"""DECAY 的定量反演：参数 → 每圈衰减（反馈系数）。

已知（§7）：参数值不是 T60（显示 4.25 s 时实测 T60 = 2.69 s）。
DECAY=1.0 时反馈接近 1（EDC 回归斜率仅 −0.38 dB/s）。

测法：
  A) EDC 斜率（dB/s）随参数的变化 → 每秒衰减量。
     若网络是「一组延迟线 + 统一反馈 g」，则
         每圈衰减 = 20·log₁₀(g) dB，每秒圈数 = SR / meanDelay
     → 斜率 = 20·log₁₀(g) · SR / meanDelay
     故 g = 10^(斜率 · meanDelay / (20·SR))。
     meanDelay 未知，但斜率本身是可直接测的、也是实现时要对上的量。

  B) 直接测 T60 与参数的关系，并检验若干候选闭式：
       * 1/T60 线性于参数
       * T60 线性于参数
       * 斜率线性于 (1 − 参数)
       * 斜率 ∝ 1/参数

  C) 检验衰减是否**频率相关**（分频带各自测 EDC 斜率）：
     若高频衰减更快 → 反馈环内有低通（damping），这会与「滤波器可分离」的
     结论并存（可分离说的是 LOW/HIGH CUT 参数，不排除环内有固定的 damping）。

用法：python3 tools/measure/ref_decay.py
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


def ir(r, params, tail_sec):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[0][IMPULSE_AT + LATENCY:]


def edc_slope(y, lo_db=-5.0, hi_db=-35.0, win=4096):
    """能量包络在 [lo_db, hi_db] 段的线性回归斜率（dB/s）与拟合 T60。"""
    e = np.sqrt(np.convolve(y ** 2, np.ones(win) / win, "same"))
    pk = e.max()
    edb = 20 * np.log10(np.maximum(e, 1e-300) / pk)
    idx = np.nonzero((edb < lo_db) & (edb > hi_db))[0]
    if len(idx) < 200:
        return float("nan"), float("nan")
    t = idx / SR
    A = np.vstack([t, np.ones_like(t)]).T
    slope, _ = np.linalg.lstsq(A, edb[idx], rcond=None)[0]
    return float(slope), float(-60.0 / slope) if slope < 0 else float("inf")


def band_slope(y, lo_hz, hi_hz):
    """带通后再测 EDC 斜率（判断衰减是否频率相关）。"""
    from scipy.signal import butter, sosfiltfilt
    sos = butter(4, [lo_hz / (SR / 2), min(hi_hz / (SR / 2), 0.99)], btype="band", output="sos")
    return edc_slope(sosfiltfilt(sos, y))[0]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    wet = {"reverb_drywet": 1.0, "reverb_predelay": 0.0}

    print("=== A) EDC 斜率与 T60 随 DECAY ===")
    print("   norm  参数(s)   斜率(dB/s)    T60(s)     1/T60")
    rows = []
    for dv in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]:
        tail = 6.0 if dv < 0.5 else (14.0 if dv < 0.85 else 30.0)
        y = ir(r, {**wet, "reverb_decay": dv}, tail)
        sl, t60 = edc_slope(y)
        rows.append((dv, V.decay_seconds(dv), sl, t60))
        inv = 1.0 / t60 if np.isfinite(t60) and t60 > 0 else float("nan")
        print(f"   {dv:.2f}  {V.decay_seconds(dv):6.2f}   {sl:9.3f}  {t60:9.3f}  {inv:8.4f}")

    # ---- B) 候选闭式 ----
    arr = np.array([(d, p, s, t) for d, p, s, t in rows if np.isfinite(s) and s < -0.5])
    n_, p_, s_, t_ = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    print("\n=== B) 候选闭式的线性度（相关系数 r²）===")
    cands = {
        "斜率 vs 参数":        (p_, s_),
        "斜率 vs 1/参数":      (1.0 / p_, s_),
        "1/T60 vs 参数":       (p_, 1.0 / t_),
        "1/T60 vs 1/参数":     (1.0 / p_, 1.0 / t_),
        "T60 vs 参数":         (p_, t_),
        "log|斜率| vs 参数":   (p_, np.log(np.abs(s_))),
        "1/T60 vs (1−norm)":   (1.0 - n_, 1.0 / t_),
    }
    for name, (xx, yy) in cands.items():
        k, b = np.polyfit(xx, yy, 1)
        pred = k * xx + b
        r2 = 1.0 - np.sum((yy - pred) ** 2) / max(np.sum((yy - yy.mean()) ** 2), 1e-30)
        print(f"   {name:22s} r² = {r2:.6f}   (斜率 {k:+.5f}, 截距 {b:+.5f})")

    # ---- C) 频率相关衰减 ----
    print("\n=== C) 分频带 EDC 斜率（判断环内是否有 damping）===")
    print("   norm    125Hz   500Hz   2kHz    8kHz")
    for dv in [0.0, 0.5, 0.8]:
        tail = 6.0 if dv < 0.5 else 14.0
        y = ir(r, {**wet, "reverb_decay": dv}, tail)
        vals = []
        for lo, hi in [(88, 177), (354, 707), (1414, 2828), (5657, 11314)]:
            try:
                vals.append(band_slope(y, lo, hi))
            except Exception:
                vals.append(float("nan"))
        print(f"   {dv:.2f}  " + "".join(f"{v:8.2f}" for v in vals))


if __name__ == "__main__":
    main()
