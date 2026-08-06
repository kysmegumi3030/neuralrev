"""echo1 的**缺失级**：直接把 参考/候选 的比值反卷积出来，不猜拓扑。

## 为什么是这个测法

`ref_delay_onset.py` 给出三条读数，它们**互相矛盾**如果只假设「少了一段纯延迟」：

  * 抛物线峰位差 −4.53（LFO 零点档）/ −4.40（D=4800 档）⇒ 常数，与 D 无关
  * 但 onset（首个 >峰值1e−6）差 **−6**，与峰位差 −4.5 **不相等**
  * 且候选 echo1 峰值**高 3…6%**（比 1.0339 / 1.0597）

纯延迟会整体平移而**不改变形状**，于是 onset 差必须等于峰位差、峰值必须相等。
三条都不满足 ⇒ 缺的不是延迟，是一个**有形状的级**（它同时给群延迟与展宽）。

所以不要再猜「是不是 16 应该改成 21」。fb=0 时 echo1 就是整条湿路径的冲激
响应，两侧之比 **就是那个缺失级的频响**，一次反卷积直读：

    H_missing(f) = FFT(ref_echo1) / FFT(cand_echo1)

## 口径

fb=0（只有一次回声，不与后续圈重叠）、amp=1e−2（线性区，§14.4）、
at=19200（过起始渐变，§14.10）、两侧同一 LFO 起相。
在 **LFO 零点档 norm=0.65** 上做主测（echo1 位置与 LFO 相位无关，
见 ref_delay_onset.py 的口径说明），D=4800 整数档作对照。

窗口对两侧取**同一段绝对样点**（不各自对齐）—— 对齐会把要测的群延迟吃掉。
只在参考 echo1 有能量的带内报（低频端被 20 Hz HP 压到噪声、高频端被 16 kHz LP
压到噪声，那两处的比值是 0/0）。
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
AT = 19200
AMP = 1e-2
LFO_PHASE = 0.238423
NFFT = 4096         # echo1 是短瞬态，4096 足够且不引入长静音段
WIN = 1024          # 截窗长度：覆盖 HP 的长尾（20 Hz 时间常数约 380 样点）


def render_pair(norm: float):
    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    rp = {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
          "delay_feedback": 0.0, "delay_lowpass": 1.0, "delay_highpass": 0.0,
          "delay_mode": 1.0}
    cp = {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
          "d_timel": norm, "d_timer": norm, "d_feedback": 0.0,
          "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
          "d_lfophase": LFO_PHASE}

    n = AT + 200000
    x = np.zeros(n, dtype=np.float64)
    x[AT] = AMP
    yr = np.asarray(ref.render(x, rp)[0], float)
    yc = np.asarray(cand.render(x, cp)[0], float)
    return yr, yc


def run(norm: float, label: str) -> None:
    yr, yc = render_pair(norm)

    # 窗起点：两侧**同一段绝对样点**。取参考 echo1 粗位置往前留 40 样点余量，
    # 足够把候选（早约 4.5）也整个装进同一个窗。
    pr = int(np.argmax(np.abs(yr[AT + 64:]))) + AT + 64
    a0 = pr - 40
    sr_seg = yr[a0:a0 + WIN]
    sc_seg = yc[a0:a0 + WIN]

    R = np.fft.rfft(sr_seg, NFFT)
    Cc = np.fft.rfft(sc_seg, NFFT)
    f = np.fft.rfftfreq(NFFT, 1.0 / SR)

    # 只在参考有能量的带内报：低频被 20 Hz HP 压掉、高频被 16 kHz LP 压掉，
    # 那两端的比值是 0/0，报出来只是噪声。
    mag = np.abs(R)
    keep = mag > mag.max() * 10 ** (-40.0 / 20.0)

    H = np.where(keep, Cc / np.where(np.abs(R) > 0, R, 1.0), np.nan)
    # 注意方向：H = cand/ref。**候选缺的那一级** = 1/H = ref/cand。
    Hm = np.where(keep, R / np.where(np.abs(Cc) > 0, Cc, 1.0), np.nan)

    print(f"\n{'=' * 78}")
    print(f"{label}（norm={norm}, fb=0）")
    print(f"{'=' * 78}")
    print("  缺失级 = 参考/候选 的频响（幅度 dB / 群延迟 样点）")
    print("    f(Hz)      |H|dB     相位(rad)   累计群延迟(样点)")

    ph = np.unwrap(np.angle(np.where(keep, Hm, 1.0)))
    for hz in (50, 100, 200, 500, 1000, 2000, 4000, 8000, 12000, 16000):
        i = int(np.argmin(np.abs(f - hz)))
        if not keep[i]:
            print(f"    {hz:7d}   ← 带外（参考在此低于 −40 dB），跳过")
            continue
        # 局部群延迟：−dφ/dω，用中心差分（样点数 = −dφ/dω · SR/2π）
        j = min(max(i, 1), len(ph) - 2)
        dphi = ph[j + 1] - ph[j - 1]
        dw = 2.0 * np.pi * (f[j + 1] - f[j - 1]) / SR
        gd = -dphi / dw
        print(f"    {hz:7d}  {20*np.log10(abs(Hm[i])):+8.3f}  {ph[i]:+10.3f}"
              f"  {gd:+14.2f}")

    # 时域看这一级像什么：反卷积核（只在带内，带外补 0 ⇒ 等价于带限）
    Hfull = np.where(keep, np.nan_to_num(Hm), 0.0)
    h = np.fft.irfft(Hfull, NFFT)
    h = np.concatenate([h[-64:], h[:192]])       # 环绕：负时间在末尾
    pk = int(np.argmax(np.abs(h)))
    print(f"\n  反卷积核（带限）：峰在偏移 {pk - 64:+d} 样点，峰值 {h[pk]:+.4f}")
    e = h ** 2
    print(f"    能量重心 {np.dot(np.arange(len(e)), e) / max(e.sum(), 1e-300) - 64:+.2f} 样点")
    print(f"    前 12 个非零抽头（从 {pk-64-2:+d} 起）：")
    print("     ", np.array2string(h[max(pk - 2, 0):pk + 10], precision=4))
    print("\n  判读：若这一级是**纯延迟**，|H|dB 应全带 ≈0 且群延迟为常数；"
          "\n        若 |H|dB 随频率下垂、群延迟随频率变化 ⇒ 缺的是**滤波器**。")


def main() -> None:
    run(0.65, "缺失级 @ LFO 零点档（主测：位置与 LFO 相位无关）")
    run(0.0, "缺失级 @ 整数档 D=4800（对照）")


if __name__ == "__main__":
    main()
