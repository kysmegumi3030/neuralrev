"""参考插件的「自一致性下界」——验收口径能取到多严，由这把尺子决定。

背景：混响已确证为**线性时变**（ref_shift_invariance.py）：
把同一个冲激挪 1 ms，响应就变了（max|Δ| = 4.8e-3，nrmse 9.2%）；
挪 10 ms 以上 nrmse 饱和在 ~82%。而重复渲染完全确定性（max|Δ| = 0），
所以时变是**确定性的**，成因是内部 LFO 调制的延迟线（plate/spring 混响的常规做法，
用来打散金属味），其相位与处理起点绑定。

这把尺子怎么用：
  拿**参考插件自己**跟**参考插件自己**比 —— 只把激励位置挪一点点。
  两者是同一个插件、同一档参数、同一次配置，差别只有「激励落在 LFO 的哪个相位」。
  这个差值就是**任何实现都无法低于的下界**：除非把 LFO 的波形、频率、
  以及**初相**都精确复现，而初相在黑箱测量下无法唯一确定。

若下界已经高于用户设定的 1e-3，那么 1e-3 不是「难」，而是**原理上不可达**，
需要换一个口径（本脚本同时给出在各口径下的实测数字，供重新设定验收标准）。

用法：python3 tools/measure/ref_self_consistency.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V                        # noqa: E402
from plugin_match.nrev_cand import waveform_diff, spectrum_err_db  # noqa: E402

SR = 48000
LATENCY = 51
BASE_AT = int(2.0 * SR)
PARAMS = {"reverb_drywet": 1.0, "reverb_predelay": 0.5, "reverb_decay": 0.5}


def ir_at(r, at, tail_sec=4.0):
    n = at + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[at] = 1.0
    return r.render(x, params=PARAMS).astype(np.float64)[0][at + LATENCY:]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    base = ir_at(r, BASE_AT)
    print(f"基准 IR：冲激 @ 2.000 s，峰值 {np.abs(base).max():.6e}\n")

    print("参考 vs 参考自身（仅激励位置不同）—— 这就是精度下界：")
    print(f"  {'激励位移':>10s}  {'波形 max|Δ|':>13s}  {'nrmse':>8s}"
          f"  {'频谱 max':>9s}  {'p99':>7s}  {'p95':>7s}")
    rows = []
    for shift in [1, 2, 4, 8, 16, 48, 480, 4800]:
        y = ir_at(r, BASE_AT + shift)
        wmax, wrms, nrmse, lag, gain = waveform_diff(base, y)
        smax, s99, s95, smean, nb, tb = spectrum_err_db(base, y, nfft=65536)
        rows.append((shift, wmax, nrmse, smax, s99, s95))
        print(f"  {shift:6d} 样点  {wmax:13.3e}  {nrmse*100:7.2f}%"
              f"  {smax:8.2f}dB  {s99:6.2f}  {s95:6.2f}")

    # 用户口径
    wmin = min(r_[1] for r_ in rows)
    smin = min(r_[3] for r_ in rows)
    s99min = min(r_[5] for r_ in rows)
    print(f"\n下界（取全部位移中最好的一档）：")
    print(f"  波形 max|Δ| ≥ {wmin:.3e}   （用户口径 1e-3）")
    print(f"  频谱 max    ≥ {smin:.2f} dB  （用户口径 3 dB）")
    print(f"  频谱 p95    ≥ {s99min:.2f} dB")

    print("\n结论：")
    if wmin > 1e-3:
        print(f"  * 参考插件与**自己**比都做不到 1e-3（最好情况 {wmin:.3e}，"
              f"超出 {wmin/1e-3:.1f} 倍）。")
        print("    逐样点 1e-3 对任何独立实现都不可达 —— 除非复现 LFO 初相。")
    else:
        print(f"  * 1 样点位移下波形误差 {wmin:.3e}，仍在 1e-3 内，口径可保留。")
    if smin > 3.0:
        print(f"  * 逐 bin 3 dB 同理不可达（自比最好 {smin:.2f} dB）。")

    # 给出可行的替代口径
    print("\n在时变系统上仍然可严格量化的口径（本脚本已同时给出）：")
    print("  a) **同起点**对拍：候选与参考都从同一样点起激励，比 IR 的统计量")
    print("     （EDC 斜率、1/3 oct 频响、密度演化、立体声相关性）")
    print("  b) 频谱的**平滑后**逐 bin 误差（1/6 或 1/12 oct RMS 平滑），")
    print("     它对 LFO 相位不敏感，仍能严格约束音色")
    print("  c) 多位移**集合平均**的频谱误差（把 LFO 相位当随机变量做期望）")


if __name__ == "__main__":
    main()
