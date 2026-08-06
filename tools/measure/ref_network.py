"""湿声网络的结构反演：延迟线长度、扩散级数、反馈拓扑。

思路：混响网络的 IR 在**自相关**与**倒谱**里会暴露其延迟线长度
（每条延迟线在自相关上产生一个峰，梳状/allpass 级联产生倍数峰）。
再配合「早期簇的时间位置」（9–10 / 13–14 / 17–20 ms，见 §3）交叉验证。

具体测法：
  A) 早期区（前 30 ms）的逐样点结构：找离散抽头/簇的起点
  B) IR 自相关：找主延迟周期（用 DECAY 最小档，尾巴短、簇最清晰）
  C) 倒谱（real cepstrum）：延迟线长度在倒谱上是尖峰
  D) 稀疏度随时间：早期是否稀疏（离散反射）→ 何时转入密集（后期扩散）
  E) 用 DECAY 极小档隔离「单圈」：反馈很低时 IR ≈ 前向部分，
     可直接读出扩散网络的抽头图

用法：python3 tools/measure/ref_network.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
LATENCY = 51
IMPULSE_AT = int(2.0 * SR)


def ir(r, params, tail_sec=6.0):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[:, IMPULSE_AT + LATENCY:]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    # 用最小 DECAY + pre-delay 最大（把第二路推远，前 200 ms 只剩第一路）
    iso = {"reverb_drywet": 1.0, "reverb_predelay": 1.0, "reverb_decay": 0.0}
    L, R = ir(r, iso, tail_sec=3.0)

    # ---- A) 早期区逐样点：找簇边界 ----
    print("=== A) 早期区（前 30 ms）簇结构：连续非零段 ----")
    thr = np.abs(L).max() * 1e-3
    early = L[:int(0.030 * SR)]
    on = np.abs(early) > thr
    runs, i = [], 0
    while i < len(on):
        if on[i]:
            j = i
            while j < len(on) and on[j]:
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    for a, b in runs[:24]:
        print(f"    [{a:5d}, {b:5d})  {a/SR*1000:7.3f} – {b/SR*1000:7.3f} ms"
              f"  长度 {b-a:4d}  峰值 {np.abs(L[a:b]).max():.5f}")

    # ---- B) 自相关主周期 ----
    print("\n=== B) IR 自相关的显著峰（前 100 ms 的 lag） ===")
    seg = L[:int(0.5 * SR)]
    ac = np.correlate(seg, seg, "full")[len(seg) - 1:]
    ac = ac / max(ac[0], 1e-30)
    lo = 20
    win = ac[lo:int(0.1 * SR)]
    # 取局部极大且高于阈值
    peaks = []
    for i in range(1, len(win) - 1):
        if win[i] > win[i - 1] and win[i] > win[i + 1] and win[i] > 0.10:
            peaks.append((lo + i, win[i]))
    peaks.sort(key=lambda t: -t[1])
    for lag, h in peaks[:15]:
        print(f"    lag {lag:5d} ({lag/SR*1000:7.3f} ms)  高度 {h:+.4f}")

    # ---- C) 倒谱 ----
    print("\n=== C) 实倒谱的显著峰（延迟线长度的指纹） ===")
    n = 1 << 15
    s = np.zeros(n)
    s[:min(len(L), n)] = L[:min(len(L), n)]
    S = np.abs(np.fft.rfft(s))
    ceps = np.fft.irfft(np.log(np.maximum(S, 1e-12)))
    c = ceps[:int(0.05 * SR)]
    idx = np.argsort(-np.abs(c[10:])) + 10
    seen = []
    for i in idx:
        if all(abs(i - j) > 8 for j in seen):
            seen.append(int(i))
        if len(seen) >= 12:
            break
    for i in sorted(seen):
        print(f"    quefrency {i:5d} ({i/SR*1000:7.3f} ms)  值 {c[i]:+.5f}")

    # ---- D) 稀疏度随时间 ----
    print("\n=== D) 稀疏度（每 10 ms 窗内 |x|>峰值1% 的样点占比） ===")
    for ms in range(0, 200, 10):
        a, b = int(ms / 1000 * SR), int((ms + 10) / 1000 * SR)
        w = L[a:b]
        if not len(w):
            break
        frac = float(np.mean(np.abs(w) > np.abs(L).max() * 0.01))
        print(f"    {ms:4d}–{ms+10:4d} ms  占比 {frac*100:6.2f}%"
              f"  rms {np.sqrt(np.mean(w**2)):.3e}")

    # ---- E) 左右声道的簇位置差异 ----
    print("\n=== E) 左右声道早期簇起点对比 ===")
    for tag, ch in (("L", L), ("R", R)):
        t = np.abs(ch).max() * 1e-3
        nz = np.nonzero(np.abs(ch[:int(0.04 * SR)]) > t)[0]
        print(f"    {tag}: 首个 {nz[0] if len(nz) else -1} 样点"
              f" ({(nz[0]/SR*1000) if len(nz) else float('nan'):.3f} ms)")


if __name__ == "__main__":
    main()
