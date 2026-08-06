"""低频缺口的成因诊断：是「网络模式不足」还是「自家 LOW CUT 压得太狠」？

背景（默认档位 lowcut=0，即 50 Hz）：
    20–40 Hz p95 = 15.31 dB，20 Hz 处候选比参考低 23 dB。

两个互斥的嫌疑：
  (A) 自家的 2 极点 RBJ 高通在 lowcut=0 时仍在起作用。
      50 Hz / Q=0.6 的 2 极点高通在 20 Hz 处约 −16 dB；
      但参考实测在 20 Hz 只有 −10.4 dB，且 30 Hz(−4.84) 比 50 Hz(−5.25) 更**强**
      —— 那是模式涟漪，不是滤波器滚降。若 (A) 成立，说明参考在最低档
      几乎等于旁通，我们不该照搬 50 Hz 的教科书高通。
  (B) FDN 的低频模式密度不足（最长线只有 ~5.5k 样点，20–40 Hz 只有一两条线贡献）。

判据：把自家高通**临时旁通**（lowcut 设成一个极低的等效档，或直接对比
「湿声总线滤波前」的谱），看 20–40 Hz 的缺口还剩多少。
剩得多 ⇒ (B) 为主；缺口基本消失 ⇒ (A) 为主。

本脚本不改任何常数，只打印诊断数字。
用法：python3 tools/fit/diag_lowfreq.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
NFFT = 65536
F = np.fft.rfftfreq(NFFT, 1.0 / SR)

PROBE_HZ = [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315,
            400, 500, 630, 800, 1000, 2000, 4000, 8000, 12000, 16000]


def smooth(y, of=1 / 12):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def ir_ref(r, params):
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    y = r.render(x, params={f"reverb_{k}": v for k, v in params.items()})
    return y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]


def ir_cand(params):
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return c.render(x, params=params).astype(np.float64)[0][BASE_AT:]


def rbj_hp_db(f, fc, q):
    """RBJ 2 极点高通在 f 处的幅度（dB），用于验证「自家滤波器压了多少」。"""
    w0 = 2 * np.pi * fc / SR
    cs, sn = np.cos(w0), np.sin(w0)
    al = sn / (2 * q)
    b0 = (1 + cs) / 2
    b1 = -(1 + cs)
    b2 = b0
    a0 = 1 + al
    a1 = -2 * cs
    a2 = 1 - al
    z = np.exp(-1j * 2 * np.pi * np.asarray(f, float) / SR)
    H = (b0 + b1 * z + b2 * z ** 2) / (a0 + a1 * z + a2 * z ** 2)
    return 20 * np.log10(np.abs(H))


def at(curve, hz):
    return np.array([curve[np.argmin(np.abs(F - h))] for h in hz])


def main():
    P = dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)
    r = V.Vst3RefRenderer(sr=SR, block=512)

    A = smooth(ir_ref(r, P))
    B = smooth(ir_cand(P))

    # 归一到 300–2000 Hz（该带已接近口径，用它当共同参考电平）
    m = (F >= 300) & (F <= 2000)
    ra = 20 * np.log10(np.maximum(A, 1e-30)) - 20 * np.log10(np.mean(A[m]))
    rb = 20 * np.log10(np.maximum(B, 1e-30)) - 20 * np.log10(np.mean(B[m]))

    a_at, b_at = at(ra, PROBE_HZ), at(rb, PROBE_HZ)
    hp = rbj_hp_db(PROBE_HZ, 50.0, 0.6)

    print("以 300–2000 Hz 为 0 dB 基准（1/12 oct 平滑）：\n")
    print(f"{'Hz':>7} {'参考':>8} {'候选':>8} {'候选−参考':>10} "
          f"{'自家HP(50/0.6)':>14} {'扣掉HP后的差':>12}")
    for h, a_, b_, hp_ in zip(PROBE_HZ, a_at, b_at, hp):
        print(f"{h:7.1f} {a_:8.2f} {b_:8.2f} {b_ - a_:10.2f} "
              f"{hp_:14.2f} {(b_ - hp_) - a_:12.2f}")

    print("\n判据：")
    lo = [i for i, h in enumerate(PROBE_HZ) if h <= 40]
    raw = float(np.mean([b_at[i] - a_at[i] for i in lo]))
    corr = float(np.mean([(b_at[i] - hp[i]) - a_at[i] for i in lo]))
    print(f"  20–40 Hz 平均差：原始 {raw:+.2f} dB，扣掉自家 HP 后 {corr:+.2f} dB")
    if abs(corr) < 0.5 * abs(raw):
        print("  → 主因是 (A) 自家 LOW CUT 在最低档压得太狠。")
    else:
        print("  → 主因是 (B) FDN 低频模式密度不足；HP 只解释一部分。")

    print("\n参考自身在最低档的低频形状（判断参考 HP 是否近似旁通）：")
    for h in [20, 25, 31.5, 40, 50, 63, 80]:
        print(f"  {h:6.1f} Hz  {ra[np.argmin(np.abs(F - h))]:+7.2f} dB")


if __name__ == "__main__":
    main()
