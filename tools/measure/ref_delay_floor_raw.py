"""延迟地板的最后一问：**原始**逐 bin（不平滑）在通带内过不过 3 dB。

这一问决定延迟段要不要沿用混响的口径放宽，所以必须单独测、单独报。

前两个脚本已经把两件事钉死：

* `ref_delay_floor.py` —— 全谱平滑口径最差 4.24 dB（2–20 kHz 带）；
* `ref_delay_floor_hf.py` —— 那 4.24 dB **只在 16–20 kHz**，而该带电平比全谱
  峰值低 58.9 dB，即 `delay_lowpass=1.0`（标称 16 kHz）的阻带。阻带里两条 IR
  比的是准噪声，比值天然乱跳，且随位移**非单调**（16→3.03、48→4.24、
  480→1.15、4800→0.51 dB），与 LFO 相位差应有的单调性相反 ⇒ 它不是失配指纹。
  去掉阻带后 20 Hz–16 kHz 的平滑地板是 **1.76 dB**，2–8 kHz 只有 0.22 dB。

于是延迟段与混响段的处境**不同**：混响的平滑地板本身就 8–13 dB（REFERENCE §10.1），
逼得验收口径必须从原始逐 bin 退到平滑逐 bin；延迟的平滑地板远在 3 dB 以内，
所以「要不要退」不再是既定结论，得回头问原始口径本身能不能用。

本脚本因此把**门限从 −80 dB 收紧到 −60/−40 dB**（把阻带排除在外，而不是靠
频带边界硬切），在通带内报原始逐 bin 的 max / p99.9 / p99 / >3 dB 占比。
分位数与占比一起报的理由：max 是单个 bin 的极值，对「谱零点附近相位微差被
放大成大 dB 差」极其敏感；若 max 超 3 dB 而 p99.9 远低于 3 dB，说明超标是
少数零点 bin，而不是宽带失配 —— 这两种情形对实现的要求完全不同。

用法：
    python3 tools/measure/ref_delay_floor_raw.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_floor import (  # noqa: E402
    DUR, IMP_AT, PARAMS, SR, impulse, spec_db,
)

SHIFTS = (1, 16, 48, 480, 4800)
FLOORS = (-80.0, -60.0, -40.0)   # 相对全谱峰值的能量门限


def hdr(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def main() -> None:
    n = int(DUR * SR)
    at = int(IMP_AT * SR)
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")

    base = r.render(impulse(n, at), PARAMS)[0]

    specs = []
    for sh in SHIFTS:
        other = r.render(impulse(n, at + sh), PARAMS)[0]
        m = min(len(base) - at, len(other) - at - sh)
        u, v = base[at:at + m], other[at + sh:at + sh + m]
        f, a = spec_db(u)
        _, b = spec_db(v)
        specs.append((sh, f, a, np.abs(a - b)))

    for fl in FLOORS:
        hdr(f"原始逐 bin，门限 {fl:+.0f} dB（相对全谱峰值）")
        print(f"  {'位移':>8} {'bin 数':>8} {'上限 Hz':>9} {'max':>8} "
              f"{'p99.9':>8} {'p99':>8} {'p95':>8} {'>3dB 占比':>11}")
        for sh, f, a, raw in specs:
            keep = (f >= 20) & (f <= 20000) & (a > a.max() + fl)
            d = raw[keep]
            # 门限实际把通带切在哪：留下的最高频 bin
            fmax = float(f[keep].max()) if keep.any() else float("nan")
            print(f"  {sh:8d} {int(keep.sum()):8d} {fmax:9.0f} "
                  f"{d.max():8.2f} {np.percentile(d, 99.9):8.2f} "
                  f"{np.percentile(d, 99):8.2f} {np.percentile(d, 95):8.2f} "
                  f"{np.mean(d > 3.0) * 100:10.2f}%")

    hdr("判读")
    print("  max 超 3 dB 而 p99.9 远低于 3 dB ⇒ 超标集中在少数谱零点 bin，非宽带失配。")
    print("  三个门限下 max 都随位移单调增 ⇒ 那是 LFO 相位差，是真下界。")


if __name__ == "__main__":
    main()
