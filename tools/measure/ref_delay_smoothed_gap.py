"""长档在**平滑口径**下剩的那 3.8–4.6 dB 落在哪个频段？

## 为什么现在才问这个

§14.14.8 走完排除链，把原来那个 35.28 dB 定性为**判据口径问题**（参考自比
在同一档同一带就有 29–36 dB）。改判平滑口径后，12 档 A/B 从 5/12 变成
**10/12**，剩两档：

  | 档位 | 平滑 | 该档平滑地板（参考自比）| 宽带 gain |
  |---|---|---|---|
  | 最长 1100 ms | **4.62 dB** | 0.42 dB | **0.9488** |
  | LFO 极大 0.9 | **3.82 dB** | 0.68 dB | **0.9651** |
  | （其余十档）| 0.26–2.99 | — | 0.995+ |

这一次差距是**真的**：地板 0.42/0.68，我们 4.62/3.82。而且 `waveform_diff`
报的宽带 gain 在这两档掉到 0.9488/0.9651，其余档都在 0.995 以上 ——
**一个与档位相关的宽带电平缺口**，平滑口径看得见它。

注意这不与 §14.14.8 矛盾：那一节证的是「原始逐 bin 的 35 dB 是相位散布、
不可修」，没有说幅度也没问题。平滑之后相位散布被吃掉，**剩下的才是幅度**。

## 测法

按倍频程带报**平滑谱**的候选 − 参考（有符号，不取绝对值 —— 符号告诉我们
是多了还是少了，这决定修的方向）。同时报：

1. **该档的平滑地板**（参考自比、偏移 1 样点，同一分带口径）—— 每一带都要有
   自己的地板，否则不知道某带的 1 dB 是缺陷还是本底。
2. **宽带能量比**（候选/参考，全带 RMS）—— 与 `waveform_diff` 的 gain 交叉核对。
3. **对照档 0.65** —— 它平滑口径过（1.78 dB）、gain 0.9854，各带应当都接近地板。

判读：若缺口集中在高带且随频率单调 ⇒ 搁架的量给少了（它只在 4 个节点上拟合，
末节点之上**不外推**，1100 ms 恰好是最后一个节点 52800）；若各带**齐平下移**
⇒ 是宽带增益，查 `kFitLoopFlatGain` / `kMeasWetGainSlope` 一类的标量。

用法：
    python3 tools/measure/ref_delay_smoothed_gap.py
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
LFO_PHASE = 0.238423

T_MIN_MS, T_MAX_MS, T_EXP = 100.0, 1100.0, 5.0 / 3.0
GATE_DB = -40.0

# 倍频程带（中心，下限，上限）
OCT = ((125.0, 88.0, 177.0), (250.0, 177.0, 354.0), (500.0, 354.0, 707.0),
       (1000.0, 707.0, 1414.0), (2000.0, 1414.0, 2828.0),
       (4000.0, 2828.0, 5657.0), (8000.0, 5657.0, 11314.0),
       (16000.0, 11314.0, 20000.0))


def time_ms(norm: float) -> float:
    return T_MIN_MS + (T_MAX_MS - T_MIN_MS) * norm ** T_EXP


def burst(n: int, at: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    x = np.zeros(n)
    x[at:at + DUR] = rng.standard_normal(DUR) * AMP
    return x


def ref_params(norm: float, fb: float) -> dict:
    return {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
            "delay_feedback": fb, "delay_lowpass": 1.0, "delay_highpass": 0.0,
            "delay_mode": 1.0}


def cand_params(norm: float, fb: float) -> dict:
    return {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
            "d_timel": norm, "d_timer": norm, "d_feedback": fb,
            "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
            "d_lfophase": LFO_PHASE}


def spectrum(y: np.ndarray, at: int) -> np.ndarray:
    seg = np.zeros(NFFT)
    s = y[at:at + NFFT]
    seg[:len(s)] = s
    return np.abs(np.fft.rfft(seg))


def smooth_frac_oct(f: np.ndarray, mag: np.ndarray, frac: float = 12.0) -> np.ndarray:
    P = mag ** 2
    out = np.empty_like(P)
    r = 2.0 ** (0.5 / frac)
    for i, fc in enumerate(f):
        if fc <= 0.0:
            out[i] = P[i]
            continue
        m = (f >= fc / r) & (f <= fc * r)
        out[i] = P[m].mean() if m.any() else P[i]
    return np.sqrt(out)


def band_delta(f, S_ref, S_cand, keep, lo, hi):
    """该带内平滑谱的**有符号**中位差（dB）。"""
    m = keep & (f >= lo) & (f <= hi)
    if not m.any():
        return None
    d = 20.0 * np.log10(np.maximum(S_cand[m], 1e-30)
                        / np.maximum(S_ref[m], 1e-30))
    return float(np.median(d))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--norms", type=float, nargs="*", default=[1.00, 0.90, 0.65])
    ap.add_argument("--fb", type=float, default=0.5)
    args = ap.parse_args()

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    f = np.fft.rfftfreq(NFFT, 1.0 / SR)
    n = AT + NFFT + SR

    print("平滑口径下的剩余缺口按倍频程拆带（有符号：负 = 候选偏少）")
    print("每带同时给该档的**平滑地板**（参考自比、偏移 1 样点）作对照\n")

    for nt in args.norms:
        ms = time_ms(nt)
        x = burst(n, AT)
        a = np.asarray(ref.render(x, ref_params(nt, args.fb))[0], float)
        c = np.asarray(cand.render(x, cand_params(nt, args.fb))[0], float)
        # 地板：参考自己，激励偏移 1 样点
        a1 = np.asarray(ref.render(burst(n, AT + 1),
                                   ref_params(nt, args.fb))[0], float)

        A = spectrum(a, AT)
        Cc = spectrum(c, AT)
        A1 = spectrum(a1, AT + 1)

        pk = A.max()
        keep = A > pk * 10.0 ** (GATE_DB / 20.0)

        SA = smooth_frac_oct(f, A)
        SC = smooth_frac_oct(f, Cc)
        S1 = smooth_frac_oct(f, A1)

        # 宽带能量比
        eg = float(np.sqrt((Cc[keep] ** 2).sum() / max((A[keep] ** 2).sum(), 1e-300)))

        print("=== norm=%.4f  (%.1f ms)   宽带能量比 %.4f (%.2f dB) ==="
              % (nt, ms, eg, 20.0 * np.log10(max(eg, 1e-30))))
        print("    带      候选−参考      地板(自比)")
        for fc, lo, hi in OCT:
            d = band_delta(f, SA, SC, keep, lo, hi)
            fl = band_delta(f, SA, S1, keep, lo, hi)
            if d is None:
                continue
            print("  %6.0f Hz   %+7.2f dB     %+7.2f dB%s"
                  % (fc, d, fl if fl is not None else float("nan"),
                     "   ← 超地板 3 倍以上" if fl is not None
                     and abs(d) > max(3.0 * abs(fl), 0.5) else ""))
        print()

    print("判读：缺口集中在高带且随频率单调 ⇒ 搁架量给少了（末节点之上不外推，")
    print("      1100 ms 恰在最后一个节点 52800 上）；")
    print("      各带齐平下移 ⇒ 宽带增益标量（kFitLoopFlatGain / kMeasWetGainSlope）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
