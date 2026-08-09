"""12–14 kHz 残余的第三种身份：**带内逐 bin 分布**（带能量已对上）

## 前两种身份都已排除

* **逐圈累积**：`ref_delay_hf_rounds.py` 装上「限带对照做自造物地板」之后，
  12–14k / 14–16k 在 echo2 及之后**两侧都只剩自造物**（SNR ≈ 0 dB），
  fb=0.5 下过了 echo1 就没有真 HF 信号可累积。岔路关闭。
* **带电平（谱型）**：同一支工具的 echo1 单次通过读数，1100 ms 的 12–14k
  是 **+1.51 dB**、14–16k 是 **+0.06 dB** —— 搁架（§14.14.4j）已经把带
  电平吃掉了。

可是验收的逐 bin 仍是 **35.28 dB @ 13013 Hz**，该带**中位 3.74 dB**。
带能量对、逐 bin 不对 ⇒ 剩下的只能是**能量在带内的分配**。

## 两种分配误差，处置完全相反

  * **近零点放大（口径）**：噪声突发的谱本身有密集深零点；参考某个 bin 掉到
    比邻域低 20–30 dB 时，两侧一点相对差就放成几十 dB。这不是机制，
    §14.14.5 与 `ref_delay_worstbins.py` 都记过这个形状。**无需修**。
  * **真实宽带差异**：与 bin 电平无关的散布，平滑之后仍在。**要修**。

## 判据：平滑 + 按参考 bin 电平分层

两条独立的读数，必须同时看：

1. **平滑对比**：1/12 倍频程平滑前后的中位与最差。近零点放大会被平滑吃掉
   （零点被邻域填平）；真实差异不会。
2. **分层**：把 12–14k 的 bin 按「参考电平相对该带峰值」分成三层
   （≤10 dB / 10–20 dB / >20 dB 以下），各层单独报中位误差。
   近零点放大的指纹是**误差随层数单调爆炸**；真实宽带差异的指纹是
   **三层中位大致相同**。

口径守两条既有纪律：逐 bin 比较**不加窗**（加窗抹平梳状零点＝换口径，见
`ref_delay_worstbins.py`），窗取 `y[at:at+65536]` 与验收完全一致；同时用
线性区 std=1e−3 和验收电平 0.02 各跑一遍，确认结论与电平无关（0.02 已接近
饱和起弯的 0.03，§14.4）。

用法：
    python3 tools/measure/ref_delay_hf_binscatter.py
    python3 tools/measure/ref_delay_hf_binscatter.py --norms 1.0 0.9
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
AT = 19200          # 过参考的起始渐变（§14.3/§14.10）
DUR = 4800
NFFT = 65536        # 与验收同口径
SEED = 12345
LFO_PHASE = 0.238423

T_MIN_MS, T_MAX_MS, T_EXP = 100.0, 1100.0, 5.0 / 3.0

BAND = (12000.0, 14000.0)
GATE_DB = -40.0     # 相对该带峰值；再低的 bin 连参考自己都不可信


def time_ms(norm: float) -> float:
    return T_MIN_MS + (T_MAX_MS - T_MIN_MS) * norm ** T_EXP


def burst(n: int, amp: float) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    x = np.zeros(n)
    x[AT:AT + DUR] = rng.standard_normal(DUR) * amp
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
    """验收同口径：不加窗，取 y[AT:AT+NFFT] 的幅度谱。"""
    seg = np.zeros(NFFT)
    s = y[AT:AT + NFFT]
    seg[:len(s)] = s
    return np.abs(np.fft.rfft(seg))


def smooth_frac_oct(f: np.ndarray, mag: np.ndarray, frac: float) -> np.ndarray:
    """1/frac 倍频程功率平滑（与混响侧同一口径）。"""
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


def stats(dif: np.ndarray) -> str:
    return "中位 %5.2f  90分位 %6.2f  最差 %6.2f dB" % (
        np.median(np.abs(dif)), np.percentile(np.abs(dif), 90),
        np.max(np.abs(dif)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--norms", type=float, nargs="*", default=[1.00, 0.90, 0.65])
    ap.add_argument("--fb", type=float, default=0.5)
    ap.add_argument("--amps", type=float, nargs="*", default=[1e-3, 0.02])
    args = ap.parse_args()

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    print("12–14 kHz 带内逐 bin 分布（fb 归一 = %.2f，NFFT=%d，不加窗）" % (args.fb, NFFT))
    print("判据：平滑吃掉 ⇒ 近零点放大（口径，不修）；平滑后仍在 ⇒ 真实宽带差异（要修）")
    print("      分层单调爆炸 ⇒ 近零点放大；三层中位相同 ⇒ 真实差异\n")

    f = np.fft.rfftfreq(NFFT, 1.0 / SR)
    inb = (f >= BAND[0]) & (f <= BAND[1])

    for nt in args.norms:
        ms = time_ms(nt)
        print("=== norm=%.4f  (%.1f ms) ===" % (nt, ms))
        for amp in args.amps:
            n = AT + NFFT + SR
            x = burst(n, amp)
            a = np.asarray(ref.render(x, ref_params(nt, args.fb))[0], float)
            c = np.asarray(cand.render(x, cand_params(nt, args.fb))[0], float)
            A, Cc = spectrum(a), spectrum(c)

            # 门：相对该带峰值 GATE_DB 以下的 bin 连参考自己都不可信
            pk = A[inb].max()
            keep = inb & (A > pk * 10.0 ** (GATE_DB / 20.0))
            dif = 20.0 * np.log10(np.maximum(Cc[keep], 1e-30)
                                  / np.maximum(A[keep], 1e-30))

            As = smooth_frac_oct(f, A, 12.0)
            Cs = smooth_frac_oct(f, Cc, 12.0)
            difs = 20.0 * np.log10(np.maximum(Cs[keep], 1e-30)
                                   / np.maximum(As[keep], 1e-30))

            print("  amp=%.0e  有效 bin %d/%d" % (amp, keep.sum(), inb.sum()))
            print("    原始   " + stats(dif))
            print("    1/12oct " + stats(difs))

            # 分层：参考 bin 电平相对带峰值
            rel = 20.0 * np.log10(np.maximum(A[keep], 1e-30) / pk)
            for lo, hi, name in ((-10.0, 0.0, "≤10dB 以下"),
                                 (-20.0, -10.0, "10–20dB 以下"),
                                 (GATE_DB, -20.0, ">20dB 以下")):
                m = (rel > lo) & (rel <= hi)
                if not m.any():
                    continue
                print("    %-12s n=%5d  中位 %5.2f  最差 %6.2f dB"
                      % (name, m.sum(), np.median(np.abs(dif[m])),
                         np.max(np.abs(dif[m]))))
        print()

    print("对照：norm=0.65（LFO 零点）该带验收本来就过（最差 2.99 dB @ 6041 Hz），")
    print("      它的三层应当都很小 —— 否则说明本工具的分层口径自己有问题。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
