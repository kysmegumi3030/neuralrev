"""量出「长延迟档 HF 搁架」的需求曲线（逐档），为环内搁架定律做输入。

## 这个工具在测什么

§14.14.4f 已经确定：候选与参考在长延迟档的差异是**一个短 LTI 核**。方法是
时域最小二乘解 `min ||ref − conv(cand, k)||` —— 全程无除法，因此没有
§14.14.4e 那个频域相除的条件数问题（除以一个已经 −52 dB 的响应只会放大噪声）。

本脚本把那个拟合推广到**多个档位**，输出每档的核幅度响应，
用来判断「与延迟长度相关的 HF 搁架」能不能写成一条光滑的律。

## 测法自带的两条自查（缺了任何一条，读数都不可信）

1. **对照档**：norm=0.65（588 ms）本来就对得上。它必须拟合出**近似单位冲激**
   （中心抽头 ≈1、其余 ≈0）、拟合前残差已经很负。若对照档给出「拟合前残差 ≈0 dB」，
   说明候选侧根本没出声 —— 参数名写错时 `Renderer._merge` 会**静默忽略**未知键，
   `d_active` 保持 0，延迟段不参与信号。候选侧参数名见 `ab_delay.py:cand_params()`。
2. **两侧峰位**：都必须落在预测 echo 位置附近（±200 样点内）。

## 为什么不扫 LFO 起相

已实测：核的**幅度响应与 LFO 相位无关**（1100 ms 的 16k 在 8 个相位下极差
0.28 dB），只有核内**时移**随相位走。搁架是幅度的事，所以这里固定 phase=0，
只读幅度，并把核内时移一并打印出来供核对（它属于另一个自由度，见 §14.14.4f）。

用法：
    python3 tools/measure/ref_delay_hf_shelf.py            # 默认 12 档
    python3 tools/measure/ref_delay_hf_shelf.py --quick    # 4 档
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
AMP = 1e-3          # 线性区；>0.03 会进湿路饱和，测的就不是滤波器了
RAMP_GUARD = 19200  # 参考开头约 400 ms 电平在爬升，激励必须放在这之后
FIT_W = 3000        # 拟合窗，覆盖 echo 峰 + 环路尾（支撑约 1983 样点）
FIT_TAPS = 31       # 31 抽头实测把残差压到 −60/−68 dB
NF = 4096           # 核的频响分析点数

# 时间映射（与 DelayTuning.h 一致）
T_MIN_MS, T_MAX_MS, T_EXP = 100.0, 1100.0, 5.0 / 3.0

PROBE_HZ = (1000, 2000, 4000, 6000, 8000, 10000, 12000, 14000, 16000, 18000, 20000)


def time_ms(norm: float) -> float:
    return T_MIN_MS + (T_MAX_MS - T_MIN_MS) * norm ** T_EXP


def _bin(hz: float, n: int) -> int:
    return int(round(hz / (SR / n)))


def ref_params(norm: float) -> dict:
    return {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
            "delay_feedback": 0.0, "delay_lowpass": 1.0, "delay_highpass": 0.0,
            "delay_mode": 1.0}


def cand_params(norm: float) -> dict:
    # 名称必须是候选侧的（d_*）。混响 drywet=0 ⇒ 干路直通，只测延迟段。
    return {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
            "d_timel": norm, "d_timer": norm, "d_feedback": 0.0,
            "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
            "d_lfophase": 0.0}


def render_pair(ref, cand, norm: float):
    ms = time_ms(norm)
    d_int = int(round(ms * 1e-3 * SR))
    n = RAMP_GUARD + d_int + 60000
    x = np.zeros(n)
    x[RAMP_GUARD] = AMP
    a = np.asarray(ref.render(x, ref_params(norm))[0], float)
    c = np.asarray(cand.render(x, cand_params(norm))[0], float)
    return a, c, RAMP_GUARD + d_int, ms


def fit_kernel(a: np.ndarray, c: np.ndarray, echo_at: int):
    """解 min ||ref − conv(cand,k)||，返回 (k, 拟合前残差dB, 拟合后残差dB, 相关)。"""
    s0 = echo_at - 200
    ra = a[s0:s0 + FIT_W]
    rc = c[s0:s0 + FIT_W]
    half = FIT_TAPS // 2
    M = np.zeros((FIT_W, FIT_TAPS))
    for j in range(FIT_TAPS):
        sh = j - half
        M[:, j] = c[s0 - sh:s0 - sh + FIT_W]
    k, _, _, _ = np.linalg.lstsq(M, ra, rcond=None)
    e_ref = float(np.sum(ra ** 2))
    before = 10 * np.log10(np.sum((ra - rc) ** 2) / e_ref)
    after = 10 * np.log10(np.sum((ra - M @ k) ** 2) / e_ref)
    da, dc = ra - ra.mean(), rc - rc.mean()
    corr = float(np.sum(da * dc) / np.sqrt(np.sum(da ** 2) * np.sum(dc ** 2)))
    return k, before, after, corr


def decompose(k: np.ndarray):
    """把核分成「时移」+「幅度响应」。时移用 0.5–6 kHz 群延迟估计后扣掉。"""
    half = len(k) // 2
    K = np.fft.rfft(k, NF)
    lo, hi = _bin(500, NF), _bin(6000, NF)
    ph = np.unwrap(np.angle(K))
    A = np.vstack([np.arange(lo, hi + 1), np.ones(hi - lo + 1)]).T
    slope, _ = np.linalg.lstsq(A, ph[lo:hi + 1], rcond=None)[0]
    tau = -slope * NF / (2.0 * np.pi) - half
    mag = 20 * np.log10(np.maximum(np.abs(K), 1e-30))
    mag = mag - mag[_bin(1000, NF)]
    # 平滑度：0.2–18 kHz 幅度的二阶差分 rms。真实滤波形状应当很小。
    seg = mag[_bin(200, NF):_bin(18000, NF)]
    rough = float(np.sqrt(np.mean(np.diff(seg, 2) ** 2)))
    return tau, mag, rough


def direct_hf_delta(a: np.ndarray, c: np.ndarray, echo_at: int,
                    lo_hz=15000.0, hi_hz=17000.0, n=2048) -> float:
    """不含拟合的两侧直接读数：cand − ref 的 15–17 kHz 电平（各自相对 1 kHz）。

    这是对 `fit_kernel` 的独立校核。拟合会在候选无能量的频带上索要巨大增益
    （见 §14.14.4h：echo2/3 曾读到 +28/+69 dB，实为放大数值尘埃），
    而这条读数没有回归、不会有那个失效模式。两者符号相反、量值应吻合。
    """
    out = []
    for y in (a, c):
        seg = y[echo_at - 200:echo_at - 200 + n]
        P = np.abs(np.fft.rfft(seg)) ** 2
        hf = P[_bin(lo_hz, n):_bin(hi_hz, n) + 1].sum()
        out.append(10 * np.log10(max(hf / max(P[_bin(1000, n)], 1e-300), 1e-300)))
    return out[1] - out[0]


def measure_setting(ref, cand, norm: float, offsets):
    """一个档位：按激励偏移平均后的需求。

    **为什么必须按偏移平均**（§14.14.4i）：固定档位、只改激励偏移，
    16 kHz 需求在 719 ms 档能摆 **12.24 dB** —— 比当初被当成"档位属性"的
    跨档 ±5 dB 散布还大。所以单次渲染读到的中段"摆动"是抽样，不是结构。
    需求真正显著的长档反而很稳（1100 ms 极差 0.68 dB）。
    """
    fits, directs, taus, afters = [], [], [], []
    for off in offsets:
        at = RAMP_GUARD + int(off)
        ms = time_ms(norm)
        d_int = int(round(ms * 1e-3 * SR))
        n = at + d_int + 60000
        x = np.zeros(n)
        x[at] = AMP
        a = np.asarray(ref.render(x, ref_params(norm))[0], float)
        c = np.asarray(cand.render(x, cand_params(norm))[0], float)
        echo_at = at + d_int
        pa = int(np.argmax(np.abs(a[at + 100:]))) + at + 100
        pc = int(np.argmax(np.abs(c[at + 100:]))) + at + 100
        if abs(pa - echo_at) > 200 or abs(pc - echo_at) > 200:
            continue
        k, before, after, corr = fit_kernel(a, c, echo_at)
        tau, mag, _rough = decompose(k)
        fits.append({h: mag[_bin(h, NF)] for h in PROBE_HZ})
        directs.append(direct_hf_delta(a, c, echo_at))
        taus.append(tau)
        afters.append(after)
    if not fits:
        return None
    avg = {h: float(np.mean([f[h] for f in fits])) for h in PROBE_HZ}
    spread = {h: float(np.max([f[h] for f in fits]) - np.min([f[h] for f in fits]))
              for h in PROBE_HZ}
    return {"norm": norm, "ms": time_ms(norm), "n": len(fits),
            "avg": avg, "spread": spread,
            "direct": float(np.mean(directs)),
            "direct_spread": float(np.max(directs) - np.min(directs)),
            "tau_spread": float(np.max(taus) - np.min(taus)),
            "after": float(np.mean(afters))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--offsets", type=int, default=1,
                    help="每档的激励偏移数（>1 时按偏移平均，覆盖一个 LFO 周期）")
    args = ap.parse_args()

    if args.offsets > 1:
        return main_averaged(args)

    norms = [0.2512, 0.65, 0.90, 1.00] if args.quick else [
        0.2512, 0.4000, 0.5477, 0.6500, 0.7000, 0.7500,
        0.8000, 0.8500, 0.9000, 0.9500, 0.9750, 1.0000]

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    print("逐档拟合环内校正核（时域最小二乘，%d 抽头，窗 %d）" % (FIT_TAPS, FIT_W))
    print("对照档 norm=0.6500 必须给出近似单位冲激；否则本次读数全部作废\n")
    hdr = "  norm      ms   前dB    后dB   相关   时移  " + " ".join(
        "%5dk" % (h // 1000) for h in PROBE_HZ if h >= 8000)
    print(hdr, flush=True)

    rows = []
    for nt in norms:
        a, c, echo_at, ms = render_pair(ref, cand, nt)
        pa = int(np.argmax(np.abs(a[RAMP_GUARD + 100:]))) + RAMP_GUARD + 100
        pc = int(np.argmax(np.abs(c[RAMP_GUARD + 100:]))) + RAMP_GUARD + 100
        if abs(pa - echo_at) > 200 or abs(pc - echo_at) > 200:
            print("%.4f %7.1f  ** 峰位异常 ref Δ%+d / cand Δ%+d，跳过 **"
                  % (nt, ms, pa - echo_at, pc - echo_at), flush=True)
            continue
        k, before, after, corr = fit_kernel(a, c, echo_at)
        tau, mag, rough = decompose(k)
        vals = [mag[_bin(h, NF)] for h in PROBE_HZ if h >= 8000]
        rows.append((nt, ms, before, after, corr, tau, rough,
                     {h: mag[_bin(h, NF)] for h in PROBE_HZ}))
        print("%.4f %7.1f %+6.2f %+7.2f %6.4f %+6.2f  " % (nt, ms, before, after, corr, tau)
              + " ".join("%+6.2f" % v for v in vals), flush=True)

    ctl = [r for r in rows if abs(r[0] - 0.65) < 1e-9]
    print()
    if not ctl:
        print("!! 对照档缺失，无法判定测法有效性")
        return 1
    c16 = ctl[0][7][16000]
    print("对照档自查: norm=0.6500 的 16k 需求 = %+.2f dB（应 ≈0）；"
          "拟合前残差 %+.2f dB（应显著为负）" % (c16, ctl[0][2]))
    if abs(c16) > 1.5 or ctl[0][2] > -10.0:
        print("!! 对照档不合格 —— 检查候选侧参数名（须为 d_*，见 ab_delay.py:cand_params）")
        return 1
    print("对照档合格，以下需求曲线可用。\n")

    print("=== HF 搁架需求（dB，相对 1 kHz）===")
    print("  norm      ms    12k     14k     16k     18k    平滑度")
    for nt, ms, _b, _a, _c, _t, rough, m in rows:
        print("%.4f %7.1f %+7.2f %+7.2f %+7.2f %+7.2f   %.4f"
              % (nt, ms, m[12000], m[14000], m[16000], m[18000], rough))

    print()
    print("=== 16 kHz 需求 vs 延迟长度（判断能否写成一条律）===")
    d = np.array([r[1] * 1e-3 * SR for r in rows])
    y = np.array([r[7][16000] for r in rows])
    print("  D(样点)  16k需求")
    for di, yi in zip(d, y):
        print("  %7.0f  %+7.2f" % (di, yi))
    # 只在需求显著（>1 dB）的档上拟合，避免短档的 ≈0 拖平斜率
    m = y > 1.0
    if m.sum() >= 2:
        A = np.vstack([d[m], np.ones(int(m.sum()))]).T
        sl, ic = np.linalg.lstsq(A, y[m], rcond=None)[0]
        pred = A @ np.array([sl, ic])
        print("  显著档线性拟合: 斜率=%.4f dB/万样点  截距=%+.2f dB  "
              "残差rms=%.2f dB  起效点≈%.0f 样点(%.0f ms)"
              % (sl * 1e4, ic, float(np.sqrt(np.mean((y[m] - pred) ** 2))),
                 -ic / sl if sl else float("nan"),
                 (-ic / sl) / SR * 1e3 if sl else float("nan")))
    print("\n注意：≤588 ms 的档目前是 ±1 dB 通过的，搁架必须不碰它们，"
          "否则是净损失。上表若在短档给出非零需求，先怀疑测法而不是加滤波。")
    return 0


def main_averaged(args) -> int:
    """按激励偏移平均的需求曲线 —— 这是给搁架定律用的那一版。"""
    norms = [0.2512, 0.65, 0.85, 1.00] if args.quick else [
        0.2512, 0.4000, 0.5477, 0.6500, 0.7000, 0.7500,
        0.8000, 0.8500, 0.9000, 0.9500, 0.9750, 1.0000]
    # 偏移均匀铺满一个 LFO 周期（T = SR / kMeasLfoRateHz ≈ 28204 样点）
    T = SR / 1.70186
    offsets = [int(round(i * T / args.offsets)) for i in range(args.offsets)]

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    print("按激励偏移平均的 HF 搁架需求（每档 %d 个偏移，铺满一个 LFO 周期 %.0f 样点）"
          % (args.offsets, T))
    print("单档内的极差本身是读数的一部分：极差大 ⇒ 该档的需求是抽样噪声，不可用于定律\n")
    print("  norm      ms   n   16k均值  16k极差  直读均值  直读极差  时移极差")
    rows = []
    for nt in norms:
        r = measure_setting(ref, cand, nt, offsets)
        if r is None:
            print("%.4f  ** 全部偏移峰位异常，跳过 **" % nt, flush=True)
            continue
        rows.append(r)
        print("%.4f %7.1f %3d  %+7.2f  %7.2f  %+7.2f  %7.2f  %7.2f"
              % (r["norm"], r["ms"], r["n"], r["avg"][16000], r["spread"][16000],
                 r["direct"], r["direct_spread"], r["tau_spread"]), flush=True)

    ctl = [r for r in rows if abs(r["norm"] - 0.65) < 1e-9]
    if not ctl:
        print("\n!! 对照档缺失，无法判定测法有效性")
        return 1
    print("\n对照档 norm=0.6500 的 16k 需求均值 = %+.2f dB（应 ≈0，极差 %.2f dB）"
          % (ctl[0]["avg"][16000], ctl[0]["spread"][16000]))

    print("\n=== 偏移平均后的需求（dB，相对 1 kHz）===")
    print("  norm      ms     12k     14k     16k     18k")
    for r in rows:
        print("%.4f %7.1f %+7.2f %+7.2f %+7.2f %+7.2f"
              % (r["norm"], r["ms"], r["avg"][12000], r["avg"][14000],
                 r["avg"][16000], r["avg"][18000]))

    print("\n=== 判定哪些档的需求可用（极差 < 2 dB 且 |均值| > 2 dB）===")
    usable = [r for r in rows if r["spread"][16000] < 2.0 and abs(r["avg"][16000]) > 2.0]
    for r in rows:
        tag = "可用" if r in usable else ("需求≈0" if abs(r["avg"][16000]) <= 2.0 else "散布过大")
        print("%.4f %7.1f ms  16k=%+7.2f 极差%6.2f  → %s"
              % (r["norm"], r["ms"], r["avg"][16000], r["spread"][16000], tag))

    if len(usable) >= 2:
        d = np.array([r["ms"] * 1e-3 * SR for r in usable])
        y = np.array([r["avg"][16000] for r in usable])
        A = np.vstack([d, np.ones(len(d))]).T
        sl, ic = np.linalg.lstsq(A, y, rcond=None)[0]
        res = float(np.sqrt(np.mean((y - A @ np.array([sl, ic])) ** 2)))
        print("\n可用档线性拟合: 斜率=%.4f dB/万样点  截距=%+.2f dB  残差rms=%.3f dB"
              % (sl * 1e4, ic, res))
        print("起效点 ≈ %.0f 样点 (%.0f ms)" % (-ic / sl, (-ic / sl) / SR * 1e3))
        print("残差 rms <0.5 dB 才算一条律；否则仍是查表，不要写进 DSP。")
    else:
        print("\n可用档不足 2 个，无法定律。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
