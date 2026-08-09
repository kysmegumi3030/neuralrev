"""长档逐 bin 残余：是不是**亚样点级的绝对时序**误差？

## 排除链（每一步都有可否证的判据，都已跑过）

| 假设 | 判据 | 结果 |
|---|---|---|
| 逐圈累积 | 限带对照做自造物地板 | **否**：fb=0.5 下 >12 kHz 过了 echo1 只剩自造物（SNR≈0） |
| 带电平（谱型）| echo1 单次通过 | **否**：1100 ms 的 12–14k = +1.51 dB，搁架已吃掉 |
| 近零点放大 | 按参考 bin 电平分层 | **否**：前两层中位 4.58 / 4.81 相同（2450/2727 个 bin），不随电平爆炸 |
| LFO 相位对齐 | 扫 d_lfophase 一整圈 | **否**：1100 ms 中位 5.03–5.15，**扫一圈只动 0.12 dB**；且现行值的波形 diff 4.15e−04 最优，别的相位都 >3e−03 |
| LFO 深度 | 比较净深度与残余 | **否**：0.9 档净深度 6.24 样点 > 1100 ms 的 2.56，残余反而更小（3.66 vs 5.13）|

而 1/12 倍频程平滑把 1100 ms 最差从 40.59 压到 **2.59 dB**（对照档 1.20）
⇒ **幅度响应已经对了**，错的只有细结构。

## 剩下的唯一候选：绝对时序

细结构（梳齿间距 SR/D = 0.909 Hz，比 bin 间距 0.732 Hz 还密）对时序极敏感，
而对平滑幅度完全不敏感 —— 这正是观测到的组合。且它必须与**延迟长度单调
相关**：1100 ms 的 D=52800，任一相对误差都被长度放大。

一个 τ 样点的纯时移在频率 f 上给出相位 2πfτ；在 13 kHz 上，**τ=0.04 样点
就是 39°**。所以「echo1 峰对齐到 −0.027 样点」（§14.14.4j 的时序核校）**远
不足以**保证 13 kHz 的逐 bin 对齐 —— 那个校核的分辨率不够。

## 测法：直接测每一档的亚样点时序差，再验证它能否解释残余

1. **互相关 + 抛物线内插**求 echo1 的亚样点时移 τ（宽带口径）。
2. **相位斜率**：在 12–14 kHz 上对 `unwrap(∠(C/A))` 做线性回归，斜率 ⇒ 该带
   自己的 τ_HF。若 τ_HF 与宽带 τ 不同，说明不是纯时移（有色散）。
3. **判决性一步**：把候选按 −τ_HF 做**分数样点平移**（频域精确移位），
   再重算逐 bin 残余。若残余大幅塌陷 ⇒ 残余就是时序，且给出应补的量；
   若不塌陷 ⇒ 时序也不是，需要另找。

第 3 步是关键：它不改插件、只在分析侧补偿，因此**先验证再实现**，
避免 §14.14.6 那种「先动常数、后找机制」。

用法：
    python3 tools/measure/ref_delay_subsample_timing.py
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
BAND = (12000.0, 14000.0)
GATE_DB = -40.0


def time_ms(norm: float) -> float:
    return T_MIN_MS + (T_MAX_MS - T_MIN_MS) * norm ** T_EXP


def burst(n: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    x = np.zeros(n)
    x[AT:AT + DUR] = rng.standard_normal(DUR) * AMP
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


def spectrum(y: np.ndarray) -> np.ndarray:
    seg = np.zeros(NFFT)
    s = y[AT:AT + NFFT]
    seg[:len(s)] = s
    return np.fft.rfft(seg)


def frac_shift(y: np.ndarray, tau: float) -> np.ndarray:
    """频域精确分数样点平移（正 tau = 右移/延后）。"""
    n = len(y)
    S = np.fft.rfft(y)
    f = np.fft.rfftfreq(n, 1.0)
    return np.fft.irfft(S * np.exp(-2j * np.pi * f * tau), n=n)


def xcorr_tau(a: np.ndarray, c: np.ndarray, at: int, w: int) -> float:
    """互相关 + 抛物线内插，求 c 相对 a 的亚样点滞后。"""
    sa = a[at:at + w]
    sc = c[at:at + w]
    r = np.correlate(sc - sc.mean(), sa - sa.mean(), mode="full")
    k = int(np.argmax(np.abs(r)))
    if 0 < k < len(r) - 1:
        y0, y1, y2 = np.abs(r[k - 1]), np.abs(r[k]), np.abs(r[k + 1])
        den = y0 - 2.0 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if abs(den) > 1e-30 else 0.0
    else:
        frac = 0.0
    return float(k - (len(sa) - 1) + frac)


def band_stats(A: np.ndarray, C: np.ndarray, keep: np.ndarray):
    dif = np.abs(20.0 * np.log10(np.maximum(np.abs(C[keep]), 1e-30)
                                 / np.maximum(np.abs(A[keep]), 1e-30)))
    return float(np.median(dif)), float(dif.max())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--norms", type=float, nargs="*", default=[1.00, 0.90, 0.65])
    ap.add_argument("--fb", type=float, default=0.5)
    args = ap.parse_args()

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    f = np.fft.rfftfreq(NFFT, 1.0 / SR)
    inb = (f >= BAND[0]) & (f <= BAND[1])
    n = AT + NFFT + SR
    x = burst(n)

    print("亚样点绝对时序：残余是不是时序？（13 kHz 上 τ=0.04 样点 = 39°）")
    print("第 3 列是判决性一步：只在分析侧按 −τ_HF 补偿，看残余是否塌陷\n")

    for nt in args.norms:
        ms = time_ms(nt)
        d = int(round(ms * 1e-3 * SR))
        a = np.asarray(ref.render(x, ref_params(nt, args.fb))[0], float)
        c = np.asarray(cand.render(x, cand_params(nt, args.fb))[0], float)
        L = min(len(a), len(c))
        a, c = a[:L], c[:L]

        A, Cs = spectrum(a), spectrum(c)
        pk = np.abs(A[inb]).max()
        keep = inb & (np.abs(A) > pk * 10.0 ** (GATE_DB / 20.0))

        # ① 宽带亚样点时移（在 echo1 窗上）
        tau_bb = xcorr_tau(a, c, AT + d - 200, 6000)

        # ② 12–14 kHz 自己的相位斜率 ⇒ τ_HF
        ratio = C[keep] / np.where(np.abs(A[keep]) > 1e-30, A[keep], 1e-30)
        ph = np.unwrap(np.angle(ratio))
        fk = f[keep]
        sl, ic = np.polyfit(fk, ph, 1)
        tau_hf = -sl / (2.0 * np.pi)
        resid = float(np.std(ph - (sl * fk + ic)))

        med0, wst0 = band_stats(A, C, keep)

        # ③ 判决性：按 −τ_HF 补偿候选，重算
        c2 = frac_shift(c, -tau_hf)
        med1, wst1 = band_stats(A, spectrum(c2), keep)

        print("=== norm=%.4f  (%.1f ms, D=%d) ===" % (nt, ms, d))
        print("  宽带 τ (互相关)      %+.4f 样点" % tau_bb)
        print("  12–14k τ_HF (相位斜率) %+.4f 样点   相位拟合残差 %.3f rad"
              % (tau_hf, resid))
        print("  12–14k 逐 bin：补偿前 中位 %5.2f 最差 %6.2f"
              % (med0, wst0))
        print("               补偿后 中位 %5.2f 最差 %6.2f  ⇒ %s"
              % (med1, wst1,
                 "塌陷 ⇒ 是时序" if med1 < 0.6 * med0 else "未塌陷 ⇒ 不是纯时序"))
        print()

    print("判读：塌陷且 τ_HF 随档位单调 ⇒ 找那个与长度成比例的时序源；")
    print("      τ_HF 与宽带 τ 差很多 ⇒ 有色散（不是纯时移）；")
    print("      不塌陷 ⇒ 时序也排除，残余是**逐 bin 相位噪声**（可能不可修）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
