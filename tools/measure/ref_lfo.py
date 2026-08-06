"""内部 LFO 调制的测量：频率、深度、波形、作用位置。

已确证（ref_shift_invariance.py）：系统线性但时变，成因是被 LFO 调制的延迟线。
时变是**确定性的**（重复渲染 max|Δ|=0），且相位与处理起点绑定。

测法：
  A) **单抽头相位追踪**。给一个纯冲激，取 IR 中某个孤立的早期反射簇，
     随着激励位置右移，该簇的**到达时刻**会随 LFO 相位来回摆动。
     把「簇质心 vs 激励位移」画出来，就是 LFO 的波形（周期 → 频率，
     峰峰值 → 深度）。这是最直接、不需要任何模型假设的测法。

  B) **稳态正弦的调频边带**。喂长正弦，看输出频谱在 f₀ 附近的边带间隔
     = LFO 频率；边带幅度比 → 调制指数 → 深度。
     混响的密集响应会糊掉边带，故用 DECAY 最小档 + 高 Q 分析。

  C) **相关性回落曲线**。ref_shift_invariance 已测得 nrmse 随位移单调上升
     并在 ~10 ms 饱和，没有周期性回落 → 说明多条延迟线各自用**不同相位/频率**
     的 LFO（若只有一个共同 LFO，nrmse 会在周期处回落）。本节做更细的扫描确认。

用法：python3 tools/measure/ref_lfo.py [--section a|b|c|all]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
LATENCY = 51
BASE_AT = int(2.0 * SR)

# DECAY 最小 + PRE-DELAY 最大：尾巴最短、第二路推远，早期簇最干净
ISO = {"reverb_drywet": 1.0, "reverb_predelay": 1.0, "reverb_decay": 0.0}


def ir_at(r, at, tail_sec=1.0, params=None):
    n = at + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[at] = 1.0
    return r.render(x, params=params or ISO).astype(np.float64)[0][at + LATENCY:]


def cluster_centroid(y, lo, hi):
    """[lo,hi) 窗内的能量质心（样点，可为小数）——即该反射簇的到达时刻。"""
    seg = y[lo:hi] ** 2
    s = seg.sum()
    if s <= 0:
        return float("nan")
    return lo + float(np.sum(np.arange(len(seg)) * seg) / s)


def section_a(r):
    print("=== A) 单抽头相位追踪：早期簇质心 vs 激励位移 ===")
    base = ir_at(r, BASE_AT)
    # 第一簇：实测 477–656 样点（REFERENCE §9）
    lo, hi = 470, 660
    print(f"  追踪窗 [{lo}, {hi}) 样点（第一个早期反射簇）")
    print("   位移(样点)  位移(ms)   簇质心(样点)   相对基准(样点)")
    c0 = cluster_centroid(base, lo, hi)
    rows = []
    # 以 2 样点步长扫 0–480 样点（0–10 ms）：足够看出 LFO 的一个周期
    for sh in range(0, 481, 10):
        y = ir_at(r, BASE_AT + sh)
        c = cluster_centroid(y, lo, hi)
        rows.append((sh, c - c0))
        print(f"   {sh:8d}  {sh/SR*1000:8.3f}   {c:12.4f}   {c-c0:+12.4f}")

    d = np.array([v for _, v in rows])
    print(f"\n  峰峰摆动 = {np.nanmax(d)-np.nanmin(d):.4f} 样点"
          f"（{(np.nanmax(d)-np.nanmin(d))/SR*1e6:.1f} µs）")
    print("  若摆动明显且呈周期性 → LFO 频率 = 1/周期，深度 = 峰峰/2")
    return rows


def section_b(r):
    print("\n=== B) 稳态正弦的调频边带 ===")
    f0 = 1000.0
    dur = 4.0
    n = BASE_AT + int(dur * SR)
    t = np.arange(int(dur * SR)) / SR
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT:] = (0.2 * np.sin(2 * np.pi * f0 * t)).astype(np.float32)
    y = r.render(x, params=ISO).astype(np.float64)[0][BASE_AT + LATENCY:]
    seg = y[int(1.0 * SR):int(3.0 * SR)]   # 取稳态段
    nfft = 1 << 18
    w = np.hanning(len(seg))
    S = np.abs(np.fft.rfft(seg * w, nfft))
    f = np.fft.rfftfreq(nfft, 1.0 / SR)
    k0 = int(round(f0 / SR * nfft))
    band = 60.0   # 看 f0 ± 60 Hz
    lo = int((f0 - band) / SR * nfft)
    hi = int((f0 + band) / SR * nfft)
    sub = S[lo:hi]
    fs = f[lo:hi]
    peak = sub.max()
    print(f"  f0={f0:.0f} Hz 附近 ±{band:.0f} Hz 的显著谱峰（相对主峰 dB）：")
    # 找局部极大
    found = []
    for i in range(2, len(sub) - 2):
        if sub[i] > sub[i-1] and sub[i] > sub[i+1] and sub[i] > peak * 10 ** (-45 / 20):
            found.append((fs[i], 20 * np.log10(sub[i] / peak)))
    # 去掉挨得太近的
    keep = []
    for fq, dbv in sorted(found, key=lambda z: -z[1]):
        if all(abs(fq - k[0]) > 1.0 for k in keep):
            keep.append((fq, dbv))
        if len(keep) >= 12:
            break
    for fq, dbv in sorted(keep):
        print(f"    {fq:9.3f} Hz   {dbv:+7.2f} dB   (Δf0 = {fq-f0:+8.3f} Hz)")
    if len(keep) >= 2:
        offs = sorted(abs(fq - f0) for fq, _ in keep if abs(fq - f0) > 0.3)
        if offs:
            print(f"\n  最小非零边带间隔 = {offs[0]:.3f} Hz → LFO 频率候选")


def section_c(r):
    print("\n=== C) 细扫相关性回落（判断单 LFO 还是多 LFO）===")
    base = ir_at(r, BASE_AT, tail_sec=1.0)
    print("   位移(ms)   nrmse")
    for ms in [0.0208, 0.0417, 0.0833, 0.167, 0.333, 0.667, 1.0, 1.5, 2.0,
               3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0]:
        sh = int(round(ms / 1000 * SR))
        y = ir_at(r, BASE_AT + sh, tail_sec=1.0)
        m = min(len(base), len(y))
        a, b = base[:m], y[:m]
        g = float(np.dot(a, b) / max(np.dot(b, b), 1e-30))
        rel = float(np.sqrt(np.mean((a - g * b) ** 2)) / max(np.sqrt(np.mean(a ** 2)), 1e-30))
        print(f"   {ms:8.4f}  {rel*100:7.3f}%")
    print("  单一 LFO → nrmse 应在周期处回落；单调饱和 → 多条不同相位/频率的 LFO")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default="all", choices=["a", "b", "c", "all"])
    args = ap.parse_args()
    r = V.Vst3RefRenderer(sr=SR, block=512)
    if args.section in ("a", "all"):
        section_a(r)
    if args.section in ("b", "all"):
        section_b(r)
    if args.section in ("c", "all"):
        section_c(r)


if __name__ == "__main__":
    main()
