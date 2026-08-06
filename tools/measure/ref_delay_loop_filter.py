"""环内滤波器的真面目：把每圈损耗 L(f) 从反馈标量里解出来并定阶。

## 为什么必须单独测它

反馈标量已经定死：**0.80**，且按 1/50 量化（`ref_delay_fb3.py`，量化模型
胜连续模型 43060 倍）。于是窄带猝发的逐圈比值给出

    r(f) = 0.80 · L(f)      ⇒      L(f) = r(f) / 0.80

L(f) 就是**每绕一圈的滤波器损耗**，这是候选侧必须复制的东西 ——
逐 bin ≤3 dB 的口径下，8 kHz 上 −8 dB 的偏差是致命的。

## 已有 12 点提出的疑问

LP 名义开到 16 kHz、HP 压到 20 Hz 时，L(f) 仍然是（dB）：

    100Hz  350   1000   2000   3000   5000   8000
    -0.00  0.00  -0.137 -0.524 -1.141 -2.979 -8.052

**一个 16 kHz 的低通不可能在 8 kHz 上吃掉 8 dB。** 所以环内还有一个
**固定**滤波器（用户 LP 之外）。它的阶数可以从形状读出来：

* 一阶：由 8 kHz 反解 fc = 3447 Hz，但由 5 kHz 反解 fc = 5036 Hz —— 不自洽；
* 两级一阶串联：分别给出 6472 / 7817 Hz —— 仍不自洽。

两个都不对，说明曲线比任何低阶 IIR **更陡**。而实测的 dB 损耗近似 ∝ f²
（1→2 kHz 涨 3.8 倍、2→4 kHz 涨 3.8 倍，正是 f² 的倍率）。
dB ∝ f² 是**分数延迟内插器**的特征，不是极点滤波器的特征 ——
混响那轮已经踩过这个坑（§7.5 / §10.2.2：线性插值的损耗被误当成 damping）。

## 三个判据

1. **可分离性**：若 L(f) = LP_user(f) · Fixed(f)，则在不同 LP 档上
   L(f)/L_16k(f) 应当**只**是用户 LP 的比值，与固定部分无关。
   扫 4 个 LP 档，看这个商是否与 LP 的名义拐点自洽。
2. **定阶**：对 L_16k(f) 拟合三个候选并比较最差偏差：
   * 一阶 / 二阶 / 四阶低通（极点型）；
   * 线性插值在 frac 上的均值（|H|² = 1 − (1/3)(1−cos ω)）；
   * 「每圈 N 次线性插值」——若延迟线上不止一处插值，损耗按 N 次幂累积。
   哪个能同时对上 100 Hz…16 kHz 才算定下来。
3. **HP 端**：50 Hz 上已见 −0.381 dB 而 100 Hz 为 0.000 —— 那是 HP 在
   20 Hz 档的残余。扫 HP 档确认它是几阶。

## 判读的用处

L(f) 定下来后，候选侧的环内滤波器就有了唯一落点；同时它解释了
「4 kHz 上读反馈偏低」这个一开始被当成噪声的现象。

用法：
    python3 tools/measure/ref_delay_loop_filter.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_fb import AT, BURST, NT, SR, band_amp, burst  # noqa: E402

FB_MAX = 0.80         # 已定死的环内反馈上限（ref_delay_fb3.py）

# 对数频率栅格。上限 16 kHz：再高猝发的能量已被压到读不出。
FREQS = (100.0, 200.0, 350.0, 500.0, 700.0, 1000.0, 1500.0, 2000.0,
         3000.0, 4000.0, 5000.0, 6000.0, 8000.0, 10000.0, 12000.0)
# 用户 LP 的四档（显示 1.0 / 4.2 / 8.3 / 16.0 kHz）
LPS = (0.0, 0.4, 0.7, 1.0)
# HP 的三档（显示 20 / 129 / 800 Hz），只在低频读
HPS = (0.0, 0.3, 1.0)
HP_FREQS = (30.0, 50.0, 80.0, 120.0, 200.0, 350.0, 700.0)


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def loop_gain(r, f: float, d: int, n: int, lp: float, hp: float) -> float:
    """逐圈比值 r(f)。反馈固定 norm=1.0 ⇒ 系数恒为 FB_MAX。"""
    p = {"delay_time_l": NT, "delay_time_r": NT, "delay_drywet": 1.0,
         "delay_lowpass": lp, "delay_highpass": hp, "delay_feedback": 1.0}
    y = r.render(burst(n, AT, f), p)[0]
    amps = []
    for k in range(0, 7):
        c = AT + k * d
        a, b = c - 300, c + BURST + 300
        if b > len(y):
            break
        amps.append(band_amp(y[a:b], f))
    rs = [amps[k] / amps[k - 1] for k in range(2, len(amps)) if amps[k - 1] > 1e-20]
    return float(np.mean(rs)) if rs else float("nan")


def db(x) -> np.ndarray:
    return 20.0 * np.log10(np.asarray(x) + 1e-30)


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    n = 10 * SR
    D = int(round(V.delay_time_ms(NT) * SR / 1000.0))

    # ---------------------------------------------------------------- LP 扫描
    hdr("每圈损耗 L(f) = r(f)/0.80，四个 LP 档")
    print(f"  {'频率':>7} " + " ".join(f"{'LP=%.1f' % v:>9}" for v in LPS)
          + "   （dB，相对 0）")
    tab = {}
    for f in FREQS:
        row = [loop_gain(r, f, D, n, lp, 0.0) / FB_MAX for lp in LPS]
        tab[f] = row
        print(f"  {f:7.0f} " + " ".join(f"{v:9.4f}" for v in db(row)))

    # 判据 1：可分离性
    hdr("判据 1：可分离性 —— L(f;LP) / L(f;16k) 是否只由用户 LP 决定")
    print("  若可分离，此商 = LP_user(f) 的比值，且与固定部分无关。")
    print(f"  {'频率':>7} " + " ".join(f"{'LP=%.1f' % v:>10}" for v in LPS[:-1])
          + f"   {'名义 fc':>10}")
    for f in FREQS:
        row = tab[f]
        q = [row[i] / (row[-1] + 1e-30) for i in range(len(LPS) - 1)]
        print(f"  {f:7.0f} " + " ".join(f"{v:10.4f}" for v in db(q)))
    print("  名义拐点: " + "  ".join(f"LP={v:.1f}→{V.delay_lowpass_hz(v):.0f} Hz"
                                     for v in LPS))

    # 判据 2：定阶
    hdr("判据 2：给 L(f;LP=1.0) 定阶（四个候选模型，各自最小二乘）")
    f = np.array(FREQS)
    y = np.array([tab[q][-1] for q in FREQS])       # 幅度（线性）
    w = 2.0 * np.pi * f / SR                        # 数字角频率

    def fit_report(name: str, model, lo: float, hi: float, grid=4000):
        """单参数模型：扫参数取最差 dB 偏差最小者。"""
        best = None
        for p in np.linspace(lo, hi, grid):
            m = model(p)
            e = np.abs(db(m) - db(y))
            if best is None or e.max() < best[1]:
                best = (float(p), float(e.max()), m)
        p, worst, m = best
        print(f"\n  {name}   最优参数 = {p:.4f}   最差偏差 = {worst:.3f} dB")
        print(f"    {'频率':>7} {'实测 dB':>9} {'模型 dB':>9} {'差':>8}")
        for q, a0, m0 in zip(FREQS, db(y), db(m)):
            print(f"    {q:7.0f} {a0:9.4f} {m0:9.4f} {m0 - a0:+8.4f}")
        return worst

    # (a) 一阶低通
    w1 = fit_report("一阶低通 1/sqrt(1+(f/fc)²)",
                    lambda fc: 1.0 / np.sqrt(1.0 + (f / fc) ** 2),
                    500.0, 40000.0)
    # (b) 二阶（两级一阶串联）
    w2 = fit_report("两级一阶串联 1/(1+(f/fc)²)",
                    lambda fc: 1.0 / (1.0 + (f / fc) ** 2),
                    500.0, 60000.0)
    # (c) 线性插值 frac 均值：|H|² = 1 − (2/6)(1−cos w)，允许 N 次累积
    def lin_interp(nrep):
        h2 = 1.0 - (1.0 / 3.0) * (1.0 - np.cos(w))
        return np.power(np.maximum(h2, 1e-12), 0.5 * nrep)
    w3 = fit_report("线性插值 frac 均值，N 次累积", lin_interp, 0.2, 40.0)
    # (d) 线性插值固定 frac d（不平均）：|H|² = 1−2d(1−d)(1−cos w)
    def lin_fixed(d):
        h2 = 1.0 - 2.0 * d * (1.0 - d) * (1.0 - np.cos(w))
        return np.sqrt(np.maximum(h2, 1e-12))
    w4 = fit_report("线性插值固定 frac d", lin_fixed, 0.0, 0.5)

    print(f"\n  最差偏差汇总：一阶 {w1:.3f} / 两级 {w2:.3f} / "
          f"插值×N {w3:.3f} / 插值固定 d {w4:.3f} dB")
    print("  ⇒ 谁最小谁是机制；若都 >1 dB，说明是几件事的乘积，需联立。")

    # ---------------------------------------------------------------- HP 扫描
    hdr("判据 3：HP 端的阶数")
    print(f"  {'频率':>7} " + " ".join(f"{'HP=%.1f' % v:>9}" for v in HPS)
          + "   （dB）")
    for f0 in HP_FREQS:
        row = [loop_gain(r, f0, D, n, 1.0, hp) / FB_MAX for hp in HPS]
        print(f"  {f0:7.0f} " + " ".join(f"{v:9.4f}" for v in db(row)))
    print("  名义拐点: " + "  ".join(f"HP={v:.1f}→{V.delay_highpass_hz(v):.0f} Hz"
                                     for v in HPS))
    print("  一阶 HP 在 fc/2 处 −7.0 dB、fc/4 处 −12.3 dB；二阶各为 −14 / −24.6 dB。")


if __name__ == "__main__":
    main()
