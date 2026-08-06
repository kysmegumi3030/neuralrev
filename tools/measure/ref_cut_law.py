"""LOW CUT / HIGH CUT 的**真实拐点律**与 Q（最小二乘，全曲线拟合）。

为什么要重测：
  1. `ref_lowcut_shape.py` 判明 `lowcut=0` 实际≈**旁通**，不是 50 Hz 高通。
     我们照搬「50 Hz 2 极点」在 20 Hz 白扣了 16.5 dB —— 这正是
     20–40 Hz 缺口（p95 15.31 dB）的主因。
  2. 显示串的 fc **不是 −3 dB 点**（v=0.2 显示 180 Hz，实测拐点 ~50 Hz；
     v=1.0 显示 700 Hz，实测 ~776 Hz）。REFERENCE §6 的旁证：显示 212 Hz
     档 @fc 只有 −0.05 dB，真 2 极点不可能这么浅。

方法（比单点读 −3 dB 稳健得多）：对每个档位 v 的比值曲线
    R(f; v) = 20log10|IR(v)| − 20log10|IR(v_ref)|
用模型
    R̂(f; v) = HP_db(f, fc(v), Q) − HP_db(f, fc(v_ref), Q)
    fc(v) = fcmin + (fcmax − fcmin)·v^p
在**有效区**（|R| 在 1…35 dB 之间，避开顶部的 0 dB 平台与底部的
−50 dB 测量地板）上联立最小二乘，解出 (fcmin, fcmax, p, Q) 四个量。
HIGH CUT 同构，只把 HP 换成 LP、参考档取 v=1.0（≈旁通端）。

用法：python3 tools/measure/ref_cut_law.py [--which low|high|both]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V                              # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
NFFT = 65536
F = np.fft.rfftfreq(NFFT, 1.0 / SR)
OCT = 1 / 3

LEVELS = [0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0]

# 拟合有效区：太浅（顶部平台）与太深（测量地板）都不带信息
DEPTH_LO, DEPTH_HI = 1.0, 35.0


def smooth(y, of=OCT):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def ir(r, **over):
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    p = dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)
    p.update(over)
    y = r.render(x, params={f"reverb_{k}": v for k, v in p.items()})
    return y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]


def biquad_db(f, fc, q, kind):
    """RBJ 2 极点 高通/低通 的幅度（dB）。"""
    f = np.asarray(f, float)
    fc = float(np.clip(fc, 1.0, SR * 0.49))
    w0 = 2 * np.pi * fc / SR
    cs, sn = np.cos(w0), np.sin(w0)
    al = sn / (2 * max(q, 1e-3))
    if kind == "hp":
        b0, b1, b2 = (1 + cs) / 2, -(1 + cs), (1 + cs) / 2
    else:
        b0, b1, b2 = (1 - cs) / 2, (1 - cs), (1 - cs) / 2
    a0, a1, a2 = 1 + al, -2 * cs, 1 - al
    z = np.exp(-1j * 2 * np.pi * f / SR)
    H = (b0 + b1 * z + b2 * z ** 2) / (a0 + a1 * z + a2 * z ** 2)
    return 20 * np.log10(np.maximum(np.abs(H), 1e-12))


def fit(which, r):
    kind = "hp" if which == "low" else "lp"
    key = "lowcut" if which == "low" else "highcut"
    v_ref = 0.0 if which == "low" else 1.0
    # 拟合频段：只看拐点附近与阻带，别把另一端的滤波器/网络形状拖进来
    f_lo, f_hi = (15.0, 3000.0) if which == "low" else (300.0, 22000.0)

    base_db = 20 * np.log10(np.maximum(smooth(ir(r, **{key: v_ref})), 1e-30))

    data = []
    for v in LEVELS:
        if abs(v - v_ref) < 1e-9:
            continue
        cur = 20 * np.log10(np.maximum(smooth(ir(r, **{key: v})), 1e-30))
        R = cur - base_db
        m = (F >= f_lo) & (F <= f_hi) & (np.abs(R) >= DEPTH_LO) & (np.abs(R) <= DEPTH_HI)
        if m.sum() > 20:
            data.append((v, F[m], R[m]))
    if not data:
        raise RuntimeError(f"{which}: 没有落在有效区的数据点")

    def resid(theta):
        fcmin, fcmax, p, q = theta
        if not (0.5 < fcmin < 40000 and 0.5 < fcmax < 40000
                and 0.2 < p < 6.0 and 0.15 < q < 3.0):
            return 1e9
        tot, cnt = 0.0, 0

        def fc_of(v):
            return fcmin + (fcmax - fcmin) * (v ** p)

        ref_c = fc_of(v_ref)
        for v, ff, RR in data:
            pred = biquad_db(ff, fc_of(v), q, kind) - biquad_db(ff, ref_c, q, kind)
            tot += float(np.sum((pred - RR) ** 2))
            cnt += len(ff)
        return tot / max(cnt, 1)

    # 粗网格 → 局部坐标下降（不依赖 scipy）
    if which == "low":
        grid = [(a, b, p, q)
                for a in (1.0, 5.0, 12.0, 20.0, 30.0)
                for b in (600.0, 700.0, 800.0, 900.0, 1100.0)
                for p in (1.0, 1.3, 1.67, 2.0, 2.5)
                for q in (0.5, 0.7071, 1.0)]
    else:
        grid = [(a, b, p, q)
                for a in (700.0, 900.0, 1100.0, 1400.0)
                for b in (9000.0, 12000.0, 16000.0, 22000.0)
                for p in (0.6, 0.8, 1.0, 1.3, 1.7)
                for q in (0.5, 0.7071, 1.0)]
    best = min(grid, key=resid)

    step = np.array([max(best[0] * 0.4, 1.0), best[1] * 0.3, 0.3, 0.2])
    cur = np.array(best, float)
    fcur = resid(cur)
    for _ in range(300):
        improved = False
        for i in range(4):
            for s in (+1.0, -1.0):
                trial = cur.copy()
                trial[i] += s * step[i]
                ft = resid(trial)
                if ft < fcur - 1e-12:
                    cur, fcur, improved = trial, ft, True
        if not improved:
            step *= 0.5
            if np.all(step < np.array([0.05, 0.5, 0.002, 0.002])):
                break

    fcmin, fcmax, p, q = cur
    rms = float(np.sqrt(fcur))
    disp_lo, disp_hi = ((50.0, 700.0) if which == "low" else (1000.0, 10000.0))

    print(f"\n===== {'LOW CUT (高通)' if which == 'low' else 'HIGH CUT (低通)'} =====")
    print(f"拟合：fc(v) = {fcmin:.2f} + ({fcmax:.2f} − {fcmin:.2f})·v^{p:.4f}"
          f"   Q = {q:.4f}")
    print(f"拟合残差 RMS = {rms:.3f} dB（有效区 |R| ∈ [{DEPTH_LO},{DEPTH_HI}] dB）")
    print(f"\n{'v':>5} {'显示 fc':>10} {'真实 fc':>10} {'真实/显示':>10}")
    for v in [0.0, 0.15, 0.3, 0.45, 0.5, 0.6, 0.75, 0.9, 1.0]:
        d = disp_lo + (disp_hi - disp_lo) * v
        a = fcmin + (fcmax - fcmin) * (v ** p)
        print(f"{v:5.2f} {d:10.1f} {a:10.1f} {a / d:10.3f}")

    # 逐档残差，确认不是靠某一档撑起来的
    print(f"\n逐档残差 RMS（dB）：")
    for v, ff, RR in data:
        pred = (biquad_db(ff, fcmin + (fcmax - fcmin) * v ** p, q, kind)
                - biquad_db(ff, fcmin + (fcmax - fcmin) * v_ref ** p, q, kind))
        print(f"  v={v:.2f}  n={len(ff):5d}  rms={np.sqrt(np.mean((pred - RR) ** 2)):.3f}")

    return dict(fcmin=fcmin, fcmax=fcmax, p=p, q=q, rms=rms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--which", default="both", choices=["low", "high", "both"])
    a = ap.parse_args()

    r = V.Vst3RefRenderer(sr=SR, block=512)
    out = {}
    for w in (["low", "high"] if a.which == "both" else [a.which]):
        out[w] = fit(w, r)

    print("\n\n==== 可直接落到 ReverbTuning.h 的常数 ====")
    for w, d in out.items():
        tag = "LowCut" if w == "low" else "HighCut"
        print(f"kFit{tag}FcMin   = {d['fcmin']:.4f};")
        print(f"kFit{tag}FcMax   = {d['fcmax']:.4f};")
        print(f"kFit{tag}FcExp   = {d['p']:.4f};")
        print(f"kFit{tag}Q       = {d['q']:.4f};")


if __name__ == "__main__":
    main()
