"""参考混响的结构解剖：pre-delay 的作用点、早期反射、衰减律、滤波器。

关键发现（本脚本负责复现与量化）：
  * 起步淡入：插件前 ~0.1–0.2 s 有淡入，裸冲激放在 0.3 s 之后才完整通过
    （不是 gate；gate 阈值参数无关）。所以 IR 激励统一放在 2.0 s。
  * 混响湿声起点固定在 477 样点（9.94 ms），**与 PRE-DELAY 参数无关**。
  * PRE-DELAY 只推迟「后期混响」，早期反射不动。

用法：python3 tools/measure/ref_structure.py [--section all|predelay|decay|filter|early]
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
IMPULSE_AT = int(2.0 * SR)  # 远离起步淡入区
MEAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "measurements")


def ir(r, params, tail_sec=4.0):
    """裸冲激 IR（放在 2 s 处避开淡入），返回 (2, N) float64。"""
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    y = r.render(x, params=params).astype(np.float64)
    return np.ascontiguousarray(y[:, IMPULSE_AT + LATENCY:])


def env(x, w=512):
    return np.sqrt(np.convolve(np.asarray(x, float) ** 2, np.ones(w) / w, "same"))


def db(x, ref=1.0):
    return 20.0 * np.log10(np.maximum(np.abs(x), 1e-300) / ref)


def section_early(r):
    print("=== 早期结构（默认档位，wet-only）===")
    L = ir(r, {"reverb_drywet": 1.0})[0]
    mx = np.abs(L).max()
    nz = np.nonzero(np.abs(L) > mx * 1e-3)[0]
    print(f"  峰值 {mx:.6f} @ {np.argmax(np.abs(L))} 样点")
    print(f"  首个显著样点 {nz[0]} = {nz[0]/SR*1000:.3f} ms")
    print("  前 40 个显著样点是否连续:",
          "连续" if np.all(np.diff(nz[:40]) == 1) else f"跳变 {np.diff(nz[:40])[:10]}")
    # 前 20 ms 的逐样点能量（找离散 tap）
    print("  0–20 ms 每 1 ms 的 absmax:")
    for ms in range(0, 21):
        i0, i1 = int(ms / 1000 * SR), int((ms + 1) / 1000 * SR)
        print(f"    {ms:3d} ms  {np.abs(L[i0:i1]).max():.5f}")


def section_predelay(r):
    print("=== PRE-DELAY 的作用点 ===")
    rows = {}
    for pv in [0.0, 0.5, 1.0]:
        rows[pv] = ir(r, {"reverb_drywet": 1.0, "reverb_predelay": pv})[0]
    gp = max(np.abs(v).max() for v in rows.values())

    print("  包络 dB（相对全局峰值），每 20 ms：")
    print("    ms    pv=0.0   pv=0.5   pv=1.0")
    envs = {pv: env(v) for pv, v in rows.items()}
    for ms in range(0, 401, 20):
        i = int(ms / 1000 * SR)
        vals = [db(envs[pv][i] if i < len(envs[pv]) else 0.0, gp) for pv in (0.0, 0.5, 1.0)]
        print(f"    {ms:4d}  {vals[0]:+7.2f}  {vals[1]:+7.2f}  {vals[2]:+7.2f}")

    # 起点是否随参数移动
    print("\n  各档 onset / t10（累积能量 10% 到达点）：")
    for pv in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]:
        L = rows.get(pv)
        if L is None:
            L = ir(r, {"reverb_drywet": 1.0, "reverb_predelay": pv})[0]
        mx = np.abs(L).max()
        nz = np.nonzero(np.abs(L) > mx * 1e-3)[0]
        seg = L[:int(0.6 * SR)] ** 2
        c = np.cumsum(seg)
        c /= max(c[-1], 1e-30)
        t10 = int(np.argmax(c > 0.1))
        cen = float(np.sum(np.arange(len(seg)) * seg) / max(np.sum(seg), 1e-30))
        print(f"    pv={pv:.1f} 参数={V.predelay_ms(pv):6.2f} ms | onset={nz[0]:5d}"
              f" ({nz[0]/SR*1000:5.2f} ms)  t10={t10/SR*1000:6.2f} ms"
              f"  centroid={cen/SR*1000:6.2f} ms")

    # 差分：pv=1 − pv=0 的起点，判断「被推迟的成分」何时开始
    d = rows[1.0][:int(0.8 * SR)] - rows[0.0][:int(0.8 * SR)]
    nzd = np.nonzero(np.abs(d) > np.abs(d).max() * 1e-3)[0]
    print(f"\n  (pv=1 − pv=0) 差分起点 = {nzd[0]} 样点 ({nzd[0]/SR*1000:.2f} ms)"
          f"，说明 0–{nzd[0]/SR*1000:.1f} ms 的成分与 PRE-DELAY 无关")


def section_decay(r):
    print("=== DECAY 与衰减律 ===")
    print("   norm  参数(s)   T60_meas(s)   EDT(s)   斜率(dB/s)")
    out = []
    for dv in [0.0, 0.25, 0.5, 0.75, 1.0]:
        tail = 12.0 if dv > 0.6 else 8.0
        L = ir(r, {"reverb_drywet": 1.0, "reverb_decay": dv}, tail_sec=tail)[0]
        e = env(L, 2048)
        pk = e.max()
        edb = db(e, pk)
        # 线性回归斜率：取 -5 dB .. -35 dB 区间
        idx = np.nonzero((edb < -5) & (edb > -35))[0]
        if len(idx) > 100:
            t = idx / SR
            A = np.vstack([t, np.ones_like(t)]).T
            slope, _ = np.linalg.lstsq(A, edb[idx], rcond=None)[0]
            t60 = -60.0 / slope
        else:
            slope, t60 = float("nan"), float("nan")
        below = np.nonzero(edb < -60)[0]
        t60m = below[0] / SR if len(below) else float("nan")
        out.append((dv, V.decay_seconds(dv), t60m, t60, slope))
        print(f"   {dv:.2f}  {V.decay_seconds(dv):6.2f}   {t60m:9.3f}   {t60:7.3f}  {slope:9.2f}")
    return out


def section_filter(r):
    print("=== LOW CUT / HIGH CUT 的实际滤波器 ===")
    nfft = 1 << 16

    def spec(params):
        L = ir(r, params, tail_sec=6.0)[0]
        n = min(len(L), nfft)
        seg = np.zeros(nfft)
        seg[:n] = L[:n]
        return np.abs(np.fft.rfft(seg))

    f = np.fft.rfftfreq(nfft, 1.0 / SR)

    def smooth_db(S, oct_frac=1 / 12):
        """1/12 倍频程平滑（混响 IR 频谱本身梳状，必须平滑才能看出滤波器形状）"""
        out = np.zeros_like(S)
        for i in range(len(S)):
            if f[i] <= 0:
                out[i] = S[i]
                continue
            lo, hi = f[i] * 2 ** -oct_frac, f[i] * 2 ** oct_frac
            m = (f >= lo) & (f <= hi)
            out[i] = np.sqrt(np.mean(S[m] ** 2)) if m.any() else S[i]
        return 20 * np.log10(np.maximum(out, 1e-30))

    base = smooth_db(spec({"reverb_drywet": 1.0, "reverb_lowcut": 0.0, "reverb_highcut": 1.0}))

    print("\n  -- LOW CUT：相对 lowcut=0 的差值 --")
    for lv in [0.25, 0.5, 1.0]:
        cur = smooth_db(spec({"reverb_drywet": 1.0, "reverb_lowcut": lv, "reverb_highcut": 1.0}))
        d = cur - base
        fc = V.lowcut_hz(lv)
        print(f"   lowcut={lv:.2f} (fc={fc:.0f} Hz):", end="")
        for fq in [20, 50, 100, 200, 400, 800, 1600]:
            i = int(fq / SR * nfft)
            print(f"  {fq}Hz:{d[i]:+.1f}", end="")
        print()

    print("\n  -- HIGH CUT：相对 highcut=1 的差值 --")
    for hv in [0.0, 0.5]:
        cur = smooth_db(spec({"reverb_drywet": 1.0, "reverb_lowcut": 0.0, "reverb_highcut": hv}))
        d = cur - base
        fc = V.highcut_hz(hv)
        print(f"   highcut={hv:.2f} (fc={fc:.0f} Hz):", end="")
        for fq in [500, 1000, 2000, 4000, 8000, 12000, 16000]:
            i = int(fq / SR * nfft)
            print(f"  {fq}Hz:{d[i]:+.1f}", end="")
        print()

    print("\n  -- 默认档位湿声绝对频响（1/12 oct 平滑，相对最大值）--")
    mx = base.max()
    for fq in [20, 30, 50, 80, 125, 200, 315, 500, 800, 1250, 2000,
               3150, 5000, 8000, 10000, 12500, 16000, 20000]:
        i = int(fq / SR * nfft)
        print(f"   {fq:6d} Hz  {base[i]-mx:+7.2f} dB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--section", default="all",
                    choices=["all", "early", "predelay", "decay", "filter"])
    args = ap.parse_args()
    r = V.Vst3RefRenderer(sr=SR, block=512)

    if args.section in ("all", "early"):
        section_early(r)
        print()
    if args.section in ("all", "predelay"):
        section_predelay(r)
        print()
    if args.section in ("all", "decay"):
        section_decay(r)
        print()
    if args.section in ("all", "filter"):
        section_filter(r)


if __name__ == "__main__":
    main()
