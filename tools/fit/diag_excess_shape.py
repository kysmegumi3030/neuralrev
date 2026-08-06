"""测量「环内超额高频损耗」的形状，并检验它是否**档位无关**。

这是 `fit_damping_shape.py` 的前置诊断：那个拟合成立的前提正是
「缺口 D 与 DECAY 档无关」，本脚本就是验证该前提的工具。
若哪天 D 变成档位相关，**不要**去拟合逐档补偿 —— 先怀疑测量口径
（历史教训见下）。

## 历史：这个量曾经骗过我一次（2026-08-05，REFERENCE §12.9）

第一版按 1 kHz 锚定，但当时 `band_t60` 的掩码只按电平选点、没有时间下界，
候选自己的 1 kHz 在 decay=0.20 上就读长了 +35.8%（峰前起振段进了回归）。
锚点把自己的误差带进每一个带，伪造出「候选环内损耗随 DECAY 崩塌」
（8 kHz 跨度 12.240 vs 参考 2.436）—— 一个看起来完全像物理机制的假象，
我差点据此去拟合逐档标量补偿。它不可能存在：damping 在 prepare 里只配置一次。

掩码改成时间连通（峰值 ≤ t ≤ 首次跌破 −35 dB）后，两侧的 E 都变回档位无关。
**锚定量的可信度不高于锚点本身**：用它之前先确认锚点带的读数是干净的。

## 量的定义

逐带衰减率 R(band) = 60 / T60(band)   [dB/s]
锚定超额损耗 E(band) = R(band) − R(1kHz)

减去 1 kHz 消掉的是**宽带**项：每条线的 g_i 按 budget 编，budget 只随
DECAY 档变，与频率无关，所以它在 E 里整体抵消。剩下的 E 就是环内
**频率相关**的损耗（damping 一阶低通 + 分数延迟插值的 sinc 型损耗）。

关键判据：damping 与插值都是**每圈固定**的滤波器，与 DECAY 档无关。
若 E(band) 在各档上稳定 ⇒ 模型成立，缺口 D = E_参考 − E_候选 就是
要补的固定形状；若 E 随档漂移 ⇒ 还有档位相关机制没找到，不能直接拟合。

## 口径

T60 一律走 t60_band_guard 的双尾长守卫（漂移 >5% 的带弃用），它内部
复用 fit_damping_t60.band_t60 —— 即已修好的时间连通掩码。不自造度量。
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "fit"))

import t60_band_guard as G                                      # noqa: E402
from plugin_match import vst3_ref as V                          # noqa: E402

ANCHOR = "1 kHz"
NORMS = [0.20, 0.50, 0.70, 0.86, 0.94]

r = V.Vst3RefRenderer(sr=G.SR, block=512)

rows_ref, rows_cand, rows_gap = {}, {}, {}

for norm in NORMS:
    ts, tl = G.tails_for(norm)
    print(f"\n{'#' * 70}\n# decay = {norm:.2f}   尾长 {ts:.1f}/{tl:.1f} s\n{'#' * 70}")
    sys.stdout.flush()

    R = G.ref_guarded(r, norm)
    C = G.measure_guarded(norm)

    print(f"{'带':<9}{'参考T60':>9}{'候选T60':>9}{'相对':>8}   "
          f"{'参考E':>8}{'候选E':>8}{'缺口D':>8}   [dB/s]")

    if ANCHOR not in R or ANCHOR not in C:
        print("  锚点带被守卫弃用 ⇒ 该档无法锚定，跳过")
        continue

    aR, aC = 60.0 / R[ANCHOR], 60.0 / C[ANCHOR]
    for nm, _, _ in G.BANDS:
        if nm not in R or nm not in C:
            print(f"{nm:<9}{'—':>9}{'—':>9}   （守卫弃用）")
            continue
        rr, cc = 60.0 / R[nm], 60.0 / C[nm]
        eR, eC = rr - aR, cc - aC
        rel = 100.0 * (C[nm] / R[nm] - 1.0)
        rows_ref.setdefault(nm, {})[norm] = eR
        rows_cand.setdefault(nm, {})[norm] = eC
        rows_gap.setdefault(nm, {})[norm] = eR - eC
        print(f"{nm:<9}{R[nm]:9.3f}{C[nm]:9.3f}{rel:+8.1f}%   "
              f"{eR:+8.2f}{eC:+8.2f}{eR - eC:+8.2f}")
    sys.stdout.flush()


def table(title, rows):
    print(f"\n=== {title} ===")
    print(f"{'带':<9}" + "".join(f"{n:>9.2f}" for n in NORMS)
          + f"{'  跨度':>9}{'  均值':>9}")
    for nm, _, _ in G.BANDS:
        d = rows.get(nm, {})
        vs = [d.get(n) for n in NORMS]
        ok = [v for v in vs if v is not None]
        span = (max(ok) - min(ok)) if len(ok) > 1 else float("nan")
        mean = float(np.mean(ok)) if ok else float("nan")
        print(f"{nm:<9}"
              + "".join(f"{v:+9.2f}" if v is not None else f"{'—':>9}" for v in vs)
              + f"{span:9.2f}{mean:+9.2f}")


table("参考侧 E(band) [dB/s]", rows_ref)
table("候选侧 E(band) [dB/s]", rows_cand)
table("缺口 D = E_参考 − E_候选 [dB/s]", rows_gap)

print("\n判据：若缺口 D 的『跨度』远小于其『均值』⇒ 固定形状失配，"
      "\n可以用一个与档位无关的环内滤波器（damping fc）去补，"
      "\n且应当按 D 的均值列去拟合，而不是按相对 T60%（后者在长尾档会爆炸）。")
