"""1.7023 Hz 是真速率，还是**欠采样的别名**？—— 深度反常的另一个出口。

## 上一步排除了什么，剩下什么

`ref_delay_lfo_depth2.py` 已经把「估计量的错」排除干净：

* 回声 99% 能量宽 **172 样点**（随 Delay Time 几乎不变：174…183），远小于 400 窗；
* 宽窗（2400）与窄窗（400）的质心幅度比 **0.994…1.006** —— 没有截断损失；
* 完全独立的**互相关**估计量与质心一致到三位数。

所以「深度随 Delay Time 非单调振荡」是真的。那么错的是我对**LFO 调制对象**
的模型，而不是测量。

## 新的怀疑：采样率不够，1.7023 Hz 是别名

关键在于测法本身：冲激列间隔 `SPACING=4000` 样点 ⇒ 我对 LFO 的**采样率只有
12 Hz**，Nyquist 6 Hz。任何高于 6 Hz 的调制都会折叠下来。若真实速率是
f_true，测到的就是 |f_true − k·12| 对某个整数 k。1.7023 Hz 可以是

    f_true = 12k ± 1.7023  ⇒  10.30 / 13.70 / 22.30 / 25.70 / 34.30 …  Hz

的任意一个。这一条能顺带解释深度的振荡：真实相位在相邻 tap 间跑过大半个周期
时，回声波形在窗内被**不同相位的调制**扫过，测出来的等效幅度就依赖于
「延迟时长与真实周期的比值」—— 正是随档位振荡而速率恒定的形状。

## 判据：改变采样率，看测到的频率是否跟着变

别名的指纹很干净：**真频率不随采样率变，别名会变**。所以用三种 SPACING
（4000 / 3000 / 2500 ⇒ 12 / 16 / 19.2 Hz）测同一个设置：

* 若三次都给 1.7023 Hz ⇒ 真速率，深度反常另有原因；
* 若三次给出不同值 ⇒ 别名，用 |f − k·fs| 反解真频率。

反解时用**中国剩余式**的思路：真频率必须同时满足三个折叠关系，扫一遍候选
k 组合，找唯一自洽解。

用法：
    python3 tools/measure/ref_delay_lfo_alias.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_lfo import BASE, SR, centroids, onset  # noqa: E402

SPACINGS = (4000, 3000, 2500, 2000)   # ⇒ 12 / 16 / 19.2 / 24 Hz
SECONDS = 8.0                          # 每种采样率都测同样的物理时长
AMP = 1e-3
NORMS = (0.4, 0.6)                     # 一个高深度档、一个低深度档


def train_sp(n: int, start: int, taps: int, spacing: int) -> np.ndarray:
    x = np.zeros(n, dtype=np.float32)
    for k in range(taps):
        p = start + k * spacing
        if p < n:
            x[p] = AMP
    return x


def centroids_sp(y, start, taps, off, spacing):
    out = []
    for k in range(taps):
        a = start + k * spacing + off - 100
        seg = y[a:a + 400].astype(np.float64)
        e = seg * seg
        s = e.sum()
        out.append(float((np.arange(len(seg)) * e).sum() / s) if s > 0 else np.nan)
    return np.array(out)


def peak_freq(c: np.ndarray, fs: float) -> tuple[float, float]:
    d = np.nan_to_num(c - np.nanmean(c))
    nfft = 1 << 16
    m = np.abs(np.fft.rfft(d * np.hanning(len(d)), nfft))
    f = np.fft.rfftfreq(nfft, 1.0 / fs)
    i = int(np.argmax(m[1:]) + 1)
    if 1 <= i < len(m) - 1:
        a, b, cc = m[i - 1], m[i], m[i + 1]
        den = a - 2 * b + cc
        d2 = 0.5 * (a - cc) / den if den != 0 else 0.0
    else:
        d2 = 0.0
    # 幅度用 rms×sqrt(2)（与正弦幅度同量纲，不依赖拟合频率）
    amp = float(np.nanstd(d) * np.sqrt(2.0))
    return float(f[i] + d2 * (f[1] - f[0])), amp


def hdr(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")

    hdr("同一设置、四种 LFO 采样率：测到的频率变不变")
    print(f"  {'norm':>6} {'spacing':>8} {'采样率 Hz':>10} {'taps':>6} "
          f"{'测得 Hz':>9} {'幅度':>8}")
    got: dict[float, list[tuple[float, float, float]]] = {}
    for nv in NORMS:
        got[nv] = []
        for sp in SPACINGS:
            fs = SR / sp
            taps = int(SECONDS * fs)
            n = 2 * SR + taps * sp + 8 * SR
            p = dict(BASE)
            p.update({"delay_time_l": nv, "delay_time_r": nv})
            y = r.render(train_sp(n, 2 * SR, taps, sp), p)[0]
            off = onset(y, 2 * SR)
            c = centroids_sp(y, 2 * SR, taps, off, sp)
            f0, amp = peak_freq(c, fs)
            print(f"  {nv:6.2f} {sp:8d} {fs:10.2f} {taps:6d} {f0:9.5f} {amp:8.4f}")
            got[nv].append((fs, f0, amp))

    hdr("别名反解：真频率必须同时满足全部折叠关系")
    for nv, obs in got.items():
        spread = max(o[1] for o in obs) - min(o[1] for o in obs)
        print(f"\n  norm={nv}:  测得频率散布 = {spread:.5f} Hz")
        if spread < 0.02:
            print("    ⇒ 四种采样率一致 ⇒ **1.7023 Hz 是真速率**，不是别名。")
            continue
        print("    ⇒ 随采样率变 ⇒ 是别名。扫描自洽的真频率：")
        best = []
        for ftrue in np.arange(0.5, 200.0, 0.0005):
            err = 0.0
            for fs, f0, _ in obs:
                # ftrue 折叠到 [0, fs/2]
                m = ftrue % fs
                fold = min(m, fs - m)
                err = max(err, abs(fold - f0))
            if err < 0.01:
                best.append((ftrue, err))
        if best:
            for ftrue, err in best[:12]:
                print(f"      候选真频率 {ftrue:9.4f} Hz   最差折叠偏差 {err:.5f}")
        else:
            print("      0.5–200 Hz 内无自洽解 ⇒ 调制不是单频正弦。")

    hdr("判读")
    print("  若确认是别名，则之前所有「速率 1.7023 Hz」的结论都要改写，")
    print("  且深度振荡自然得到解释：相邻 tap 间真实相位跑过大半周期。")


if __name__ == "__main__":
    main()
