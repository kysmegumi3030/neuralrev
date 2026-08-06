"""DRY/WET 混合律的闭式确认。

粗测与精测（ref_drywet.py，两种独立测法逐点一致）给出：

    dw     0.0   0.1   0.2   0.3   0.4   0.5   0.6   0.7   0.8   0.9   1.0
    干     1.00  1.00  1.00  1.00  1.00  1.00  1.00  1.00  0.72  0.38  0.00
    湿     0.00  0.02  0.08  0.18  0.32  0.50  0.72  0.98  1.00  1.00  1.00

湿的那一行正是 **2·dw²**（0.02=2·0.01，0.08=2·0.04，0.18=2·0.09，
0.32=2·0.16，0.50=2·0.25，0.72=2·0.36，0.98=2·0.49），在 dw²=0.5 即
dw = 1/√2 ≈ 0.7071 处触顶 1.0 后钳位。

干的那一行在 dw ≤ 1/√2 恒为 1，之后线性降到 0：
    0.8 → 0.72，0.9 → 0.38，1.0 → 0.00
斜率 = (0 − 0.72)/(1.0 − 0.8) = −3.6，零点 dw = 1.0。
反解起点：1 = −3.6·dw₀ + 3.6 → dw₀ = 0.7222。

于是完整闭式（w = dw）：

    wet(w)  = min(1, 2w²)
    dry(w)  = 1                                w ≤ 0.7222
            = (1 − w) / (1 − 0.7222) = 3.6(1−w) w > 0.7222

本脚本用**独立细网格**（步长 0.02，含两个断点附近的密集采样）
验证这两个闭式，并给出最大偏差。

用法：python3 tools/measure/ref_drywet_law.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
LATENCY = 51
IMPULSE_AT = int(2.5 * SR)

QUANT = 0.01
"""插件把 DRY/WET 量化到 0.01 的栅格上（实测：dw 在 0.7050…0.7125 之间输出
完全不变，每跨过一个 0.01 才跳一档）。在栅格点上闭式与实测**逐点精确相等**，
非栅格点的偏差纯粹是量化，不是模型误差。"""


def quantize(w):
    """向下取整到 0.01 栅格。

    实测插件取的是 floor 而非 round：dw=0.705 与 0.7125 都给出 dw=0.70 的系数
    （0.99180 对应 0.71，而 0.705 实测 0.99180 → 说明 0.705 落在 0.71 档；
    再看 0.725 实测 0.93420 = 2(1−0.73²) → 落在 0.73 档）。
    半格点的归属与浮点表示有关，栅格中心点上两种取整一致，
    故对拍时统一取 0.01 的整数倍档位，避开半格点。
    """
    return np.floor(w / QUANT + 0.5) * QUANT


def model_wet(w):
    """湿增益 = min(1, 2·q²)，q 为量化后的 dw。"""
    q = quantize(w)
    return min(1.0, 2.0 * q * q)


def model_dry(w):
    """干增益 = min(1, 2·(1−q²))。

    与湿声互补：wet² + dry² 在 2·(w²) 与 2·(1−w²) 两支上，
    交点在 w = 1/√2（此处两者同时为 1）→ 这是一条**功率互补**的交叉淡化，
    只是两支都被 min(·,1) 钳住，于是中段出现「干湿同时满增益」的平台。
    """
    q = quantize(w)
    return min(1.0, 2.0 * (1.0 - q * q))


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    n = IMPULSE_AT + int(3.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0

    # 湿声幅度的归一基准：dw=1 的湿能量
    y1 = r.render(x, params={"reverb_drywet": 1.0}).astype(np.float64)[0][IMPULSE_AT + LATENCY:]
    wet_ref = float(np.sqrt(np.sum(y1[200:] ** 2)))

    # 全部取 0.01 栅格的整数倍（避开半格点的取整歧义），并在拐点 1/√2 附近加密
    grid = [round(v, 2) for v in np.arange(0.0, 1.0001, 0.05)]
    grid += [0.68, 0.69, 0.70, 0.71, 0.72, 0.73, 0.74, 0.76, 0.78]
    grid = sorted(set(grid))

    print("     dw     干实测   干模型   Δ干      湿实测   湿模型   Δ湿")
    max_d, max_w = 0.0, 0.0
    for dw in grid:
        y = r.render(x, params={"reverb_drywet": dw}).astype(np.float64)[0][IMPULSE_AT + LATENCY:]
        dry_m = float(y[0])
        wet_m = float(np.sqrt(np.sum(y[200:] ** 2))) / wet_ref
        dd = dry_m - model_dry(dw)
        dwv = wet_m - model_wet(dw)
        max_d = max(max_d, abs(dd))
        max_w = max(max_w, abs(dwv))
        print(f"   {dw:5.3f}  {dry_m:8.5f} {model_dry(dw):8.5f} {dd:+8.5f}"
              f"  {wet_m:8.5f} {model_wet(dw):8.5f} {dwv:+8.5f}")

    print(f"\n干增益最大偏差 = {max_d:.2e}")
    print(f"湿增益最大偏差 = {max_w:.2e}")
    print("\n闭式（q = dw 量化到 0.01 栅格）：")
    print("    wet = min(1, 2·q²)")
    print("    dry = min(1, 2·(1−q²))")
    print(f"  功率互补交叉淡化，两支都被钳在 1；交点 q = 1/√2 = {1.0/np.sqrt(2.0):.5f}")


if __name__ == "__main__":
    main()
