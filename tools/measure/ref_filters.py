"""LOW CUT / HIGH CUT 的滤波器阶数与拐点，以及 DRY/WET 的混合律。

测法：LOW/HIGH CUT 作用在**湿声支路**上，直接对湿声 IR 做频谱比值
（同一档位下改一个参数，其余不动），得到该滤波器的幅度响应。
比值法自动消掉混响本身的梳状着色。

判据：
  * 斜率 dB/oct → 阶数（6 = 1 极点，12 = 2 极点，…）
  * 在标称 fc 处的衰减量 → 拓扑（Butterworth 2 阶在 fc 为 −3 dB；
    两级串联单极点在 fc 为 −6 dB）

用法：python3 tools/measure/ref_filters.py
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
NFFT = 1 << 16


def ir(r, params, tail_sec=6.0):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[:, IMPULSE_AT + LATENCY:]


def spectrum(y):
    seg = np.zeros(NFFT)
    n = min(len(y), NFFT)
    seg[:n] = y[:n]
    return np.abs(np.fft.rfft(seg))


F = np.fft.rfftfreq(NFFT, 1.0 / SR)


def smooth(S, oct_frac=1 / 6):
    """1/6 倍频程 RMS 平滑（混响频谱梳状，必须平滑）。"""
    out = np.zeros_like(S)
    for i in range(len(S)):
        if F[i] <= 0:
            out[i] = S[i]
            continue
        m = (F >= F[i] * 2 ** -oct_frac) & (F <= F[i] * 2 ** oct_frac)
        out[i] = np.sqrt(np.mean(S[m] ** 2)) if m.any() else S[i]
    return out


def at(curve, hz):
    return float(curve[int(round(hz / SR * NFFT))])


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    wet = {"reverb_drywet": 1.0}

    flat = smooth(spectrum(ir(r, {**wet, "reverb_lowcut": 0.0, "reverb_highcut": 1.0})[0]))

    print("=== LOW CUT：相对 lowcut=0(50 Hz) 的传输函数 ===")
    print("  设定fc    @fc      斜率(dB/oct, fc/4→fc/2)   推定阶数")
    for lv in [0.25, 0.5, 0.75, 1.0]:
        cur = smooth(spectrum(ir(r, {**wet, "reverb_lowcut": lv, "reverb_highcut": 1.0})[0]))
        d = 20 * np.log10(np.maximum(cur, 1e-30) / np.maximum(flat, 1e-30))
        fc = V.lowcut_hz(lv)
        a_fc = at(d, fc)
        s1, s2 = at(d, fc / 4), at(d, fc / 2)
        slope = s2 - s1
        print(f"  {fc:6.0f} Hz  {a_fc:+6.2f} dB   {slope:+7.2f}"
              f"                {abs(slope)/6.0:.2f} 极点")

    print("\n=== HIGH CUT：相对 highcut=1(10 kHz) 的传输函数 ===")
    print("  设定fc    @fc      斜率(dB/oct, 2fc→4fc)     推定阶数")
    for hv in [0.0, 0.25, 0.5, 0.75]:
        cur = smooth(spectrum(ir(r, {**wet, "reverb_lowcut": 0.0, "reverb_highcut": hv})[0]))
        d = 20 * np.log10(np.maximum(cur, 1e-30) / np.maximum(flat, 1e-30))
        fc = V.highcut_hz(hv)
        a_fc = at(d, fc)
        f2, f4 = min(2 * fc, 20000), min(4 * fc, 22000)
        slope = at(d, f4) - at(d, f2)
        print(f"  {fc:6.0f} Hz  {a_fc:+6.2f} dB   {slope:+7.2f}"
              f"                {abs(slope)/6.0:.2f} 极点")

    # ---- DRY/WET 混合律 ----
    print("\n=== DRY/WET 混合律 ===")
    print("  测法：干路系数 = IR 中干冲激（t=0）的幅度；湿路系数 = 湿声能量的平方根比")
    n = IMPULSE_AT + int(3.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    print("   dw    干系数    湿能量      √(湿能量) 归一")
    wet_e0 = None
    for dw in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
        y = r.render(x, params={"reverb_drywet": dw}).astype(np.float64)[0]
        y = y[IMPULSE_AT + LATENCY:]
        dry_c = float(y[0])
        wet_e = float(np.sum(y[100:] ** 2))   # 跳过干冲激本身
        if wet_e0 is None or dw == 1.0:
            wet_e0 = wet_e if dw == 1.0 else wet_e0
        print(f"   {dw:.2f}  {dry_c:8.5f}  {wet_e:.6e}", end="")
        print()
    # 归一到 dw=1
    print("\n  → 若干系数 = (1−dw) 且 √湿能量 ∝ dw，则为线性混合；"
          "若为 cos/sin 律则是等功率混合。")


if __name__ == "__main__":
    main()
