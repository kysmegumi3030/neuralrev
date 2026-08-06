"""参考混响的湿声脉冲响应（IR）采集 + 结构分析。

采集：预热正弦（开 gate）→ 静音间隔 → 单位冲激 → 长尾巴。
    drywet=1 取「纯湿」，drywet=0 取「纯干」，两者相减可验证混合律。

分析要点（决定后续能否用 FDN/plate 结构对上）：
  * 混响是否 LTI（两次相同激励是否 bit-identical；缩放是否严格线性）
  * 是否含调制（LTI 检验失败即说明有 LFO/时变延迟）
  * 左右声道是否去相关、是否互为镜像
  * pre-delay 是否就是纯延迟（首次非零位置 vs 参数显示值）
  * 早期反射（离散 tap）位置 / 密度
  * 衰减包络（EDC）与 DECAY 参数的关系
  * 湿声频响（low cut / high cut 的实际滤波器阶数与拐点）

用法：
    python3 tools/measure/ref_ir.py            # 默认档位
    python3 tools/measure/ref_ir.py --grid     # 采一批参数档位存 npz
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
LATENCY = 51  # 实测固有延迟（ref_probe.py）
MEAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "measurements")

PRIME_SEC, PRIME_FREQ, PRIME_AMP, GAP_SEC = 0.30, 220.0, 0.2, 0.25


def excite(tail_sec=8.0, amp=1.0, sr=SR, with_impulse=True):
    """预热 + 间隔 + （可选）冲激 + 尾巴；返回 (signal, impulse_index)。

    预热段是为了把 gate 打开（裸冲激会被 gate 吞掉，见 ref_probe.py）。
    """
    t = np.arange(int(PRIME_SEC * sr)) / sr
    prime = (PRIME_AMP * np.sin(2 * np.pi * PRIME_FREQ * t)).astype(np.float32)
    gap = np.zeros(int(GAP_SEC * sr), dtype=np.float32)
    tail = np.zeros(int(tail_sec * sr), dtype=np.float32)
    idx = len(prime) + len(gap)
    spike = np.array([amp if with_impulse else 0.0], dtype=np.float32)
    return np.concatenate([prime, gap, spike, tail]), idx


def grab_ir(r, params, tail_sec=8.0, amp=1.0, sr=SR):
    """差分法取 IR：render(预热+冲激) − render(预热+无冲激)。

    为什么必须差分：drywet=1 时**预热正弦自己也会进混响**，它的尾巴长达数秒，
    远超 0.25 s 的间隔，直接切段得到的是「预热尾巴 + 冲激响应」的叠加
    （实测污染极重：首样点就有能量、齐次性检验相对误差 0.98）。
    两次渲染只差那一个冲激样点，相减即把预热尾巴整段消掉，
    留下纯净的冲激响应。这一步同时是 LTI/叠加性的检验。
    """
    sig_a, idx = excite(tail_sec, amp, sr, with_impulse=True)
    sig_b, _ = excite(tail_sec, amp, sr, with_impulse=False)
    ya = r.render(sig_a, params=params)
    yb = r.render(sig_b, params=params)
    d = ya.astype(np.float64) - yb.astype(np.float64)
    return np.ascontiguousarray(d[:, idx + LATENCY:])


def edc_db(x, sr=SR, win=1024):
    """短时能量包络（dB，峰值归一）。"""
    e = np.convolve(np.asarray(x, float) ** 2, np.ones(win) / win, "same")
    e = np.sqrt(np.maximum(e, 1e-300))
    return 20.0 * np.log10(e / max(e.max(), 1e-300))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", action="store_true", help="采集参数网格并存 npz")
    args = ap.parse_args()

    r = V.Vst3RefRenderer(sr=SR, block=512)
    os.makedirs(MEAS, exist_ok=True)

    # ---- 默认档位的纯湿 IR ----
    wet = {"reverb_drywet": 1.0}
    ir = grab_ir(r, wet)
    L, R = ir[0].astype(float), ir[1].astype(float)
    print(f"IR 长度 {len(L)} 样点（{len(L)/SR:.2f} s）")

    # 1) LTI 检验：同一激励两次是否逐样点一致
    ir2 = grab_ir(r, wet)
    same = float(np.max(np.abs(ir2[0].astype(float) - L)))
    print(f"[LTI-1] 重复渲染 max|Δ| = {same:.3e} "
          f"({'确定性' if same == 0 else '不确定/有噪声或状态残留'})")

    # 2) 齐次性：幅度缩放是否严格线性（有调制/非线性时会破）
    ir_half = grab_ir(r, wet, amp=0.5)
    hom = float(np.max(np.abs(ir_half[0].astype(float) * 2.0 - L)))
    rel = hom / max(np.abs(L).max(), 1e-300)
    print(f"[LTI-2] 0.5 倍激励×2 与 1.0 倍 max|Δ| = {hom:.3e}（相对峰值 {rel:.2e}）"
          f" {'→ 线性' if rel < 1e-4 else '→ 非线性/时变'}")

    # 3) 左右声道关系
    n = min(len(L), len(R))
    cc = float(np.corrcoef(L[:n], R[:n])[0, 1])
    print(f"[stereo] corr(L,R) = {cc:+.4f}；corr(L,-R) = {-cc:+.4f}")

    # 4) pre-delay：首个显著非零样点
    thr = np.abs(L).max() * 1e-3
    nz = np.nonzero(np.abs(L) > thr)[0]
    first = int(nz[0]) if len(nz) else -1
    print(f"[predelay] 首个 >{thr:.2e} 的样点 = {first}"
          f"（{first/SR*1000:.2f} ms；参数显示 {V.predelay_ms(0.5):.2f} ms）")

    # 5) 峰值与早期结构
    peak = int(np.argmax(np.abs(L)))
    print(f"[early] 峰值样点 {peak}（{peak/SR*1000:.2f} ms），值 {L[peak]:+.4f}")
    big = nz[:40] if len(nz) >= 40 else nz
    print(f"[early] 前若干显著样点索引: {big[:20].tolist()}")

    # 6) 衰减包络 → 估 T60
    e = edc_db(L)
    below = np.nonzero(e < -60.0)[0]
    t60 = int(below[0]) if len(below) else -1
    print(f"[decay] EDC 首次跌破 -60 dB 于 {t60} 样点（{t60/SR:.3f} s）")

    # 7) 湿声频响（对 IR 直接做 FFT）
    nfft = 1 << 16
    seg = L[:nfft] if len(L) >= nfft else np.pad(L, (0, nfft - len(L)))
    H = np.abs(np.fft.rfft(seg))
    f = np.fft.rfftfreq(nfft, 1.0 / SR)
    Hn = 20 * np.log10(np.maximum(H, 1e-12) / max(H.max(), 1e-12))
    print("[spectrum] 湿声 IR 幅度（相对峰值）:")
    for fq in [20, 30, 50, 80, 120, 200, 500, 1000, 2000, 5000, 8000, 10000, 12000, 16000]:
        i = int(fq / SR * nfft)
        print(f"    {fq:6d} Hz  {Hn[i]:+7.2f} dB")

    stats = {
        "sr": SR, "latency": LATENCY, "ir_len": int(len(L)),
        "lti_repeat_maxdiff": same, "homogeneity_maxdiff": hom,
        "homogeneity_rel": rel, "corr_LR": cc,
        "first_nonzero": first, "peak_index": peak,
        "t60_samples": t60,
    }
    with open(os.path.join(MEAS, "ref_ir_stats.json"), "w") as fh:
        json.dump(stats, fh, indent=2)
    np.savez_compressed(os.path.join(MEAS, "ref_ir_default.npz"), L=ir[0], R=ir[1])
    print(f"\n-> measurements/ref_ir_default.npz, ref_ir_stats.json")

    # ---- 参数网格 ----
    if args.grid:
        grid = {}
        for name, key, vals in [
            ("decay",    "reverb_decay",    [0.0, 0.25, 0.5, 0.75, 1.0]),
            ("predelay", "reverb_predelay", [0.0, 0.25, 0.5, 0.75, 1.0]),
            ("lowcut",   "reverb_lowcut",   [0.0, 0.5, 1.0]),
            ("highcut",  "reverb_highcut",  [0.0, 0.5, 1.0]),
        ]:
            for v in vals:
                p = dict(wet)
                p[key] = v
                tail = 10.0 if key == "reverb_decay" and v > 0.6 else 8.0
                ir_g = grab_ir(r, p, tail_sec=tail)
                grid[f"{name}_{v:.2f}_L"] = ir_g[0]
                grid[f"{name}_{v:.2f}_R"] = ir_g[1]
                print(f"  grid {name}={v:.2f} -> {ir_g.shape[1]} 样点")
        np.savez_compressed(os.path.join(MEAS, "ref_ir_grid.npz"), **grid)
        print("-> measurements/ref_ir_grid.npz")


if __name__ == "__main__":
    main()
