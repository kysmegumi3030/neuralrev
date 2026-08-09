"""长档逐 bin 判据的**真实下限**：参考自己与自己比

## 为什么必须先测这个，再谈"修"

排除链已把长档 12–14 kHz 残余的身份定死（每一步都有可否证判据）：

| 假设 | 判据 | 结果 |
|---|---|---|
| 逐圈累积 | 限带对照做自造物地板 | 否：fb=0.5 下 >12 kHz 过 echo1 只剩自造物 |
| 带电平（谱型）| echo1 单次通过 | 否：1100 ms 的 12–14k = +1.51 dB |
| 近零点放大 | 按参考 bin 电平分层 | 否：前两层中位 4.58/4.81 相同（2450/2727 bin）|
| LFO 相位对齐 | 扫 `d_lfophase` 一整圈 | 否：中位 5.03–5.15，**一整圈只动 0.12 dB** |
| LFO 深度 | 净深度 vs 残余 | 否：0.9 档深度 6.24 > 1100 ms 的 2.56，残余反而小 |
| 亚样点时序 | 相位斜率 + 分数移位补偿 | 否：τ_HF = 0.0017 样点，补偿后残余**一点没动** |

而最后一步顺带量到决定性的一条：**相位拟合残差 14.844 rad**（1100 ms）
对 **0.169 rad**（对照档 0.65）—— 差 88 倍。

结论：长档的湿声与参考**能量谱一致（平滑后 1.29 vs 对照 1.20）、群延迟一致
（0.002 样点）、相位谱逐 bin 随机散开**。

**没有任何 LTI 环节能补这个。**滤波器给出确定的相位函数；随机散布不是函数。
它只能来自**时变**：长档 LFO 在 65536 窗内让梳齿持续移动（净深度 2.56 /
6.24 样点，而 12–14 kHz 的周期只有 3.4–4 样点 —— 调制量是波长的量级），
两侧移动轨迹的任何微小差异都让每个 bin 的相位各自跑开。对照档净深度
0.005 样点，所以它的残差是 0.169 rad。

## 于是判据本身要重新标定

§14.6 量到「参考自比、1 样点偏移 ⇒ 0.49 dB」，据此认定延迟段可以用**原始逐
bin ≤3 dB**（不像混响那样需要平滑）。但那个数是在什么档位上量的？若是 LFO
零点附近，它就**只对零点档成立** —— 长档是时变的，参考自己与自己比就已经
散开，那时 3 dB 是一个物理上无法达到的目标，而不是我们的缺陷。

本工具量每档的**不可逾越下限**，两种口径各自独立：

1. **参考 vs 参考，激励偏移 1 样点**（§14.6 的原口径，逐档重做）。
2. **参考 vs 参考，同一激励、渲染长度不同**（不改激励，只改缓冲长度；
   若 LFO 锚在渲染起点则这一项应为 0，是对①的交叉核对）。

同时报原始与 1/12 倍频程平滑两种统计，因为若下限本身就 >3 dB，那么
**平滑口径就不是"放宽"，而是长档上唯一有意义的口径** —— 这正是混响段
（线性时变）当初改用平滑的同一条理由，不是新的让步。

用法：
    python3 tools/measure/ref_delay_longsetting_floor.py
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V          # noqa: E402

SR = 48000
AT = 19200
DUR = 4800
NFFT = 65536
SEED = 12345
AMP = 1e-3

T_MIN_MS, T_MAX_MS, T_EXP = 100.0, 1100.0, 5.0 / 3.0
BAND = (12000.0, 14000.0)
GATE_DB = -40.0

LFO_AMP = 3.27550
LFO_RATE = 1.70186


def time_ms(norm: float) -> float:
    return T_MIN_MS + (T_MAX_MS - T_MIN_MS) * norm ** T_EXP


def net_depth(d: float) -> float:
    T = SR / LFO_RATE
    return 2.0 * LFO_AMP * abs(np.sin(np.pi * d / T))


def burst(n: int, at: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    x = np.zeros(n)
    x[at:at + DUR] = rng.standard_normal(DUR) * AMP
    return x


def ref_params(norm: float, fb: float) -> dict:
    return {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
            "delay_feedback": fb, "delay_lowpass": 1.0, "delay_highpass": 0.0,
            "delay_mode": 1.0}


def spectrum(y: np.ndarray, at: int) -> np.ndarray:
    seg = np.zeros(NFFT)
    s = y[at:at + NFFT]
    seg[:len(s)] = s
    return np.abs(np.fft.rfft(seg))


def smooth_frac_oct(f: np.ndarray, mag: np.ndarray, frac: float) -> np.ndarray:
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


def compare(A: np.ndarray, B: np.ndarray, keep: np.ndarray):
    d = np.abs(20.0 * np.log10(np.maximum(B[keep], 1e-30)
                               / np.maximum(A[keep], 1e-30)))
    return float(np.median(d)), float(np.percentile(d, 90)), float(d.max())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--norms", type=float, nargs="*",
                    default=[0.65, 0.90, 1.00])
    ap.add_argument("--fb", type=float, default=0.5)
    args = ap.parse_args()

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    f = np.fft.rfftfreq(NFFT, 1.0 / SR)
    inb = (f >= BAND[0]) & (f <= BAND[1])

    print("长档判据的真实下限：**参考自己与自己比**（12–14 kHz，NFFT=%d，不加窗）"
          % NFFT)
    print("口径①激励偏移 1 样点（§14.6 原口径）；②只改渲染长度（LFO 锚在起点则应为 0）")
    print("若下限本身 >3 dB，则长档的原始逐 bin ≤3 dB 是物理上不可达的目标\n")

    for nt in args.norms:
        ms = time_ms(nt)
        d = ms * 1e-3 * SR
        n = AT + NFFT + SR

        # 基准
        y0 = np.asarray(ref.render(burst(n, AT), ref_params(nt, args.fb))[0], float)
        A0 = spectrum(y0, AT)
        pk = A0[inb].max()
        keep = inb & (A0 > pk * 10.0 ** (GATE_DB / 20.0))

        # ① 激励偏移 1 样点（分析窗同步跟着挪，比的是同一段回声）
        y1 = np.asarray(ref.render(burst(n, AT + 1), ref_params(nt, args.fb))[0], float)
        A1 = spectrum(y1, AT + 1)

        # ② 只改渲染长度
        y2 = np.asarray(ref.render(burst(n + 4096, AT),
                                   ref_params(nt, args.fb))[0], float)
        A2 = spectrum(y2, AT)

        S0 = smooth_frac_oct(f, A0, 12.0)
        S1 = smooth_frac_oct(f, A1, 12.0)

        print("=== norm=%.4f  (%.1f ms, LFO 净深度 %.3f 样点) ==="
              % (nt, ms, net_depth(d)))
        print("  ① 偏移 1 样点   原始   中位 %5.2f  90分位 %6.2f  最差 %6.2f dB"
              % compare(A0, A1, keep))
        print("                 1/12oct 中位 %5.2f  90分位 %6.2f  最差 %6.2f dB"
              % compare(S0, S1, keep))
        print("  ② 改渲染长度   原始   中位 %5.2f  90分位 %6.2f  最差 %6.2f dB"
              % compare(A0, A2, keep))
        print()

    print("判读：①随档位增大而增大、长档 >3 dB ⇒ 判据须按档位改用平滑口径")
    print("      （与混响段线性时变改用平滑是同一条理由，不是新的让步）；")
    print("      ②不为 0 ⇒ LFO 未锚在渲染起点，那是另一个独立问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
