"""长档逐 bin 残余的驱动量是不是 **LFO 对齐**？（相位扫描）

## 上游已确立的事实

`ref_delay_hf_binscatter.py` 给出三条：

  1. **带能量对**：echo1 单次通过 12–14k = +1.51 dB（1100 ms）。
  2. **平滑后也对**：1/12 倍频程平滑把最差从 40.59 压到 **2.59 dB**，
     中位 1.29 —— 与对照档（0.65）的 1.20 同量级。幅度响应没问题。
  3. **与 bin 电平无关**：前两层（占 2450/2727 个 bin）中位 4.58 / 4.81
     几乎相同，不随电平单调爆炸 ⇒ **不是**近零点放大。

⇒ 残余只剩一种身份：**梳状细结构错位**。1100 ms 的 D=52800，梳齿间距
SR/D = 0.909 Hz，而 NFFT=65536 的 bin 间距 0.732 Hz —— 梳齿基本没被解析，
此时亚样点级时序差就能让逐 bin 值剧烈变化，而平滑后消失。

## 为什么怀疑 LFO

净调制深度 2A·|sin(πD/T)|（A=3.27550，T=28204.4）：

  | 档位 | D | 净深度 | 原始/平滑 中位 |
  |---|---|---|---|
  | 0.65（对照）| 28212 | **0.005** 样点 | 1.23 / 1.20（**无**细结构误差）|
  | 0.90 | 45070 | 6.24 样点 | 3.66 / 1.35 |
  | 1.00 | 52800 | 2.55 样点 | 5.13 / 1.29 |

唯一没有细结构误差的档，恰好是唯一没有 LFO 的档。

## 判据（这是一个可以被否证的预测）

`d_lfophase` 现在是**一个全局标量** 0.238423（§14.9 用对照档标定的）。
若驱动量真是 LFO 对齐，那么在长档上扫这个相位，逐 bin 误差必须出现
**明显的极小**（深谷）。若曲线基本平坦，LFO 相位就不是驱动量，
假设被否证 —— 那时要查的是 LFO **速率**（窗内漂移，相位补不了）或深度。

同时打印**波形 diff**：相位是全局量，改它会动所有档，不能为了长档的
逐 bin 把已经通过的波形判据（<1e−3）弄坏。

用法：
    python3 tools/measure/ref_delay_lfo_phase_sweep.py
    python3 tools/measure/ref_delay_lfo_phase_sweep.py --norms 1.0 --steps 48
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V          # noqa: E402
from plugin_match import nrev_cand as C         # noqa: E402

SR = 48000
AT = 19200
DUR = 4800
NFFT = 65536
SEED = 12345
AMP = 1e-3
BASE_PHASE = 0.238423       # 现行全局值（§14.9）

T_MIN_MS, T_MAX_MS, T_EXP = 100.0, 1100.0, 5.0 / 3.0
BAND = (12000.0, 14000.0)
GATE_DB = -40.0

# LFO 实测量（DelayTuning.h）：用来报每档的净调制深度
LFO_AMP = 3.27550
LFO_RATE = 1.70186


def time_ms(norm: float) -> float:
    return T_MIN_MS + (T_MAX_MS - T_MIN_MS) * norm ** T_EXP


def net_depth(d: float) -> float:
    T = SR / LFO_RATE
    return 2.0 * LFO_AMP * abs(np.sin(np.pi * d / T))


def burst(n: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    x = np.zeros(n)
    x[AT:AT + DUR] = rng.standard_normal(DUR) * AMP
    return x


def ref_params(norm: float, fb: float) -> dict:
    return {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
            "delay_feedback": fb, "delay_lowpass": 1.0, "delay_highpass": 0.0,
            "delay_mode": 1.0}


def cand_params(norm: float, fb: float, ph: float) -> dict:
    return {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
            "d_timel": norm, "d_timer": norm, "d_feedback": fb,
            "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
            "d_lfophase": ph}


def spectrum(y: np.ndarray) -> np.ndarray:
    seg = np.zeros(NFFT)
    s = y[AT:AT + NFFT]
    seg[:len(s)] = s
    return np.abs(np.fft.rfft(seg))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--norms", type=float, nargs="*", default=[1.00, 0.90])
    ap.add_argument("--fb", type=float, default=0.5)
    ap.add_argument("--steps", type=int, default=24)
    args = ap.parse_args()

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    f = np.fft.rfftfreq(NFFT, 1.0 / SR)
    inb = (f >= BAND[0]) & (f <= BAND[1])
    n = AT + NFFT + SR
    x = burst(n)

    print("LFO 相位扫描：长档 12–14 kHz 逐 bin 残余对 d_lfophase 的敏感性")
    print("现行全局值 %.6f（§14.9 用对照档标定）；深谷 ⇒ LFO 对齐是驱动量，"
          % BASE_PHASE)
    print("平坦 ⇒ 假设被否证，改查 LFO 速率（窗内漂移）或深度\n")

    for nt in args.norms:
        ms = time_ms(nt)
        d = ms * 1e-3 * SR
        a = np.asarray(ref.render(x, ref_params(nt, args.fb))[0], float)
        A = spectrum(a)
        pk = A[inb].max()
        keep = inb & (A > pk * 10.0 ** (GATE_DB / 20.0))

        print("=== norm=%.4f  (%.1f ms, D=%.0f, LFO 净深度 %.3f 样点) ==="
              % (nt, ms, d, net_depth(d)))
        print("   相位     12-14k 中位   12-14k 最差   波形 diff")

        best = None
        phases = list(np.linspace(0.0, 1.0, args.steps, endpoint=False))
        if BASE_PHASE not in phases:
            phases.append(BASE_PHASE)
        for ph in sorted(phases):
            c = np.asarray(cand.render(x, cand_params(nt, args.fb, ph))[0], float)
            Cc = spectrum(c)
            dif = np.abs(20.0 * np.log10(np.maximum(Cc[keep], 1e-30)
                                         / np.maximum(A[keep], 1e-30)))
            med, wst = float(np.median(dif)), float(dif.max())
            L = min(len(a), len(c))
            wd = float(np.max(np.abs(a[:L] - c[:L])))
            tag = "  ← 现行" if abs(ph - BASE_PHASE) < 1e-9 else ""
            print("  %.6f   %9.2f     %9.2f    %.3e%s" % (ph, med, wst, wd, tag))
            if best is None or med < best[1]:
                best = (ph, med, wst, wd)

        print("  最优：相位 %.6f  中位 %.2f  最差 %.2f  波形 %.3e"
              % best)
        print()

    print("判读：若最优相位在各长档上**一致**且明显优于现行值 ⇒ 全局相位需重标定；")
    print("      若各档最优相位**互相冲突** ⇒ 单个标量补不了，是速率/深度误差；")
    print("      若曲线平坦 ⇒ LFO 不是驱动量，本假设否证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
