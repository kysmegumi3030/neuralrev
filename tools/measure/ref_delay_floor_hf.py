"""延迟地板的两点追问 —— `ref_delay_floor.py` 的结果不能直接当口径用。

那张表留下两个必须先解释掉的疑点，否则读出来的「下界」是假的：

**疑点 1：唯一超 3 dB 的带是 2–20 kHz，且随位移非单调。**
    位移  16 → 3.03 dB，48 → 4.24 dB，480 → 1.15 dB，4800 → 0.51 dB。
若误差全由 LFO 相位差引起，它应当**随位移单调变大**（1.71 Hz 下 4800 样点
= 17% 个周期，远大于 48 样点的 0.17%）。实测反过来 ⇒ 2–20 kHz 那个数
**不是 LFO 的指纹**，另有来源。最可能的来源：`delay_lowpass=1.0` 只到
16 kHz，16–20 kHz 已在陡降段，−80 dB 门限会放进一批准噪声 bin，
它们的比值天然乱跳。故把 2–20 kHz 拆成 2–8 / 8–16 / 16–20 三段单独看。

**疑点 2：波形列的绝对值不能和 1e-3 比。**
激励是 AMP=1e-3（饱和逼着我们待在线性区），于是 max|Δ|=8e-05 这个数
**是在小信号下量的**。线性系统里它随激励幅度等比放大：换成满幅激励就是
8e-02，超 1e-3 八十倍。所以这里同时报**相对量** max|Δ|/max|湿声|，
那才是与激励幅度无关、可以和 1e-3 对话的量。

用法：
    python3 tools/measure/ref_delay_floor_hf.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_floor import (  # noqa: E402
    AMP, DUR, IMP_AT, NFFT, PARAMS, SR, impulse, smooth_frac_oct, spec_db,
)

SHIFTS = (1, 16, 48, 480, 4800)

# 把原来的 2–20 kHz 拆开：16 kHz 是 lowpass 的标称上限，拆点选在它上面
BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000),
         (2000, 8000), (8000, 16000), (16000, 20000)]


def hdr(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def main() -> None:
    n = int(DUR * SR)
    at = int(IMP_AT * SR)
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")

    base = r.render(impulse(n, at), PARAMS)[0]

    hdr("波形口径：相对量才能和 1e-3 对话（激励 AMP=1e-3，线性区）")
    print(f"  {'位移':>8} {'max|Δ|':>12} {'max|湿声|':>12} "
          f"{'相对 max|Δ|':>13} {'nrmse':>10} {'折算满幅':>12}")

    rows = []
    for sh in SHIFTS:
        other = r.render(impulse(n, at + sh), PARAMS)[0]
        m = min(len(base) - at, len(other) - at - sh)
        u, v = base[at:at + m], other[at + sh:at + sh + m]

        dev = float(np.max(np.abs(u - v)))
        peak = float(np.max(np.abs(u)))
        rel = dev / (peak + 1e-30)
        nrmse = float(np.linalg.norm(u - v) / (np.linalg.norm(u) + 1e-30))
        # 线性区内 max|Δ| 与激励幅度等比 ⇒ 满幅（amp=1.0）激励下的绝对偏差
        full = dev / AMP
        print(f"  {sh:8d} {dev:12.3e} {peak:12.3e} {rel:13.3e} "
              f"{nrmse * 100:9.4f}% {full:12.3e}")

        f, a = spec_db(u)
        _, b = spec_db(v)
        keep = (f >= 20) & (f <= 20000) & (a > a.max() - 80.0)
        sa = smooth_frac_oct(10 ** (a / 20.0), f)
        sb = smooth_frac_oct(10 ** (b / 20.0), f)
        sm = np.abs(20.0 * np.log10(sa + 1e-30) - 20.0 * np.log10(sb + 1e-30))
        rows.append((sh, f, a, sm, keep))

    hdr("2–20 kHz 拆开：那 4.24 dB 是真的宽带失配，还是 16 kHz 以上的准噪声？")
    print(f"  {'频带':>16} {'bin 数':>7} {'带内电平':>10} "
          + " ".join(f"{sh:>8}" for sh in SHIFTS) + f" {'下界':>8}")
    for lo, hi in BANDS:
        vals, nb, lvl = [], 0, float("nan")
        for sh, f, a, sm, keep in rows:
            sel = keep & (f >= lo) & (f < hi)
            vals.append(float(sm[sel].max()) if sel.any() else float("nan"))
            if sh == SHIFTS[0]:
                nb = int(sel.sum())
                # 带内相对电平：相对全谱峰值，判断这段是不是已经在噪声里
                lvl = float(a[sel].max() - a[keep].max()) if sel.any() else float("nan")
        lab = f"{lo}-{hi} Hz"
        print(f"  {lab:>16} {nb:7d} {lvl:9.1f}dB "
              + " ".join(f"{v:8.2f}" for v in vals) + f" {np.nanmax(vals):8.2f}")

    hdr("判读")
    print("  若 4.24 dB 只出现在 16–20 kHz 且该带电平 < −40 dB ⇒ 是门限放进的准噪声，")
    print("  真实下界应按 2–16 kHz 报；若 2–8 kHz 也超 3 dB ⇒ 宽带失配，口径必须放宽。")


if __name__ == "__main__":
    main()
