"""fb=1.0 档的**参考自比地板**：那 21 dB 里有多少是任何实现都达不到的？

## 为什么必须单独测这一档

§14.6 那张地板表是在 **fb=0.5** 上测的（shift 1 样点 → 0.49 dB）。而 fb=1.0
的环路增益 0.80，梳状谱有**深零点**：零点处参考自身电平极低，两个实现哪怕只
差 0.1 样点，零点位置一挪，那个 bin 的比值就能跳几十 dB —— 而绝对误差微乎其微。

所以「fb=1.0 档 21.11 dB」这个数在被当成失配指纹之前，必须先问：**参考与它
自己比，在这一档能到多少？** 口径必须与判据完全一致（−40 dB 门限、同一分析窗、
65536 FFT），唯一的变量是给参考一个微小扰动。

扰动选「激励位置挪 k 样点」：它不改变任何参数，只改变 LFO 与回声序列的相对
相位 —— 正是两个独立实现之间必然残留的那个自由度（我们只能把全局起相标到
±1 样点，标不到 0）。

若地板本身就在 20 dB 量级，那 21.11 dB 就不是失配，判据要么换口径（如混响段
已批准的平滑谱），要么承认这一档的原始逐 bin 口径不可达 —— 但那必须是**量出来
的结论**，不是让步。
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V          # noqa: E402
from plugin_match import nrev_cand as C         # noqa: E402

SR = 48000
AT = 19200          # 过起始渐变（§14.10）
NFFT = 65536
GATE = -40.0        # 与 ab_delay.py 判据同口径


def burst(n: int, at: int, seed: int = 12345, dur: int = 4800) -> np.ndarray:
    """与 ab_delay.py 同源的噪声突发激励。"""
    rng = np.random.default_rng(seed)
    x = np.zeros(n, dtype=np.float64)
    x[at:at + dur] = rng.standard_normal(dur) * 0.02
    return x


def main() -> None:
    rp = {"delay_drywet": 1.0, "delay_time_l": 0.577079952,
          "delay_time_r": 0.577079952, "delay_feedback": 1.0,
          "delay_lowpass": 1.0, "delay_highpass": 0.0, "delay_mode": 1.0}

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    n = AT + NFFT + 8192

    base = np.asarray(ref.render(burst(n, AT), rp)[0], float)[AT:AT + NFFT]

    print(f"\n{'=' * 78}")
    print("参考自比地板 @ fb=1.0（400ms 档，−40 dB 门限，65536 FFT）")
    print(f"{'=' * 78}")
    print("  位移(样点)    原始逐bin max     p99      p95    平滑谱 max")

    for shift in (1, 2, 4, 8, 16, 48, 480):
        y = np.asarray(ref.render(burst(n, AT + shift), rp)[0], float)
        # 把位移补偿掉：比的是「同一段信号」，只有 LFO 相对相位不同。
        seg = y[AT + shift:AT + shift + NFFT]
        r = C.spectrum_err_db(base, seg, nfft=NFFT, floor_db=GATE)
        s = C.smoothed_spectrum_err_db(base, seg, nfft=NFFT)
        print(f"  {shift:8d}    {r[0]:11.2f}  {r[1]:7.2f}  {r[2]:7.2f}"
              f"    {s[0]:8.2f}")

    print(f"\n  对照：候选当前在这一档为 原始逐bin 21.11 / p99 4.62 / p95 1.99"
          f" / 平滑 1.32")
    print("  判读：若地板的原始逐bin 已达 20 dB 量级，则 21.11 不是失配指纹，")
    print("        而 p95/p99 与平滑谱才是这一档的有效判据。")


if __name__ == "__main__":
    main()
