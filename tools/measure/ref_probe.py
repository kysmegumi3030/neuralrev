"""参考混响基础性质探测：延迟、干信号透明性、gate 行为、dry/wet 律、IR 可测性。

这些是后续所有测量的前提假设，必须先证实：
  * 隔离设置下干路是否 bit-transparent（否则测不出「纯混响」）
  * 固有延迟（否则波形 diff 全错）
  * gate 的开门行为（决定 IR 怎么激励：裸冲激会被吞掉）
  * dry/wet 的混合律（等功率？线性？）

输出：measurements/ref_probe.json
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "measurements")

# gate 在无信号起步时是关闭的（实测：裸冲激输出恒为 ~8.7e-8，与幅度无关；
# 先用 0.3 s 正弦「预热」后，同一冲激原样通过，峰值 1.0）。
# 所以所有冲激类激励前面都要加一段预热信号，并在分析时跳过。
PRIME_SEC = 0.30
PRIME_FREQ = 220.0
PRIME_AMP = 0.2
GAP_SEC = 0.20  # 预热与冲激之间的静音间隔（让预热的混响尾巴衰减开）


def prime_then(x_after: np.ndarray, sr=SR):
    """在激励前拼接「预热正弦 + 静音间隔」，返回 (信号, 激励起始样点)。"""
    t = np.arange(int(PRIME_SEC * sr)) / sr
    prime = (PRIME_AMP * np.sin(2 * np.pi * PRIME_FREQ * t)).astype(np.float32)
    gap = np.zeros(int(GAP_SEC * sr), dtype=np.float32)
    sig = np.concatenate([prime, gap, np.asarray(x_after, dtype=np.float32)])
    return sig, len(prime) + len(gap)


def db(x):
    return 20.0 * np.log10(np.maximum(np.abs(np.asarray(x, float)), 1e-300))


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    dry_only = {V.REVERB_PARAMS["reverb_active"]: 0.0}
    res = {"sr": SR}

    # ---- 1. 干路透明性 + 固有延迟 ----
    # 必须用**非周期**激励测延迟：周期信号（正弦）的互相关有多个等高峰，
    # 会把 51 样点的真实延迟误判成 3 样点。这里用白噪声（前置预热段开 gate）。
    rng = np.random.default_rng(1)
    t = np.arange(int(PRIME_SEC * SR)) / SR
    prime = (PRIME_AMP * np.sin(2 * np.pi * PRIME_FREQ * t)).astype(np.float32)
    noise = (0.2 * rng.standard_normal(SR)).astype(np.float32)
    x = np.concatenate([prime, noise])
    y = r.render(x, params=dry_only)[0]

    a, b = x.astype(float), y.astype(float)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    lag = int(np.argmax(np.abs(np.correlate(b, a, "full"))) - (n - 1))
    aligned = b[lag:] if lag >= 0 else b
    m = min(len(aligned), len(a))
    s = int(0.4 * SR)  # 跳过预热段
    err = float(np.max(np.abs(aligned[s:m] - a[s:m])))
    res["latency_samples"] = lag
    res["dry_max_abs_err"] = err
    print(f"[1] 干路：latency={lag} samples, max|y-x|={err:.3e}"
          f"（{'bit-transparent' if err < 1e-6 else '有着色'}）")

    # ---- 2. gate 行为：裸冲激 vs 预热冲激 ----
    bare = np.zeros(SR, dtype=np.float32)
    bare[1000] = 1.0
    y_bare = r.render(bare, params=dry_only)[0]

    imp = np.zeros(int(0.5 * SR), dtype=np.float32)
    imp[0] = 1.0
    sig, off = prime_then(imp)
    y_prime = r.render(sig, params=dry_only)[0]
    seg = y_prime[off + lag - 5: off + lag + 50]
    res["gate_bare_impulse_peak"] = float(np.abs(y_bare).max())
    res["gate_primed_impulse_peak"] = float(np.abs(seg).max())
    print(f"[2] gate：裸冲激峰值={np.abs(y_bare).max():.3e}，"
          f"预热后冲激峰值={np.abs(seg).max():.4f}")

    # ---- 3. dry/wet 混合律：用 1 kHz 正弦稳态测干湿两端的贡献 ----
    #     wet 端能量随频率变化，故这里只记录**干成分**的系数：
    #     把 decay 拉到最小、high cut 拉到最低，湿声在 1 kHz 处贡献很小时近似只剩干。
    laws = []
    for dw in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
        y = r.render(x, params={"reverb_drywet": dw})[0]
        seg = y[int(0.5 * SR):]
        laws.append({"drywet": dw, "rms": float(np.sqrt(np.mean(seg.astype(float) ** 2)))})
        print(f"[3] drywet={dw:.2f} -> rms={laws[-1]['rms']:.5f}")
    res["drywet_rms_1k"] = laws

    # ---- 4. 湿声 IR 可测性：drywet=1，预热 + 冲激，看尾巴长度 ----
    imp = np.zeros(int(6.0 * SR), dtype=np.float32)
    imp[0] = 1.0
    sig, off = prime_then(imp)
    y = r.render(sig, params={"reverb_drywet": 1.0})[0]
    ir = y[off + lag:]
    env = np.sqrt(np.convolve(ir.astype(float) ** 2, np.ones(512) / 512, "same"))
    peak = env.max()
    below60 = np.nonzero(env < peak * 10 ** (-60 / 20))[0]
    res["ir_peak"] = float(peak)
    res["ir_len_to_-60dB_samples"] = int(below60[-1]) if len(below60) else None
    # 找到 -60 dB 的**首次持续**跨越点更有意义
    idx = None
    for i in range(len(env)):
        if env[i] < peak * 10 ** (-60 / 20) and np.all(env[i:i + SR // 10] < peak * 10 ** (-60 / 20)):
            idx = i
            break
    res["ir_t60_samples"] = idx
    print(f"[4] 湿声 IR：peak={peak:.4e}, -60 dB 于 {idx} 样点"
          f"（{(idx / SR) if idx else float('nan'):.3f} s，DECAY=4.25 设定）")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "ref_probe.json")
    with open(path, "w") as f:
        json.dump(res, f, indent=2)
    print(f"\n-> {os.path.normpath(path)}")


if __name__ == "__main__":
    main()
