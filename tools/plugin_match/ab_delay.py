"""延迟段的量化对拍：候选 vs 参考插件，按**用户原始口径**判。

## 口径（与混响段不同 —— 这里用严的那一套）

用户的要求是：波形 diff < 1e-3、65536 点 FFT 下逐 bin ≤ 3 dB。
混响段当初放宽到 1/12 oct 平滑，理由是**参考混响与自己比都过不了原始逐 bin**
（内部 LFO 让它成为线性时变系统）。

延迟段**不需要**这个放宽，这是量出来的（`ref_delay_floor.py`）：
参考延迟与自己比，在激励整体平移 1 个样点时给出 0.49 dB / 6.31e-04，
平移 16 样点 1.97 dB，48 样点 2.35 dB。即只要 LFO 相位对齐到 ±48 样点
（LFO 周期 28204 样点的 0.17%），原始逐 bin ≤3 dB 就是可达的；
对齐到 ±1 样点时波形 1e-3 也可达。

LFO 相位是**确定性的、锚定在渲染起点**（重复渲染 Δ=0，渲染长度改变 Δ=0），
所以这不是运气问题，是一个可标定的标量。本脚本先扫这个相位偏移，
再在最优相位上报三项指标。

## 为什么先扫相位而不是直接判

候选侧 LfoDelayLine 的相位从 0 起算，参考侧的起相未知（只知道它确定）。
不标定就直接比，等价于随机取一个相位差 —— 上面那张表说明 480 样点的
错位就会给出 8.57 dB，会把一个正确的实现判成失败。

标定的合法性：这是**一个**全局标量，不随频率/延迟档/反馈变化。
若它需要随参数变，那说明机制错了，本脚本会因为「各档最优相位不一致」暴露出来。

用法：
    python3 tools/plugin_match/ab_delay.py            # 默认档位组
    python3 tools/plugin_match/ab_delay.py --quick    # 只跑 3 档
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
AMP = 1e-3          # 线性区（幅度 >0.03 会进湿路饱和，测的就不是滤波器了）
NFFT = 65536

# 参考插件开头约 400 ms 电平在爬升（实测收敛点，见 DelayTuning.h
# kMeasStartRampSamples）。所有电平/谱比较都必须从这之后开始取，
# 否则量到的是渐变而不是 DSP。
RAMP_GUARD = 19200


def analysis_window(y, at: int, n: int = NFFT) -> np.ndarray:
    """从激励处起取 n 点（不足则补零）。

    点数恒为 NFFT=65536，与用户口径一致；只是窗的**起点**从 0 挪到 at，
    这样窗内不含参考的起始渐变，而最长档的回声又仍在窗内。
    """
    y = np.asarray(y, float)
    seg = y[at:at + n]
    if len(seg) < n:
        seg = np.concatenate([seg, np.zeros(n - len(seg))])
    return seg


def hdr(t: str) -> None:
    print(f"\n{'=' * 88}\n{t}\n{'=' * 88}")


def excite(n: int, at: int, kind: str = "burst") -> np.ndarray:
    """激励。

    burst —— 2048 点 Hann 窗白噪猝发。用白噪而不是正弦：逐 bin 口径要求
    **每个 bin 都有能量**，单频只能验一个 bin。用猝发而不是连续噪声：
    回声在时间上分离，便于看清每一次重复，也避免稳态下的相位纠缠。
    """
    x = np.zeros(n, dtype=np.float64)
    rng = np.random.default_rng(20260806)
    b = 2048
    w = np.hanning(b)
    x[at:at + b] = AMP * w * rng.standard_normal(b)
    return x


# ---------------------------------------------------------------- 档位
# 每一档都要覆盖一个**机制**，不是随手取值：
#   * time 扫过 LFO 深度律的零点（0.65）与两侧的极大（0.4 / 0.9）；
#   * feedback 0 验单回声（纯延迟线+滤波器，无环路累积）；
#     1.0 验环路（每圈 0.80，衰到本底要 31 圈）；
#   * lowpass/highpass 各取一个非极端档，验「显示 fc 诚实」这条；
#   * stereo=0 验 Mono 求和路径。
CASES = [
    # 名称                 d_drywet timel  timer  fb    lp    hp   stereo
    ("单回声 400ms fb=0",   1.0, 0.4,   0.4,   0.0,  1.0,  0.0, 1.0),
    ("环路 400ms fb=0.5",   1.0, 0.4,   0.4,   0.5,  1.0,  0.0, 1.0),
    ("环路 400ms fb=1.0",   1.0, 0.4,   0.4,   1.0,  1.0,  0.0, 1.0),
    ("LFO 零点 norm=0.65",  1.0, 0.65,  0.65,  0.5,  1.0,  0.0, 1.0),
    ("LFO 极大 norm=0.9",   1.0, 0.9,   0.9,   0.5,  1.0,  0.0, 1.0),
    ("最短 100ms",          1.0, 0.0,   0.0,   0.5,  1.0,  0.0, 1.0),
    ("最长 1100ms",         1.0, 1.0,   1.0,   0.5,  1.0,  0.0, 1.0),
    ("LP=0.4 (3046Hz)",     1.0, 0.4,   0.4,   0.5,  0.4,  0.0, 1.0),
    ("HP=1.0 (800Hz)",      1.0, 0.4,   0.4,   0.5,  1.0,  1.0, 1.0),
    ("L/R 异步",            1.0, 0.3,   0.55,  0.5,  1.0,  0.0, 1.0),
    ("Mono 模式",           1.0, 0.4,   0.4,   0.5,  1.0,  0.0, 0.0),
    ("干湿 0.5",            0.5, 0.4,   0.4,   0.5,  1.0,  0.0, 1.0),
]

QUICK = {"单回声 400ms fb=0", "环路 400ms fb=1.0", "LFO 零点 norm=0.65"}


def ref_params(c) -> dict:
    _, dw, tl, tr, fb, lp, hp, st = c
    # delay_mode：Mono / Stereo（>0.5 = Stereo，默认 Stereo）
    return {"delay_drywet": dw, "delay_time_l": tl, "delay_time_r": tr,
            "delay_feedback": fb, "delay_lowpass": lp, "delay_highpass": hp,
            "delay_mode": st}


def cand_params(c) -> dict:
    _, dw, tl, tr, fb, lp, hp, st = c
    # 混响 drywet=0 ⇒ 干路增益恒 1（实测），混响成为直通，只测延迟段
    return {"drywet": 0.0, "d_active": 1.0, "d_drywet": dw,
            "d_timel": tl, "d_timer": tr, "d_feedback": fb,
            "d_lowpass": lp, "d_highpass": hp, "d_stereo": st}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seconds", type=float, default=8.0)
    args = ap.parse_args()

    cases = [c for c in CASES if (not args.quick or c[0] in QUICK)]
    # 渲染长度必须装得下 渐变 + 整个 FFT 窗，否则 analysis_window 补零，
    # 等于把窗尾的回声当成静音比。
    n = max(int(args.seconds * SR), RAMP_GUARD + NFFT + SR)
    # 激励位置有**两个**互相拉扯的约束，必须同时满足：
    #
    # 1. 要落在 FFT 窗**内部**。spectrum_err_db 只 FFT 前 65536 样点（1.365 s）。
    #    原先放在 2 s 处，于是两侧的谱都是**静音**的谱，比值失去意义 ——
    #    那是第一版跑出 5950 dB、gain=0.0000 的真正原因，与 DSP 无关。
    #
    # 2. 要**过了参考的起始渐变**。参考在开头约 400 ms 内电平是爬升的：
    #    同参数下 echo1 峰值 at=1000 → 5.55e−06、at=4800 → 3.94e−03、
    #    at=19200 → 4.52e−03（收敛）。原先取 SR//10 = 4800 正压在渐变尾巴上，
    #    于是候选（无渐变）比参考高约 1.1 dB，是个**纯人造**的电平差。
    #
    # 两条约束一起看似把窗口卡死：at 要 ≥ 19200，而 at + D 要 ≤ 65536，
    # 最长档 D=52800 ⇒ at + D = 72000，出窗。
    #
    # 解法不是加长 FFT（那就偏离了用户定的 65536 口径），而是**把分析窗的
    # 起点挪到激励处**：取 y[at : at+65536] 而不是 y[0 : 65536]。
    # 点数仍然是 65536，口径一个字没变，但窗内装的是「过了渐变的信号」
    # 而不是「渐变 + 信号」。at=19200 时最长档 D=52800 稳稳在窗内。
    at = RAMP_GUARD
    x = excite(n, at)

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    hdr("第 1 步：标定 LFO 起相（一个全局标量，不随档位变）")
    print("  做法：扫候选侧 LFO 的起相 d_lfophase，取原始逐 bin 最小。")
    print("  合法性：LFO 确定且锚定渲染起点 ⇒ 相位差是常数。若各档最优 s 不一致，")
    print("  说明机制错了 —— 那种情况下这一步会自己暴露出来，而不是被它掩盖。")

    # 扫的是候选侧 LfoDelayLine 的**起相**（d_lfophase，周期的分数），
    # 不是「把激励整体平移」。后者在猝发靠近缓冲起点时会把回声推出 FFT 窗，
    # 而且改变的是信号本身；前者只动 LFO，信号一个样点都不动。
    period = SR / 1.70186
    probe = cases[0]
    wr = analysis_window(ref.render(x, ref_params(probe))[0], at)

    def err_at(frac: float) -> float:
        p = dict(cand_params(probe))
        p["d_lfophase"] = frac % 1.0
        wc = analysis_window(cand.render(x, p)[0], at)
        # 标定的目标量必须与**判据**同口径（−40 dB 通带门限）。
        # 用默认的 −80 dB 会让阻带的准噪声参与决定最优相位 ——
        # 那部分误差随相位非单调（§14.6），会把最优点推到错的地方。
        return C.spectrum_err_db(wr, wc, NFFT, floor_db=-40.0)[0]

    coarse = [k / 64.0 for k in range(64)]
    print(f"\n  粗扫 64 点（步长 {period/64:.0f} 样点 = 1/64 周期）…")
    cvals = [(s, err_at(s)) for s in coarse]
    s0, e0 = min(cvals, key=lambda t: t[1])
    print(f"  粗扫最优 phase = {s0:.6f}  max={e0:.2f} dB")

    fine = [s0 + d / 64.0 / 16.0 for d in range(-16, 17)]
    fvals = [(s, err_at(s)) for s in fine]
    s1, e1 = min(fvals, key=lambda t: t[1])
    print(f"  细扫最优 phase = {s1:.6f}  max={e1:.2f} dB")

    fine2 = [s1 + d / period for d in range(-16, 17)]
    f2 = [(s, err_at(s)) for s in fine2]
    best_phase, best_err = min(f2, key=lambda t: t[1])
    print(f"  逐样点最优 phase = {best_phase:.6f}  max={best_err:.2f} dB")
    print(f"  （= {best_phase * 360.0:.2f}° = {best_phase * period:.1f} 样点）")

    hdr(f"第 2 步：在 phase={best_phase:.6f} 上逐档对拍（用户原始口径）")
    print("  判据：波形 max|Δ| < 1e-3 且 原始逐 bin ≤ 3 dB")
    print("  ⚠️ 门限必须与地板同口径。§14.6 那张地板表（shift 1 → 0.49 dB）是在")
    print("  **−40 dB 通带门限**下测的；默认的 −80 dB 会把 delay_lowpass 的**阻带**")
    print("  也算进来，那里比的是准噪声（§14.6：阻带 bin 比全谱峰值低 58.9 dB，")
    print("  且误差随位移**非单调** ⇒ 不是失配指纹）。两个门限都报，判据用 −40。\n")

    rows = []
    for c in cases:
        yr = analysis_window(ref.render(x, ref_params(c))[0], at)
        p = dict(cand_params(c))
        p["d_lfophase"] = best_phase % 1.0
        yc = analysis_window(cand.render(x, p)[0], at)

        wmax, wrms, nrmse, lag, gain = C.waveform_diff(yr, yc)
        # 判据口径：−40 dB（与 §14.6 地板表同口径）
        rmax, r99, r95, rmean = C.spectrum_err_db(yr, yc, NFFT, floor_db=-40.0)[:4]
        # 参考量：−80 dB（含阻带，会被准噪声顶高，只作对照）
        r80 = C.spectrum_err_db(yr, yc, NFFT, floor_db=-80.0)[0]
        gmax = C.smoothed_spectrum_err_db(yr, yc, NFFT, sr=SR)[0]
        ok = (wmax < 1e-3) and (rmax <= 3.0)
        rows.append((c[0], wmax, nrmse, rmax, r99, r95, gmax, lag, gain, ok, r80))
        print(f"  {c[0]:22s} 波形 max|Δ|={wmax:.2e} {'✓' if wmax < 1e-3 else '✗'}"
              f"  逐bin@−40={rmax:6.2f} {'✓' if rmax <= 3.0 else '✗'}"
              f"  p99={r99:5.2f} p95={r95:5.2f}  平滑={gmax:5.2f}"
              f"  (@−80={r80:6.2f} lag={lag} gain={gain:.4f})")

    hdr("汇总")
    # 按名字取，不用 r[-1]：加一列参考量就会让 r[-1] 指向那一列
    # （曾经因此把 1/3 报成 3/3 —— r80 恒真）。
    npass = sum(1 for r in rows if r[9])
    print(f"  通过 {npass} / {len(rows)} 档（两项都过才算通过）")
    print(f"  波形 max|Δ| 最差 = {max(r[1] for r in rows):.3e}  (口径 1e-3)")
    print(f"  逐 bin@−40 最差   = {max(r[3] for r in rows):.2f} dB  (口径 3 dB)")
    print(f"  逐 bin@−80 最差   = {max(r[10] for r in rows):.2f} dB  (含阻带，参考量)")
    print(f"  平滑谱  最差     = {max(r[6] for r in rows):.2f} dB  (参考量)")

    worst = max(rows, key=lambda r: r[3])
    print(f"\n  最差档位：{worst[0]}  逐bin {worst[3]:.2f} dB")


if __name__ == "__main__":
    main()
