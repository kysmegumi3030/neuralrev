"""LOW CUT 的真实形状：`lowcut=0`（显示 50 Hz）到底是不是一个真高通？

动机：候选在 20 Hz 比参考低 22 dB，而把自家 2 极点 50 Hz/Q=0.6 高通的
响应（20 Hz 处 −16.5 dB）扣掉后，缺口反号成 +5.7 dB。也就是说
**自家高通在最低档压掉的量，参考并没有压**。

另有一条旁证（REFERENCE §6）：显示 fc=212 Hz 档，@fc 实测只有 −0.05 dB。
真正的 2 极点在自己的拐点处必然有 −3…−6 dB。⇒ **显示值不是 −3 dB 点**。

判决办法（比值法，不需要知道 lowcut=0 的绝对响应）：
    R(f) = |IR(lowcut=v)| / |IR(lowcut=0)|
  * 若 lowcut=0 ≈ 旁通：R = HP(fc_v)，在低频以 12 dB/oct **持续滚降**；
  * 若 lowcut=0 是真 50 Hz 高通：R = HP(fc_v)/HP(50)，两者同阶，
    低频端斜率相消 → R 在低频**趋于平台** (fc_v/50)²。
这两种形状在 20–40 Hz 完全不同，可以直接判。

顺带反解每档的等效 −3 dB 点，看它与显示值的关系。

用法：python3 tools/measure/ref_lowcut_shape.py
"""
from __future__ import annotations

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

# 用较宽的平滑（1/3 oct）：这里要的是滤波器的**趋势**，
# 不是模式细节；宽平滑能把梳状涟漪压掉。
OCT = 1 / 3

LEVELS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
PROBE = [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 700]


def smooth(y, of=OCT):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def ir(r, lowcut):
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    p = dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=lowcut, highcut=1.0)
    y = r.render(x, params={f"reverb_{k}": v for k, v in p.items()})
    return y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]


def at(curve, hz):
    return np.array([curve[np.argmin(np.abs(F - h))] for h in hz])


def hp_mag_db(f, fc, q, order_pairs=1):
    """RBJ 2 极点高通级联 order_pairs 次的幅度（dB）。"""
    f = np.asarray(f, float)
    w0 = 2 * np.pi * fc / SR
    cs, sn = np.cos(w0), np.sin(w0)
    al = sn / (2 * q)
    b0, b1, b2 = (1 + cs) / 2, -(1 + cs), (1 + cs) / 2
    a0, a1, a2 = 1 + al, -2 * cs, 1 - al
    z = np.exp(-1j * 2 * np.pi * f / SR)
    H = (b0 + b1 * z + b2 * z ** 2) / (a0 + a1 * z + a2 * z ** 2)
    return order_pairs * 20 * np.log10(np.abs(H))


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)

    base = smooth(ir(r, 0.0))
    base_db = 20 * np.log10(np.maximum(base, 1e-30))

    print("R(f) = |IR(lowcut=v)| / |IR(lowcut=0)|，1/3 oct 平滑（dB）\n")
    print(f"{'Hz':>7} " + " ".join(f"v={v:.1f}".rjust(9) for v in LEVELS[1:]))
    curves = {}
    for v in LEVELS[1:]:
        cur = 20 * np.log10(np.maximum(smooth(ir(r, v)), 1e-30))
        curves[v] = cur - base_db
    for h in PROBE:
        row = " ".join(f"{at(curves[v], [h])[0]:9.2f}" for v in LEVELS[1:])
        print(f"{h:7.1f} " + row)

    print("\n低频端形状判决（比值在 20→40 Hz 的斜率，dB/oct）：")
    print("  持续滚降 ≈ +12 dB/oct ⇒ lowcut=0 是旁通")
    print("  趋于平台 ≈  0 dB/oct ⇒ lowcut=0 是真 50 Hz 高通\n")
    for v in LEVELS[1:]:
        d20 = at(curves[v], [20])[0]
        d40 = at(curves[v], [40])[0]
        fc = 50.0 + 650.0 * v
        # 两个模型的预测斜率
        byp = hp_mag_db([40], fc, 0.7071)[0] - hp_mag_db([20], fc, 0.7071)[0]
        real = ((hp_mag_db([40], fc, 0.7071)[0] - hp_mag_db([40], 50.0, 0.7071)[0])
                - (hp_mag_db([20], fc, 0.7071)[0] - hp_mag_db([20], 50.0, 0.7071)[0]))
        print(f"  v={v:.1f} (fc={fc:6.1f} Hz)  实测 {d40 - d20:+6.2f}"
              f"   旁通模型 {byp:+6.2f}   真高通模型 {real:+6.2f}")

    print("\n每档的等效 −3 dB 点（比值曲线跌到 −3 dB 处）vs 显示值：")
    for v in LEVELS[1:]:
        c = curves[v]
        m = (F >= 15) & (F <= 2000)
        f_m, c_m = F[m], c[m]
        idx = np.where(c_m <= -3.0)[0]
        got = f_m[idx[-1]] if len(idx) else float("nan")
        print(f"  v={v:.1f}  显示 {50.0 + 650.0 * v:6.1f} Hz   "
              f"比值 −3 dB 处 {got:7.1f} Hz")


if __name__ == "__main__":
    main()
