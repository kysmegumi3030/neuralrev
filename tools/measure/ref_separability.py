"""可分离性检验：LOW/HIGH CUT 是「湿声总线上的后置滤波」还是「反馈环内的滤波」？

这是**决定实现路线**的一次测量，不是锦上添花：

  * 若可分离（后置滤波）：
        wet(params) = Filter_lo(fc) ∘ Filter_hi(fc) ∘ WetCore(decay, predelay)
    则整个 5 维参数空间塌缩成「1 维 decay 的 IR 族 + 2 个已知滤波器 + 1 个纯延迟」，
    可以用少量实测 IR 精确重建任意档位 → 用户要求的 1e-3 波形误差可达。

  * 若不可分离（环内滤波）：
        滤波器改变每圈的衰减，decay 与 fc 强耦合，IR 族是 3 维的，
    只能靠算法结构本身对齐 → 1e-3 波形误差需要**精确复现网络拓扑与系数**。

检验方法（LTI 已确证，homogeneity 1.3e-4，见 §3）：
  取 IR(lo=0, hi=1) 作为「未滤波核」，实测 IR(lo=a, hi=b)，
  在频域求传输比 H = FFT(IR_ab) / FFT(IR_00)。
  * 可分离 → H 与 decay **无关**（不同 decay 档给出同一条 H）
  * 不可分离 → H 随 decay 显著变化

同时对 DECAY 与 PRE-DELAY 做同样的交叉检验。

用法：python3 tools/measure/ref_separability.py
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
F = np.fft.rfftfreq(NFFT, 1.0 / SR)


def ir(r, params, tail_sec=6.0):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[:, IMPULSE_AT + LATENCY:]


def spec(y):
    s = np.zeros(NFFT)
    n = min(len(y), NFFT)
    s[:n] = y[:n]
    return np.fft.rfft(s)


def smooth_db(mag, oct_frac=1 / 6):
    out = np.zeros_like(mag)
    for i in range(len(mag)):
        if F[i] <= 0:
            out[i] = mag[i]
            continue
        m = (F >= F[i] * 2 ** -oct_frac) & (F <= F[i] * 2 ** oct_frac)
        out[i] = np.sqrt(np.mean(mag[m] ** 2)) if m.any() else mag[i]
    return 20 * np.log10(np.maximum(out, 1e-30))


PROBE_HZ = [50, 100, 200, 500, 1000, 2000, 4000, 8000, 12000]


def transfer(r, base_params, mod_params):
    """|IR(mod)| / |IR(base)| 的 1/6 oct 平滑 dB 曲线，在 PROBE_HZ 上取值。"""
    a = ir(r, base_params)[0]
    b = ir(r, mod_params)[0]
    da = smooth_db(np.abs(spec(a)))
    dbm = smooth_db(np.abs(spec(b)))
    d = dbm - da
    return [float(d[int(round(hz / SR * NFFT))]) for hz in PROBE_HZ]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    wet = {"reverb_drywet": 1.0, "reverb_predelay": 1.0}

    hdr = "  " + "".join(f"{hz:>8d}" for hz in PROBE_HZ)

    # ---- 1. HIGH CUT 的传输比是否随 DECAY 变化 ----
    print("=== HIGH CUT (fc=1000 Hz) 的传输比，在不同 DECAY 下 ===")
    print("  decay" + hdr)
    rows = []
    for dv in [0.0, 0.35, 0.7]:
        t = transfer(r,
                     {**wet, "reverb_decay": dv, "reverb_highcut": 1.0},
                     {**wet, "reverb_decay": dv, "reverb_highcut": 0.0})
        rows.append(t)
        print(f"  {dv:5.2f}" + "".join(f"{v:+8.2f}" for v in t))
    spread = np.max(np.abs(np.array(rows) - np.array(rows).mean(axis=0)), axis=0)
    print("  离散度" + "".join(f"{v:8.2f}" for v in spread))
    print(f"  → 最大离散度 {spread.max():.2f} dB："
          f"{'可分离（与 decay 无关）' if spread.max() < 1.5 else '不可分离（环内滤波）'}")

    # ---- 2. LOW CUT 的传输比是否随 DECAY 变化 ----
    print("\n=== LOW CUT (fc=700 Hz) 的传输比，在不同 DECAY 下 ===")
    print("  decay" + hdr)
    rows = []
    for dv in [0.0, 0.35, 0.7]:
        t = transfer(r,
                     {**wet, "reverb_decay": dv, "reverb_lowcut": 0.0},
                     {**wet, "reverb_decay": dv, "reverb_lowcut": 1.0})
        rows.append(t)
        print(f"  {dv:5.2f}" + "".join(f"{v:+8.2f}" for v in t))
    spread = np.max(np.abs(np.array(rows) - np.array(rows).mean(axis=0)), axis=0)
    print("  离散度" + "".join(f"{v:8.2f}" for v in spread))
    print(f"  → 最大离散度 {spread.max():.2f} dB："
          f"{'可分离（与 decay 无关）' if spread.max() < 1.5 else '不可分离（环内滤波）'}")

    # ---- 3. HIGH CUT 的传输比是否随 PRE-DELAY 变化 ----
    print("\n=== HIGH CUT (fc=1000 Hz) 的传输比，在不同 PRE-DELAY 下 ===")
    print("  predly" + hdr)
    rows = []
    for pv in [0.0, 0.5, 1.0]:
        t = transfer(r,
                     {"reverb_drywet": 1.0, "reverb_predelay": pv, "reverb_highcut": 1.0},
                     {"reverb_drywet": 1.0, "reverb_predelay": pv, "reverb_highcut": 0.0})
        rows.append(t)
        print(f"  {pv:5.2f}" + "".join(f"{v:+8.2f}" for v in t))
    spread = np.max(np.abs(np.array(rows) - np.array(rows).mean(axis=0)), axis=0)
    print("  离散度" + "".join(f"{v:8.2f}" for v in spread))
    print(f"  → 最大离散度 {spread.max():.2f} dB")


if __name__ == "__main__":
    main()
