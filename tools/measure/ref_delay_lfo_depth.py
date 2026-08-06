"""LFO 调制深度随 Delay Time 的律 —— 写 DSP 之前必须定下来的最后一条。

`ref_delay_lfo.py` 第 5 节留下一个反常结果：深度**随延迟时长非单调**，峰值在中档。

    time norm   0.00    0.20    0.40    0.80    1.00
    显示 ms   100.00  168.40  317.15  789.42 1100.00
    峰峰       6.62   10.18   12.93   11.49    5.44   样点
    速率     1.70234 1.70235 1.70235 1.70233 1.70228 Hz  ← 全程不变

两个最自然的实现假设都被它否掉了：

* **加在延迟样点数上**（LFO 幅度固定 ⇒ 峰峰恒定）—— 不是，峰峰变了两倍以上；
* **乘在延迟样点数上**（深度 ∝ 延迟 ⇒ 相对深度恒定）—— 也不是，相对深度
  0.137% → 0.010% 单调降。

还有一个更隐蔽的候选也不成立：**LFO 加在归一参数上**再过 5/3 幂律。那样
深度 = f'(n)·δ，而 f(n)=100+1000·n^(5/3) 的导数 f'(n)=1666.7·n^(2/3) 随 n
**单调增**，给不出中档峰值。

所以要么深度另有一条律，要么「峰峰」这个统计量本身不可靠。本脚本同时排除
后者，办法是把三个量并排报：

1. **正弦最小二乘幅度**（而非峰峰）—— 峰峰是两个极值之差，只要有一个离群点
   就被抬高；96 tap 的采样又不保证正好落在波峰波谷上，会**系统性低估**。
   幅度由全部 tap 联合定出，两个毛病都没有。
2. **拟合残差** —— 若某档残差突然变大，说明那里不止一个调制成分（例如第二个
   速率不同的 LFO，两者拍频会让「峰峰」随观测窗漂移），那时非单调是假象。
3. **速率** —— 若各档速率一致（前测已提示 1.7023 全程不变），可排除拍频。

密度也加上去：11 个 norm 点，覆盖 100…1100 ms 全程。

用法：
    python3 tools/measure/ref_delay_lfo_depth.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_lfo import (  # noqa: E402
    BASE, SPACING, SR, centroids, fine_rate, onset, train,
)

TAPS = 96             # 96 × 83.33 ms = 8 s ≈ 13.6 个 LFO 周期
NORMS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def sine_fit(c: np.ndarray, rate: float) -> tuple[float, float, float]:
    """返回 (幅度, 初相 deg, 残差/信号)。"""
    d = np.nan_to_num(c - np.nanmean(c))
    t = np.arange(len(d)) / (SR / SPACING)
    ph = 2 * np.pi * rate * t
    A = np.column_stack([np.sin(ph), np.cos(ph)])
    coef, *_ = np.linalg.lstsq(A, d, rcond=None)
    fit = A @ coef
    amp = float(np.hypot(*coef))
    res = float(np.linalg.norm(d - fit) / (np.linalg.norm(d) + 1e-30))
    return amp, float(np.degrees(np.arctan2(coef[1], coef[0]))), res


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    n = 2 * SR + TAPS * SPACING + 6 * SR

    hdr("深度 vs Delay Time（11 点；幅度用正弦最小二乘，非峰峰）")
    print(f"  {'norm':>6} {'显示 ms':>9} {'起点':>7} {'幅度':>8} {'峰峰':>8} "
          f"{'残差':>8} {'速率 Hz':>9} {'初相°':>8} {'幅度/起点':>10}")

    rows = []
    for nv in NORMS:
        p = dict(BASE)
        p.update({"delay_time_l": nv, "delay_time_r": nv})
        y = r.render(train(n, 2 * SR, TAPS), p)[0]
        off = onset(y, 2 * SR)
        c = centroids(y, 2 * SR, TAPS, off)
        rate, _ = fine_rate(c, SR / SPACING)
        amp, ph, res = sine_fit(c, rate)
        pp = float(np.nanmax(c) - np.nanmin(c))
        ms = V.delay_time_ms(nv)
        print(f"  {nv:6.2f} {ms:9.2f} {off:7d} {amp:8.4f} {pp:8.4f} "
              f"{res * 100:7.3f}% {rate:9.5f} {ph:+8.2f} {amp / (off + 1e-30) * 100:9.4f}%")
        rows.append((nv, ms, off, amp, res, rate, ph))

    # ------------------------------------------------------------ 律的候选检验
    hdr("候选律：哪条能同时对上 11 个点")
    nv = np.array([r0[0] for r0 in rows])
    ms = np.array([r0[1] for r0 in rows])
    off = np.array([r0[2] for r0 in rows], dtype=float)
    amp = np.array([r0[3] for r0 in rows])

    def report(name: str, pred: np.ndarray) -> None:
        # 允许一个整体比例（我们关心形状是否对，不关心单位）
        s = float(np.dot(pred, amp) / (np.dot(pred, pred) + 1e-30))
        rel = np.abs(s * pred - amp) / (amp + 1e-30)
        print(f"  {name:<34} 比例 {s:11.5g}  最差相对偏差 {rel.max() * 100:7.2f}%"
              f"  {'✓' if rel.max() < 0.05 else ''}")

    report("恒定样点数（加在延迟上）", np.ones_like(amp))
    report("∝ 延迟样点数（乘在延迟上）", off)
    report("∝ 归一参数的导数 n^(2/3)", np.maximum(nv, 1e-9) ** (2.0 / 3.0))
    report("∝ n·(1−n)", nv * (1.0 - nv))
    report("∝ sqrt(延迟) ", np.sqrt(off))
    report("∝ 延迟 × (1−n)", off * (1.0 - nv))
    report("∝ n^(2/3)·(1−n)", np.maximum(nv, 1e-9) ** (2.0 / 3.0) * (1.0 - nv))
    report("∝ ms 的对数 log(ms/100)", np.log(ms / 100.0) + 1e-9)

    hdr("判读")
    print("  残差列全部很小且速率一致 ⇒ 只有一个 LFO，非单调是真的，不是拍频假象。")
    print("  若无候选律通过，说明深度不是延迟时长的函数 —— 下一步要查它是否与")
    print("  Sync Note / tempo 或某个内部固定线长有关（即 LFO 调的不是这条延迟线）。")


if __name__ == "__main__":
    main()
