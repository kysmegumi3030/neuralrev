"""LFO 的**连续**测量：正弦相位解调，48 kHz 全速率看调制波形本身。

## 为什么换仪器

冲激列已经把三件事测干净，但也走到了它的极限：

* 速率 **1.70235 Hz** 是真的（4000/3000/2500/2000 四种采样率给出的值散布
  仅 3e-5 Hz；若是别名会随采样率变）；
* 深度也是真的（同一档在四种采样率下重现到三位数：6.4797/6.4845/6.4744/6.4887）；
* 但深度随 Delay Time **非单调振荡**（3.31 / 5.09 / 6.47 / 5.18 / 2.11 / 5.78 / 2.57
  样点，对应 100…1100 ms），没有任何一条自然的律能对上（最差偏差 106%…145%）。

冲激列的问题在于它只在**离散时刻**采样调制，且每次采样都要靠回声质心间接
反推。要看清机制，需要直接看调制波形。

## 仪器：正弦相位解调

`delay_drywet=1.0` + `delay_feedback=0` ⇒ 输出就是被延迟的输入，没有干声、
没有多次绕环。送一个频率 f 的稳态正弦，输出是

    y(t) = A·sin( 2πf·(t − D(t)/SR) + φ )

于是**瞬时相位偏差**直接给出延迟的调制量：

    δD(t) = −(瞬时相位 − 2πf·t) · SR / (2πf)     样点

用解析信号（FFT 实现的 Hilbert）取瞬时相位，`np.unwrap` 展开。这个仪器的
好处是**调制波形以 48 kHz 被完整采样** —— 速率、深度、形状、初相一次全出，
不存在欠采样，也不依赖任何窗口/质心的选择。

选 f = 1000 Hz：深度 ~6.5 样点 ⇒ 相位偏差 2π·1000·6.5/48000 = 0.85 rad，
远小于 π（不会缠绕）；且 1 kHz 在 `delay_lowpass=1.0`（16 kHz）通带内。

**仍必须小幅度**（AMP=1e-3）：延迟段有静态奇对称饱和，满幅会生成 H3/H5，
污染瞬时相位。

## 这个仪器能回答冲激列答不了的问题

* 调制是**单频正弦**还是含谐波 / 多个成分（看 δD 的频谱，不只看峰）；
* 深度的振荡是不是**两个同频、不同相**的调制分量叠加（那样 δD 仍是单频，
  但幅度随两者相对权重变）；
* 各档的**初相**是否落在同一条直线上（若相位随延迟线性变化，说明相位参考
  点在读指针而非写指针）。

用法：
    python3 tools/measure/ref_delay_lfo_demod.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
AMP = 1e-3
FCAR = 1000.0         # 载波（1 kHz，lowpass 通带内）
LEAD = 2.0            # 起步淡入之后
DUR = 12.0            # 稳态时长 ⇒ ~20 个 LFO 周期
NORMS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)

BASE = {
    "delay_drywet":   1.0,    # 纯湿：输出只有延迟支路
    "delay_feedback": 0.0,    # 单次通过：相位偏差 = 一次延迟的调制量
    "delay_lowpass":  1.0,
    "delay_highpass": 0.0,
}


def analytic_phase(x: np.ndarray) -> np.ndarray:
    """解析信号的瞬时相位（FFT 实现，避免依赖 scipy）。"""
    n = len(x)
    X = np.fft.fft(x)
    h = np.zeros(n)
    h[0] = 1.0
    if n % 2 == 0:
        h[n // 2] = 1.0
        h[1:n // 2] = 2.0
    else:
        h[1:(n + 1) // 2] = 2.0
    z = np.fft.ifft(X * h)
    return np.unwrap(np.angle(z))


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def measure(r, nv: float) -> dict:
    n = int((LEAD + DUR + 2.0) * SR)
    t = np.arange(n) / SR
    x = (AMP * np.sin(2 * np.pi * FCAR * t)).astype(np.float32)
    # 起步淡入期内不激励：前 LEAD 秒静音
    x[: int(LEAD * SR)] = 0.0

    p = dict(BASE)
    p.update({"delay_time_l": nv, "delay_time_r": nv})
    y = r.render(x, p)[0]

    # 稳态段：跳过激励起点 + 延迟 + 建立时间
    d0 = int(round(V.delay_time_ms(nv) * SR / 1000.0))
    a = int(LEAD * SR) + d0 + 4096
    b = a + int((DUR - 1.0) * SR)
    b = min(b, len(y))
    seg = y[a:b].astype(np.float64)

    ph = analytic_phase(seg)
    tt = np.arange(len(seg)) / SR
    # 去掉载波与常数延迟（一次多项式），剩下的就是调制
    co = np.polyfit(tt, ph, 1)
    dev = ph - np.polyval(co, tt)
    dD = -dev * SR / (2 * np.pi * FCAR)      # 样点

    # 调制的谱（去均值 + Hann + 零填充）
    m = dD - dD.mean()
    nfft = 1 << 20
    M = np.abs(np.fft.rfft(m * np.hanning(len(m)), nfft))
    f = np.fft.rfftfreq(nfft, 1.0 / SR)
    lim = f <= 200.0
    i = int(np.argmax(M[lim]))
    f0 = float(f[i])
    # 抛物线插值细化
    if 1 <= i < len(M) - 1:
        y0, y1, y2 = M[i - 1], M[i], M[i + 1]
        den = y0 - 2 * y1 + y2
        f0 += (0.5 * (y0 - y2) / den if den != 0 else 0.0) * (f[1] - f[0])

    # 基频处的幅度与相位（最小二乘，锁定 f0）
    A = np.column_stack([np.sin(2 * np.pi * f0 * tt), np.cos(2 * np.pi * f0 * tt)])
    coef, *_ = np.linalg.lstsq(A, m, rcond=None)
    amp = float(np.hypot(*coef))
    phase = float(np.degrees(np.arctan2(coef[1], coef[0])))
    res = float(np.linalg.norm(m - A @ coef) / (np.linalg.norm(m) + 1e-30))

    # 谐波含量：2f0 / 3f0 相对基频
    def at(fq: float) -> float:
        A2 = np.column_stack([np.sin(2 * np.pi * fq * tt), np.cos(2 * np.pi * fq * tt)])
        c2, *_ = np.linalg.lstsq(A2, m, rcond=None)
        return float(np.hypot(*c2))

    h2, h3 = at(2 * f0) / (amp + 1e-30), at(3 * f0) / (amp + 1e-30)

    return dict(nv=nv, d0=d0, f0=f0, amp=amp, phase=phase, res=res,
                h2=h2, h3=h3, pp=float(dD.max() - dD.min()), mean=float(dD.mean()))


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")

    hdr("正弦相位解调：调制波形的速率 / 深度 / 谐波 / 初相")
    print(f"  {'norm':>6} {'ms':>8} {'D 样点':>8} {'速率 Hz':>9} {'幅度':>8} "
          f"{'峰峰':>8} {'残差':>8} {'H2':>7} {'H3':>7} {'初相°':>8}")
    rows = []
    for nv in NORMS:
        q = measure(r, nv)
        print(f"  {nv:6.2f} {V.delay_time_ms(nv):8.1f} {q['d0']:8d} "
              f"{q['f0']:9.5f} {q['amp']:8.4f} {q['pp']:8.4f} "
              f"{q['res'] * 100:7.3f}% {q['h2'] * 100:6.2f}% {q['h3'] * 100:6.2f}% "
              f"{q['phase']:+8.2f}")
        rows.append(q)

    hdr("初相是否随延迟线性变（相位参考点在读指针还是写指针）")
    d0 = np.array([q["d0"] for q in rows], dtype=float)
    ph = np.array([q["phase"] for q in rows])
    f0 = float(np.mean([q["f0"] for q in rows]))
    # 若相位参考在写指针，读出时刻晚 D 样点 ⇒ 相位多走 2π f D / SR
    pred = np.degrees(2 * np.pi * f0 * d0 / SR)
    resid = (ph - pred + 180.0) % 360.0 - 180.0
    print(f"  LFO 平均速率 = {f0:.5f} Hz")
    print(f"  {'norm':>6} {'D':>8} {'实测相位':>10} {'2πfD/SR':>10} {'残差':>10}")
    for q, p0, rr in zip(rows, pred, resid):
        print(f"  {q['nv']:6.2f} {q['d0']:8d} {q['phase']:+10.2f} "
              f"{p0 % 360.0:10.2f} {rr:+10.2f}")
    print(f"  残差标准差 = {np.std(resid):.2f}°"
          f"   {'✓ 相位锚在写指针' if np.std(resid) < 20 else '✗ 不是这个关系'}")

    hdr("深度律：用连续解调的幅度重跑")
    amp = np.array([q["amp"] for q in rows])
    nv = np.array([q["nv"] for q in rows])

    def report(name: str, pred2: np.ndarray) -> None:
        s = float(np.dot(pred2, amp) / (np.dot(pred2, pred2) + 1e-30))
        rel = np.abs(s * pred2 - amp) / (amp + 1e-30)
        print(f"  {name:<28} 比例 {s:11.5g}  最差相对偏差 {rel.max() * 100:7.2f}%"
              f"  {'✓' if rel.max() < 0.05 else ''}")

    report("恒定样点数", np.ones_like(amp))
    report("∝ D", d0)
    report("∝ sqrt(D)", np.sqrt(d0))
    report("∝ n^(2/3)", np.maximum(nv, 1e-9) ** (2.0 / 3.0))

    hdr("判读")
    print("  残差 / H2 / H3 都很小 ⇒ 调制是单频正弦，深度振荡不是波形失真造的。")
    print("  若深度仍无律可循，实现上就用**实测查表 + 插值**，不强行拟合закрытая形式。")


if __name__ == "__main__":
    main()
