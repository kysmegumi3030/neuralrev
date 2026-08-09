"""两个仪器读数不一致：4.62 dB（验收）vs 2.14 dB（诊断）—— 差在哪个自由度上？

## 为什么这必须先查清，不能先去改搁架

加了 −40 dB 门之后，12 档里有 10 档大幅改善，0.9 档也按预测从 3.82 落到 3.14。
但 **1100 ms 一动没动**（4.62 → 4.62），而我用独立脚本在**同样的半宽、同样的
−40 dB 门**下量同一档，读到的是 **2.14 dB**。

两个数不可能都对。在这个矛盾解决之前，4.62 不能用来判断任何事情 ——
更不能拿它去调搁架：§14.14.4i 已经记过「拟合到某一次渲染的抽样」这个坑，
而 §14.14.8 结论 3 明确写了不要为压这个数去动 `kMeasLoopFirTaps`。

## 已经排掉的一个嫌疑：平滑半宽

`nrev_cand.smoothed_spectrum_err_db` 的 `oct_frac=1/12` 是**半宽**
（带 = [f·2^−1/12, f·2^+1/12]，合计 **1/6 oct**）；
本目录各 measure 工具用 `r = 2**(0.5/12)`（合计 **1/12 oct**）。
即验收工具平滑得**更宽**，而更宽只会让误差**更小**。
⇒ 半宽差异无法解释「验收读得更高」，方向是反的。排除。

剩下两个自由度：

1. **激励**：验收用 2048 点 **Hann 窗**猝发（seed 20260806）；
   诊断用 4800 点**矩形**猝发（seed 12345）。
2. **LFO 起相**：验收**自己扫**一个 `best_phase`（在 0.4 档上扫的）；
   诊断固定用 0.238423。

## 测法：2×2 交叉表，度量函数一律调用验收侧那一个

关键纪律是**不要重写度量**。两个工具的差异如果出在度量实现上，
我再写第三份实现只会得到第三个数。所以这里直接 import
`nrev_cand.smoothed_spectrum_err_db`，四格只改激励与相位，
度量代码逐字相同 —— 于是差值只能归给这两个自由度。

同时报**该档参考自比的地板**（同一格、同一激励、同一相位，激励偏移 1 样点），
因为「哪个激励给出的读数更高」本身不是判据：若某个激励让**地板**也一起抬高，
那它抬的是测量本底，不是失配。只有「读数高而地板不高」才是真实差距。

用法：
    python3 tools/measure/ref_delay_caliber_gap.py
    python3 tools/measure/ref_delay_caliber_gap.py --norms 1.00
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
NFFT = 65536
AMP = 1e-3
GATE_DB = -40.0

# 验收侧的激励参数（与 ab_delay.excite 逐字一致）
AB_SEED, AB_LEN = 20260806, 2048
# 诊断侧的激励参数（与 ref_delay_smoothed_gap / longsetting_floor 一致）
DIAG_SEED, DIAG_LEN = 12345, 4800

DIAG_PHASE = 0.238423

T_MIN_MS, T_MAX_MS, T_EXP = 100.0, 1100.0, 5.0 / 3.0


def time_ms(norm: float) -> float:
    return T_MIN_MS + (T_MAX_MS - T_MIN_MS) * norm ** T_EXP


def excite_ab(n: int, at: int) -> np.ndarray:
    """验收侧：2048 点 Hann 窗猝发。"""
    x = np.zeros(n)
    rng = np.random.default_rng(AB_SEED)
    x[at:at + AB_LEN] = AMP * np.hanning(AB_LEN) * rng.standard_normal(AB_LEN)
    return x


def excite_diag(n: int, at: int) -> np.ndarray:
    """诊断侧：4800 点矩形猝发。"""
    x = np.zeros(n)
    rng = np.random.default_rng(DIAG_SEED)
    x[at:at + DIAG_LEN] = rng.standard_normal(DIAG_LEN) * AMP
    return x


def _burst(n: int, at: int, seed: int, length: int, hann: bool) -> np.ndarray:
    x = np.zeros(n)
    rng = np.random.default_rng(seed)
    w = np.hanning(length) if hann else 1.0
    x[at:at + length] = AMP * w * rng.standard_normal(length)
    return x


def excite_hann_long(n: int, at: int) -> np.ndarray:
    """判别格：Hann 窗但用诊断侧的长度。"""
    return _burst(n, at, AB_SEED, DIAG_LEN, True)


def excite_rect_short(n: int, at: int) -> np.ndarray:
    """判别格：矩形但用验收侧的长度。"""
    return _burst(n, at, DIAG_SEED, AB_LEN, False)


# 2×2（长度 × 窗形）—— 用来判 3.44 dB 的地板是**长度**造成的还是**窗形**造成的。
#
# 若是长度：猝发越短，谱的相关宽度越宽（≈SR/L），一个平滑带内的**独立**样本
# 就越少，带内 RMS 的估计方差越大 ⇒ 地板抬高。2048 点 ⇒ 相关宽度 23.4 Hz
# ≈32 个 bin；4800 点 ⇒ 10 Hz ≈13.6 个 bin。差 2.3 倍自由度。
# 若是窗形：Hann 会把两侧 taper 掉，等效长度只有约 L/2，同样减自由度，
# 但幅度应当比「长度差 2.34 倍」小。
EXCITERS = (("Hann2048(验收)", excite_ab), ("矩形4800(诊断)", excite_diag),
            ("Hann4800", excite_hann_long), ("矩形2048", excite_rect_short))


def analysis_window(y, at: int, n: int = NFFT) -> np.ndarray:
    y = np.asarray(y, float)
    seg = y[at:at + n]
    if len(seg) < n:
        seg = np.concatenate([seg, np.zeros(n - len(seg))])
    return seg


def ref_params(norm: float, fb: float) -> dict:
    return {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
            "delay_feedback": fb, "delay_lowpass": 1.0, "delay_highpass": 0.0,
            "delay_mode": 1.0}


def cand_params(norm: float, fb: float, phase: float) -> dict:
    return {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
            "d_timel": norm, "d_timer": norm, "d_feedback": fb,
            "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
            "d_lfophase": phase}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--norms", type=float, nargs="*", default=[1.00, 0.90, 0.65])
    ap.add_argument("--fb", type=float, default=0.5)
    ap.add_argument("--phases", type=float, nargs="*",
                    default=[DIAG_PHASE, 0.0])
    args = ap.parse_args()

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)
    n = AT + NFFT + SR

    print("两个仪器读数不一致的归因：只改激励与 LFO 起相，**度量函数逐字相同**")
    print("（直接 import nrev_cand.smoothed_spectrum_err_db，floor_db=-40）")
    print("地板 = 同格同激励同相位、参考自比（激励偏移 1 样点）\n")
    print("平滑半宽已排除：验收 oct_frac=1/12 是半宽(合计1/6oct)，诊断合计1/12oct，")
    print("验收平滑**更宽** ⇒ 只会让误差更小，方向与「验收读得更高」相反。\n")

    for nt in args.norms:
        print("=== norm=%.4f  (%.1f ms) ===" % (nt, time_ms(nt)))
        print("  %-18s %-10s %10s %10s %10s" %
              ("激励", "相位", "平滑最差", "地板", "原始最差"))
        for ename, efn in EXCITERS:
            x = efn(n, AT)
            yr = analysis_window(ref.render(x, ref_params(nt, args.fb))[0], AT)
            # 地板：同一激励偏移 1 样点，分析窗同步跟着挪
            x1 = efn(n, AT + 1)
            yr1 = analysis_window(
                ref.render(x1, ref_params(nt, args.fb))[0], AT + 1)
            floor = C.smoothed_spectrum_err_db(
                yr, yr1, NFFT, sr=SR, floor_db=GATE_DB)[0]
            for ph in args.phases:
                yc = analysis_window(
                    cand.render(x, cand_params(nt, args.fb, ph))[0], AT)
                gmax = C.smoothed_spectrum_err_db(
                    yr, yc, NFFT, sr=SR, floor_db=GATE_DB)[0]
                rmax = C.spectrum_err_db(yr, yc, NFFT, floor_db=GATE_DB)[0]
                print("  %-18s %-10.6f %10.2f %10.2f %10.2f"
                      % (ename, ph, gmax, floor, rmax))
        print()

    print("判读：")
    print("  * 只有相位那一列变 ⇒ 差异是 ab_delay 扫出的 best_phase 不适用于该档；")
    print("  * 只有激励那一行变，且**地板同步抬高** ⇒ 是该激励的测量本底，不是失配；")
    print("  * 只有激励那一行变，而地板不动 ⇒ 该激励确实激出了真实差距")
    print("    （Hann 窗猝发的谱是集中的，矩形猝发在 HF 放的能量分布不同）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
