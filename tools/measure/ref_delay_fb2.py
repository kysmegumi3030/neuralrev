"""反馈系数的最终定值：找到环内损耗的**极小点**，在那里读。

## 上一轮剩下的偏倚

`ref_delay_fb.py` 用窄带猝发读出的比值，在三个频点上并不一致（fb norm=1.0）：

    250 Hz   1000 Hz   4000 Hz
    0.7924    0.7840    0.6355     ← 4 kHz 明显偏低

而反馈系数必须是频率无关的标量。三点不一致 ⇒ **即使 LP 开到 16 kHz、HP 开到
20 Hz，环内滤波器在 4 kHz 上仍有可观损耗**。单回声的谱也早就提示了这一点
（§14 第 3 节：4 kHz −5.50 dB、8 kHz −11.45 dB，相对 50 Hz）。

好消息是 250 Hz 与 1 kHz 只差 1.07%，说明损耗在中低频有个平台。要定出真值，
就得找到损耗的**极小点**并在那里读 —— 而不是随便挑一个「看起来在通带里」的频点。

## 办法

1. **扫频**读比值：50…8000 Hz 取 12 个点，比值随频率的曲线本身就是「反馈 ×
   每圈损耗」的形状。曲线的**极大值**就是损耗最小处，那里的读数最接近纯反馈
   （损耗只会让比值变小，不会变大 ⇒ 极大值是下确界最紧的估计）。
2. **外推**：单圈损耗 L(f) 可从曲线形状估出。若比值 = fb·L(f)，且在极小损耗
   区 L→1，则极大值 ≈ fb。同时报「用二次曲线在极大点附近拟合的顶点值」，
   避免采样点没落在真正的极大处。
3. **交叉验证**：把 HP 推到最低（20 Hz）、LP 推到最高（16 kHz）已经做了；
   这里再加一条独立路径 —— **拿两个不同 fb 的读数相除**。若比值 = fb·L(f)，
   则同一频点上两个 fb 的读数之比 = fb1/fb2，**L(f) 被消掉**。这条不依赖
   「找到 L=1 的频点」，是最干净的。

第 3 条是本脚本的主结论来源；1、2 用来确认量级。

用法：
    python3 tools/measure/ref_delay_fb2.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_fb import AT, BURST, NT, SR, band_amp, burst  # noqa: E402

SWEEP = (50.0, 100.0, 200.0, 350.0, 500.0, 700.0, 1000.0,
         1500.0, 2000.0, 3000.0, 5000.0, 8000.0)
FBS = (0.25, 0.5, 0.75, 1.0)


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def ratios(r, f: float, fb: float, d: int, n: int) -> float:
    p = {"delay_time_l": NT, "delay_time_r": NT, "delay_drywet": 1.0,
         "delay_lowpass": 1.0, "delay_highpass": 0.0, "delay_feedback": fb}
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


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    n = 10 * SR
    D = int(round(V.delay_time_ms(NT) * SR / 1000.0))

    hdr("扫频读比值：曲线的极大处 = 环内损耗最小处")
    print(f"  {'频率':>7} " + " ".join(f"{'fb=%.2f' % b:>10}" for b in FBS))
    tab = {}
    for f in SWEEP:
        row = [ratios(r, f, b, D, n) for b in FBS]
        tab[f] = row
        print(f"  {f:7.0f} " + " ".join(f"{v:10.5f}" for v in row))

    hdr("路径 A：极大值（含二次顶点细化）")
    for i, b in enumerate(FBS):
        ys = np.array([tab[f][i] for f in SWEEP])
        j = int(np.argmax(ys))
        vtx = ys[j]
        if 1 <= j < len(ys) - 1:
            lf = np.log(np.array(SWEEP))
            c = np.polyfit(lf[j - 1:j + 2], ys[j - 1:j + 2], 2)
            if c[0] < 0:
                vtx = float(np.polyval(c, -c[1] / (2 * c[0])))
        print(f"  fb={b:.2f}  极大 {ys[j]:.5f} @ {SWEEP[j]:.0f} Hz"
              f"   顶点细化 {vtx:.5f}   显示值 {0.5 * b:.4f}"
              f"   比 {vtx / (0.5 * b):.4f}")

    hdr("路径 B：两个 fb 的读数相除 —— 环内损耗 L(f) 被消掉（主结论）")
    print("  若比值 = fb·L(f)，则 r(fb_i)/r(fb_j) = fb_i/fb_j，与频率无关。")
    print(f"  {'频率':>7} " + " ".join(f"{'r%.2f/r1.0' % b:>12}" for b in FBS[:-1]))
    ref_i = len(FBS) - 1
    quot = {b: [] for b in FBS[:-1]}
    for f in SWEEP:
        row = tab[f]
        out = []
        for i, b in enumerate(FBS[:-1]):
            q = row[i] / (row[ref_i] + 1e-30)
            out.append(q)
            quot[b].append(q)
        print(f"  {f:7.0f} " + " ".join(f"{v:12.6f}" for v in out))

    print(f"\n  {'fb':>6} {'比值均值':>11} {'std':>9} {'norm 之比':>11} {'偏差':>9}")
    for b in FBS[:-1]:
        v = np.array(quot[b])
        exp = b / FBS[ref_i]
        print(f"  {b:6.2f} {v.mean():11.6f} {v.std():9.6f} {exp:11.6f} "
              f"{(v.mean() - exp) / exp * 100:+8.3f}%")
    print("\n  ⇒ 若各 fb 的比值均等于 norm 之比（偏差 <1%），则**反馈对 norm 严格线性**，")
    print("    且 r(1.0) = fb_max·L(f) ⇒ 只差一个与频率有关的因子 L。")

    hdr("路径 C：用 L(f) 的形状外推 f→损耗最小")
    # 单回声（fb=0）的谱给出「一次通过」的损耗形状；环内每圈同一形状
    # 用 fb=1.0 的比值除以其在极大处的值，得到归一化 L(f)
    ys = np.array([tab[f][ref_i] for f in SWEEP])
    j = int(np.argmax(ys))
    L = ys / ys[j]
    print(f"  {'频率':>7} {'r(fb=1)':>10} {'L 归一':>9} {'损耗 dB':>9}")
    for f, y0, l0 in zip(SWEEP, ys, L):
        print(f"  {f:7.0f} {y0:10.5f} {l0:9.5f} {20 * np.log10(l0 + 1e-30):9.3f}")
    print(f"\n  极大处 {SWEEP[j]:.0f} Hz 的读数 {ys[j]:.5f} 是 fb_max 的**下确界最紧估计**。")
    print(f"  显示上限 0.500 ⇒ 实测 {ys[j]:.4f}   比值 {ys[j] / 0.5:.4f}")


if __name__ == "__main__":
    main()
