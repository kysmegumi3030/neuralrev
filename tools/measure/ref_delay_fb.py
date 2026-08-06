"""环内反馈系数：把它从**环内滤波器的损耗**里分离出来。

## 为什么上一轮的读数不能直接用

`ref_delay_topo.py` 第 1 节按逐次回声的能量比读反馈，得到（显示 fb=0.500）：

    E2/E1   E3/E2   E4/E3   E5/E4
    0.671   0.718   0.739   0.751     ← 随 k **递增**，不是常数

一个纯标量反馈应当给出恒定比值。递增说明比值里混着**别的东西**，而第 2 节
已经查明那是什么：**LP 和 HP 都在环内**（HIGH PASS 在 100 Hz 上给出
−20.68 / −40.66 / −59.34 / −85.34 dB，即严格的 ×k 累积；LOW PASS 在 2 kHz 上
−6.68 / −9.72 / −14.71 / −17.48 dB）。

于是每绕一圈的能量损失 = **反馈系数 × 滤波器损耗**，而滤波器损耗依赖当时的
频谱形状 —— 谱越窄，后续每圈损失越小 ⇒ 比值随 k 递增。所以「能量比」这个
估计量把两个因子乘在了一起，读出来的既不是反馈也不是损耗。

## 分离办法：把滤波器开到「几乎不滤」

反馈系数是**频率无关**的标量，滤波器损耗**只在通带外**起作用。所以把
`delay_lowpass=1.0`（16 kHz）+ `delay_highpass=0.0`（20 Hz）—— 通带开到最宽 ——
再在**通带内的窄带**上读逐次回声的比值。此时滤波器在该带内近乎无损，
比值就是纯反馈。

具体做法：用**窄带激励**（1 kHz 正弦猝发，落在通带正中，远离两个截止频率），
读逐次回声的幅度比。相比冲激，猝发的能量全部集中在一个频点上，
不受滤波器形状影响 —— 这是关键。

三重交叉验证：

1. **多个频点**（250 / 1000 / 4000 Hz）：若读数一致 ⇒ 确认是频率无关的标量；
2. **多个 k**（前 6 个回声）：若比值随 k 恒定 ⇒ 确认滤波器损耗已被排除；
3. **多个延迟时长**：反馈系数不应随 D 变。

最后拟合 `norm → 系数` 的律，并与显示值 `0.5·norm` 对照 —— §6.1 的教训要求
必须问这一句：显示的 0.50 是不是环内真值。

用法：
    python3 tools/measure/ref_delay_fb.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
AT = 2 * SR
AMP = 1e-3            # 线性区（§14.4）
BURST = 2048          # 猝发长度（样点）—— 远短于 D，回声不重叠
NT = 0.4              # D ≈ 15223
FREQS = (250.0, 1000.0, 4000.0)
FBS = (0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0)


def burst(n: int, at: int, f: float) -> np.ndarray:
    """Hann 窗正弦猝发（窄带激励，能量集中在 f）。"""
    x = np.zeros((2, n), dtype=np.float32)
    t = np.arange(BURST) / SR
    w = np.hanning(BURST)
    s = (AMP * w * np.sin(2 * np.pi * f * t)).astype(np.float32)
    x[0, at:at + BURST] = s
    x[1, at:at + BURST] = s
    return x


def band_amp(seg: np.ndarray, f: float) -> float:
    """seg 在频率 f 上的幅度（最小二乘投影，抗邻带泄漏）。"""
    t = np.arange(len(seg)) / SR
    A = np.column_stack([np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t)])
    c, *_ = np.linalg.lstsq(A, seg.astype(np.float64), rcond=None)
    return float(np.hypot(*c))


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    n = 10 * SR
    D = int(round(V.delay_time_ms(NT) * SR / 1000.0))
    base = {"delay_time_l": NT, "delay_time_r": NT, "delay_drywet": 1.0,
            "delay_lowpass": 1.0, "delay_highpass": 0.0}

    hdr(f"窄带猝发读环内反馈（D = {D}，LP/HP 开到最宽 ⇒ 通带内近乎无损）")
    print(f"  {'fb norm':>8} {'显示':>7} {'频率':>7} "
          + " ".join(f"{'A%d/A%d' % (k + 1, k):>8}" for k in range(1, 6))
          + f" {'均值':>8} {'std':>8}")

    table = {}
    for nv in FBS:
        p = dict(base)
        p["delay_feedback"] = nv
        rows = []
        for f in FREQS:
            y = r.render(burst(n, AT, f), p)[0]
            amps = []
            for k in range(0, 7):
                c = AT + k * D
                a, b = c - 300, c + BURST + 300
                if b > len(y):
                    break
                amps.append(band_amp(y[a:b], f))
            ratios = [amps[k] / amps[k - 1] if amps[k - 1] > 1e-20 else np.nan
                      for k in range(2, min(7, len(amps)))]
            ratios = ratios + [np.nan] * (5 - len(ratios))
            mu = float(np.nanmean(ratios)) if nv > 0 else 0.0
            sd = float(np.nanstd(ratios)) if nv > 0 else 0.0
            print(f"  {nv:8.3f} {V.delay_feedback(nv):7.3f} {f:7.0f} "
                  + " ".join(f"{v:8.5f}" for v in ratios)
                  + f" {mu:8.5f} {sd:8.5f}")
            rows.append(mu)
        table[nv] = rows
        print()

    hdr("三个频点是否一致（判定反馈是频率无关的标量）")
    print(f"  {'fb norm':>8} " + " ".join(f"{'%.0f Hz' % f:>10}" for f in FREQS)
          + f" {'散布':>9}")
    for nv in FBS:
        v = table[nv]
        print(f"  {nv:8.3f} " + " ".join(f"{x:10.5f}" for x in v)
              + f" {max(v) - min(v):9.5f}")

    hdr("反馈的律：实测系数 vs 显示值 0.5·norm")
    nv = np.array(FBS)
    got = np.array([float(np.mean(table[k])) for k in FBS])
    disp = 0.5 * nv
    print(f"  {'norm':>8} {'显示':>9} {'实测':>9} {'实测/显示':>11} {'实测/norm':>11}")
    for a, b, c in zip(nv, disp, got):
        print(f"  {a:8.3f} {b:9.4f} {c:9.5f} "
              f"{c / (b + 1e-30) if b > 0 else float('nan'):11.5f} "
              f"{c / (a + 1e-30) if a > 0 else float('nan'):11.5f}")

    ok = nv > 0
    # 线性拟合（过原点）：got = s·norm
    s = float(np.dot(nv[ok], got[ok]) / np.dot(nv[ok], nv[ok]))
    rel = np.abs(s * nv[ok] - got[ok]) / (got[ok] + 1e-30)
    print(f"\n  过原点线性拟合: 系数 = {s:.6f}   最差相对偏差 = {rel.max() * 100:.3f}%"
          f"   {'✓ 线性' if rel.max() < 0.02 else '✗ 非线性'}")
    print(f"  显示上限 0.500 ⇒ 实测上限 {s:.4f}"
          f"   比值 {s / 0.5:.4f}")

    hdr("反馈是否随延迟时长变（应当不变）")
    print(f"  {'time norm':>10} {'D':>7} {'实测系数 @1 kHz':>16}")
    for tn in (0.0, 0.4, 0.8, 1.0):
        d2 = int(round(V.delay_time_ms(tn) * SR / 1000.0))
        p = dict(base)
        p["delay_feedback"] = 1.0
        p["delay_time_l"] = tn
        p["delay_time_r"] = tn
        y = r.render(burst(n, AT, 1000.0), p)[0]
        amps = []
        for k in range(0, 6):
            c = AT + k * d2
            a, b = c - 300, c + BURST + 300
            if b > len(y):
                break
            amps.append(band_amp(y[a:b], 1000.0))
        ratios = [amps[k] / amps[k - 1] for k in range(2, len(amps))
                  if amps[k - 1] > 1e-20]
        print(f"  {tn:10.2f} {d2:7d} {np.mean(ratios):16.5f}")


if __name__ == "__main__":
    main()
