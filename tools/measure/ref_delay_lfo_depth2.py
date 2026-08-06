"""深度律复测：先证明「非单调」是不是**质心窗截断**造出来的假象。

## 为什么怀疑估计量而不是先信结果

`ref_delay_lfo_depth.py` 的 11 点扫描里没有一条候选律能对上（最差相对偏差
112%…172%），而幅度序列是**振荡**而非趋势：

    norm   0.00  0.10  0.20  0.30  0.40  0.50  0.60  0.70  0.80  0.90  1.00
    幅度   3.31  3.93  5.09  6.19  6.47  5.22  2.10  2.18  5.75  6.21  2.56

物理上很难造出这种形状（速率全程锁死 1.7023 Hz，且 feedback=0 ⇒ 单次通过，
没有多次绕环的累积效应可言）。于是先查估计量本身。

**嫌疑点很具体**：`ref_delay_lfo.py` 的质心窗是 `WIN=400`、`GUARD=100`，
即覆盖 [onset−100, onset+300)；而回声实测宽约 **360 样点**。窗口只比回声宽
一点，LFO 一动就把回声尾巴推出窗外 —— 而截断带来的质心变化**与位移反向**
（尾巴被切 ⇒ 质心往前跑），于是与真实位移**部分相消**，测出来的幅度被压低，
且压低多少取决于回声在窗内的位置 ⇒ 随档位振荡。norm=1.00 的拟合残差
飙到 6.631%（其余 0.4%…3.2%）正是这种非线性失真的指纹。

## 复测怎么做

1. **先量回声宽度**随 Delay Time 变不变，把窗宽定在「远大于回声」上。
2. **换估计量**：不用质心，用**互相关求亚样点时移**。把各 tap 的回声与
   「全体 tap 的平均回声」对齐，用抛物线插值细化峰位。只要窗口完整包住回声，
   这个量对窗宽不敏感 —— 这正是质心不具备的性质。
3. **两个估计量并排报**（同一批渲染数据），差异本身就是判据：若宽窗下两者
   一致而窄窗下质心偏小 ⇒ 假象确认。

用法：
    python3 tools/measure/ref_delay_lfo_depth2.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_lfo import BASE, SPACING, SR, fine_rate, onset, train  # noqa: E402
from measure.ref_delay_lfo_depth import TAPS, sine_fit  # noqa: E402

WIDE = 2400           # 宽窗（远大于回声 ~360）
WIDE_GUARD = 600
NORMS = (0.0, 0.2, 0.4, 0.5, 0.6, 0.8, 1.0)


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def echo_width(y: np.ndarray, start: int, off: int) -> tuple[float, float]:
    """回声的能量宽度：返回 (含 99% 能量的宽度, 含 99.9% 的宽度)。"""
    seg = y[start + off - WIDE_GUARD:start + off - WIDE_GUARD + WIDE].astype(np.float64)
    e = seg * seg
    cs = np.cumsum(e) / (e.sum() + 1e-30)
    w99 = float(np.searchsorted(cs, 0.995) - np.searchsorted(cs, 0.005))
    w999 = float(np.searchsorted(cs, 0.9995) - np.searchsorted(cs, 0.0005))
    return w99, w999


def windows(y: np.ndarray, start: int, off: int, win: int, guard: int) -> np.ndarray:
    """各 tap 的回声窗，堆成 (taps, win)。"""
    out = []
    for k in range(TAPS):
        a = start + k * SPACING + off - guard
        s = y[a:a + win].astype(np.float64)
        if len(s) < win:
            s = np.pad(s, (0, win - len(s)))
        out.append(s)
    return np.array(out)


def centroid_of(w: np.ndarray) -> np.ndarray:
    e = w * w
    s = e.sum(axis=1)
    idx = np.arange(w.shape[1])
    return (e * idx).sum(axis=1) / (s + 1e-30)


def xcorr_shift(w: np.ndarray) -> np.ndarray:
    """各 tap 相对「平均回声」的亚样点时移（互相关 + 抛物线插值）。"""
    ref = w.mean(axis=0)
    ref = ref - ref.mean()
    n = w.shape[1]
    out = []
    for row in w:
        a = row - row.mean()
        xc = np.correlate(a, ref, mode="full")
        i = int(np.argmax(xc))
        if 1 <= i < len(xc) - 1:
            y0, y1, y2 = xc[i - 1], xc[i], xc[i + 1]
            den = y0 - 2 * y1 + y2
            d = 0.5 * (y0 - y2) / den if den != 0 else 0.0
        else:
            d = 0.0
        out.append((i + d) - (n - 1))
    return np.array(out)


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    n = 2 * SR + TAPS * SPACING + 8 * SR

    hdr("回声宽度 vs Delay Time（定窗宽的依据）")
    print(f"  {'norm':>6} {'显示 ms':>9} {'起点':>7} {'99% 宽':>9} {'99.9% 宽':>10}")
    ys = {}
    for nv in NORMS:
        p = dict(BASE)
        p.update({"delay_time_l": nv, "delay_time_r": nv})
        y = r.render(train(n, 2 * SR, TAPS), p)[0]
        off = onset(y, 2 * SR)
        ys[nv] = (y, off)
        w99, w999 = echo_width(y, 2 * SR, off)
        print(f"  {nv:6.2f} {V.delay_time_ms(nv):9.2f} {off:7d} {w99:9.0f} {w999:10.0f}")

    hdr("两个估计量 × 两种窗宽：非单调是真的还是窗截断造的")
    print(f"  {'norm':>6} {'窄窗质心':>10} {'宽窗质心':>10} {'宽窗互相关':>12} "
          f"{'窄/宽 比':>10} {'互相关残差':>11} {'速率 Hz':>9}")
    rows = []
    for nv in NORMS:
        y, off = ys[nv]
        wn = windows(y, 2 * SR, off, 400, 100)       # 原来的窄窗
        ww = windows(y, 2 * SR, off, WIDE, WIDE_GUARD)

        cn, cw = centroid_of(wn), centroid_of(ww)
        sx = xcorr_shift(ww)

        rate, _ = fine_rate(sx, SR / SPACING)
        an, _, _ = sine_fit(cn, rate)
        aw, _, _ = sine_fit(cw, rate)
        ax, phx, rx = sine_fit(sx, rate)
        print(f"  {nv:6.2f} {an:10.4f} {aw:10.4f} {ax:12.4f} "
              f"{an / (ax + 1e-30):10.3f} {rx * 100:10.3f}% {rate:9.5f}")
        rows.append((nv, ax, phx, rx))

    hdr("用宽窗互相关的幅度重跑候选律")
    nvv = np.array([q[0] for q in rows])
    amp = np.array([q[1] for q in rows])
    off = np.array([ys[q[0]][1] for q in rows], dtype=float)

    def report(name: str, pred: np.ndarray) -> None:
        s = float(np.dot(pred, amp) / (np.dot(pred, pred) + 1e-30))
        rel = np.abs(s * pred - amp) / (amp + 1e-30)
        print(f"  {name:<30} 比例 {s:11.5g}  最差相对偏差 {rel.max() * 100:7.2f}%"
              f"  {'✓' if rel.max() < 0.05 else ''}")

    report("恒定样点数", np.ones_like(amp))
    report("∝ 延迟样点数", off)
    report("∝ sqrt(延迟)", np.sqrt(off))
    report("∝ n^(2/3)", np.maximum(nvv, 1e-9) ** (2.0 / 3.0))

    hdr("判读")
    print("  若「窄/宽 比」明显 <1 且随档位振荡 ⇒ 原来的非单调是窗截断假象；")
    print("  此时以宽窗互相关那一列为准，再看哪条律通过。")


if __name__ == "__main__":
    main()
