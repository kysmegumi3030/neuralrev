"""延迟段拓扑：回声形状、滤波器位置、反馈系数、饱和位置、立体声路由、混合律。

§14.8 的七个待测项，一次测完 —— 它们共用同一批渲染，分开测是浪费。

## 每一项的判据（为什么这样测才算证明）

**1. 反馈系数**：显示 0.00…0.50。是不是环内实际系数？直接读**逐次回声的能量比**。
   feedback=0 时只有一个回声 ⇒ 用它定回声的「形状模板」；feedback>0 时第 k 个
   回声与第 k−1 个的幅度比就是环内系数。§6.1 的教训（显示 fc 不是 −3 dB 点）
   要求这一条必须用信号读，不能照搬显示值。

**2. LP / HP 在环内还是环外**：判据是**逐次回声的谱是否累积变化**。
   环内 ⇒ 每绕一圈滤一次 ⇒ 第 k 个回声的谱 = 第 1 个的 k 次幂（dB 域线性递增）；
   环外（post）⇒ 所有回声被同一个滤波器滤一次 ⇒ 谱形状**相同**，只有幅度差。
   这是个定性判据，非常硬，不需要拟合。

**3. 回声形状**：feedback=0 的单回声就是「延迟线 + 内插 + 可能的滤波」的总冲激
   响应。99% 能量宽 172 样点太宽，不可能是纯分数延迟内插（那只有几个抽头）。
   看它的**谱**：若是一个低通的冲激响应，谱应当光滑单调；若是内插器拖尾，
   会在高频有特征的起伏。同时报**群延迟**。

**4. 饱和位置**：在延迟线前 / 环内 / 湿声总线。判据是**高幅度下逐次回声的
   幅度比**。若饱和在环内，每绕一圈压一次 ⇒ 比值随 k 递增（后面的回声压得轻，
   因为幅度已经小了）；若在环外总线，所有回声被同样压一次 ⇒ 比值与 k 无关；
   若在延迟线前（输入端），只压一次 ⇒ 与环外总线在单输入下不可分 —— 那要靠
   **干湿对比**再分（干声是否也被压）。

**5. 立体声路由 / Mono-Stereo**：L 单独激励，看 R 有没有输出（交叉馈送），
   以及 mode=Mono（0.0）与 Stereo（1.0）的差别。

**6. Dry/Wet 混合律**：drywet 扫描，读干声幅度与湿声幅度。线性 ⇒ (1−w, w)；
   等功率 ⇒ (cos, sin)。用干声（可直接量）定标最可靠。

**7. Sync Note 档位表**：21 档的实际延迟样点数，以及 100–1100 ms 截断。

**全程 amp=1e-3**（§14.4：amp≤0.03 才线性），唯独第 4 项刻意用大幅度。

用法：
    python3 tools/measure/ref_delay_topo.py
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
AMP = 1e-3
NT = 0.4              # 基准延迟档：317.15 ms ⇒ D ≈ 15223 样点


def imp(n: int, at: int, amp: float = AMP, ch: str = "both") -> np.ndarray:
    """冲激。ch: both / l / r ⇒ 返回 (2, n) 立体声。"""
    x = np.zeros((2, n), dtype=np.float32)
    if ch in ("both", "l"):
        x[0, at] = amp
    if ch in ("both", "r"):
        x[1, at] = amp
    return x


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def echo_peaks(y: np.ndarray, d: int, nmax: int = 6, half: int = 700) -> list:
    """前 nmax 个回声的 (峰值, 能量, 窗)。第 k 个回声中心 ≈ AT + k·d。"""
    out = []
    for k in range(1, nmax + 1):
        c = AT + k * d
        a, b = c - half, c + half
        if b > y.shape[-1]:
            break
        seg = y[a:b].astype(np.float64)
        out.append((float(np.max(np.abs(seg))), float(np.sum(seg * seg)), seg))
    return out


def spec(seg: np.ndarray, nfft: int = 8192) -> tuple:
    s = seg[:nfft] if len(seg) >= nfft else np.pad(seg, (0, nfft - len(seg)))
    m = np.abs(np.fft.rfft(s * np.hanning(len(s)), nfft))
    return np.fft.rfftfreq(nfft, 1.0 / SR), 20.0 * np.log10(m + 1e-30)


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    n = 8 * SR
    D = int(round(V.delay_time_ms(NT) * SR / 1000.0))
    base = {"delay_time_l": NT, "delay_time_r": NT, "delay_drywet": 1.0,
            "delay_lowpass": 1.0, "delay_highpass": 0.0}

    # ------------------------------------------------------------ 1. 反馈系数
    hdr("1. 环内反馈系数：显示值 vs 逐次回声的实测比")
    print(f"  D = {D} 样点   （显示 {V.delay_time_ms(NT):.2f} ms）")
    print(f"  {'norm':>6} {'显示 fb':>8} " + " ".join(f"{'E%d/E%d' % (k + 1, k):>9}"
                                                     for k in range(1, 5))
          + f" {'均值':>8} {'幅度比均值':>11}")
    for nv in (0.0, 0.25, 0.5, 0.75, 1.0):
        p = dict(base)
        p["delay_feedback"] = nv
        y = r.render(imp(n, AT), p)[0]
        pk = echo_peaks(y, D, nmax=6)
        er = [np.sqrt(pk[k][1] / pk[k - 1][1]) if pk[k - 1][1] > 0 else np.nan
              for k in range(1, min(5, len(pk)))]
        ar = [pk[k][0] / pk[k - 1][0] if pk[k - 1][0] > 0 else np.nan
              for k in range(1, min(5, len(pk)))]
        er = er + [np.nan] * (4 - len(er))
        print(f"  {nv:6.2f} {V.delay_feedback(nv):8.3f} "
              + " ".join(f"{v:9.5f}" for v in er)
              + f" {np.nanmean(er):8.5f} {np.nanmean(ar):11.5f}")

    # ------------------------------------------------ 2. LP/HP 在环内还是环外
    hdr("2. LP / HP 的位置：逐次回声的谱是累积变化还是形状不变")
    for name, key, val in (("LOW PASS 0.30", "delay_lowpass", 0.30),
                           ("HIGH PASS 0.60", "delay_highpass", 0.60)):
        p = dict(base)
        p["delay_feedback"] = 1.0        # 显示 0.50，绕环次数最多
        p[key] = val
        y = r.render(imp(n, AT), p)[0]
        pk = echo_peaks(y, D, nmax=4)
        print(f"\n  {name}（{'LP fc=%.0f Hz' % V.delay_lowpass_hz(val) if 'low' in key else 'HP fc=%.0f Hz' % V.delay_highpass_hz(val)}）")
        f, a1 = spec(pk[0][2])
        print(f"    {'频率':>8} " + " ".join(f"{'回声%d' % (k + 1):>9}" for k in range(len(pk)))
              + "   （各回声已归一到自身峰值；环内 ⇒ 逐次累积）")
        specs = [spec(q[2])[1] for q in pk]
        specs = [s - s.max() for s in specs]
        for fq in (100, 250, 500, 1000, 2000, 4000, 8000, 12000):
            i = int(np.argmin(np.abs(f - fq)))
            print(f"    {fq:8d} " + " ".join(f"{s[i]:9.2f}" for s in specs))

    # ------------------------------------------------------------ 3. 回声形状
    hdr("3. 单回声（feedback=0）的形状与谱")
    p = dict(base)
    p["delay_feedback"] = 0.0
    y = r.render(imp(n, AT), p)[0]
    seg = y[AT + D - 700:AT + D + 700].astype(np.float64)
    e = seg * seg
    cs = np.cumsum(e) / (e.sum() + 1e-30)
    i50 = int(np.searchsorted(cs, 0.5))
    print(f"  峰值 = {np.max(np.abs(seg)):.6e}   （输入 {AMP}）"
          f"   增益 = {np.max(np.abs(seg)) / AMP:.6f}")
    print(f"  能量宽度: 99% = {np.searchsorted(cs, 0.995) - np.searchsorted(cs, 0.005)} 样点, "
          f"99.9% = {np.searchsorted(cs, 0.9995) - np.searchsorted(cs, 0.0005)}")
    print(f"  峰位相对窗首 = {int(np.argmax(np.abs(seg)))}   能量中位 = {i50}")
    f, a = spec(seg)
    a = a - a.max()
    print(f"  {'频率':>8} {'相对电平 dB':>12}")
    for fq in (50, 100, 250, 500, 1000, 2000, 4000, 8000, 12000, 16000, 20000):
        i = int(np.argmin(np.abs(f - fq)))
        print(f"  {fq:8d} {a[i]:12.2f}")

    # -------------------------------------------------------- 4. 饱和的位置
    hdr("4. 饱和在哪一环：高幅度下逐次回声的幅度比 vs k")
    print(f"  {'amp':>8} " + " ".join(f"{'A%d/A%d' % (k + 1, k):>9}" for k in range(1, 5))
          + "   （环内 ⇒ 比值随 k 递增；环外 ⇒ 与 k 无关）")
    for amp in (1e-3, 1e-2, 0.1, 0.5, 1.0):
        p = dict(base)
        p["delay_feedback"] = 1.0
        y = r.render(imp(n, AT, amp), p)[0]
        pk = echo_peaks(y, D, nmax=6)
        ar = [pk[k][0] / pk[k - 1][0] if pk[k - 1][0] > 0 else np.nan
              for k in range(1, min(5, len(pk)))]
        ar = ar + [np.nan] * (4 - len(ar))
        print(f"  {amp:8.3f} " + " ".join(f"{v:9.5f}" for v in ar))

    # 干声是否也被饱和（分离「输入端」与「湿声总线」）
    print("\n  干声是否被饱和（drywet=0 ⇒ 纯干）：")
    for amp in (1e-3, 0.5, 1.0):
        p = dict(base)
        p["delay_drywet"] = 0.0
        y = r.render(imp(n, AT, amp), p)[0]
        pkv = float(np.max(np.abs(y[AT:AT + 200])))
        print(f"    amp={amp:6.3f}  干声峰值 = {pkv:.6e}   增益 = {pkv / amp:.6f}")

    # -------------------------------------------------------- 5. 立体声路由
    hdr("5. 立体声路由：交叉馈送与 Mono/Stereo")
    for mode, mname in ((0.0, "Mono"), (1.0, "Stereo")):
        for ch in ("l", "r"):
            p = dict(base)
            p["delay_feedback"] = 0.0
            p["delay_mode"] = mode
            p["delay_time_l"] = 0.4
            p["delay_time_r"] = 0.6
            y = r.render(imp(n, AT, ch=ch), p)
            dl = int(round(V.delay_time_ms(0.4) * SR / 1000.0))
            dr = int(round(V.delay_time_ms(0.6) * SR / 1000.0))
            pl = float(np.max(np.abs(y[0, AT + dl - 700:AT + dl + 700])))
            pr = float(np.max(np.abs(y[1, AT + dr - 700:AT + dr + 700])))
            # 也看对侧在自己延迟处有没有能量
            xl = float(np.max(np.abs(y[1, AT + dl - 700:AT + dl + 700])))
            xr = float(np.max(np.abs(y[0, AT + dr - 700:AT + dr + 700])))
            print(f"  {mname:7s} 激励 {ch.upper()}:  L@Dl={pl:.3e}  R@Dr={pr:.3e}"
                  f"   R@Dl={xl:.3e}  L@Dr={xr:.3e}")

    # -------------------------------------------------------- 6. Dry/Wet 律
    hdr("6. Dry/Wet 混合律：线性还是等功率")
    print(f"  {'norm':>6} {'干声增益':>10} {'湿声峰值':>12} {'干/(1−w)':>11} "
          f"{'干/cos':>10} {'湿 归一':>10}")
    wetpk = {}
    for nv in (0.0, 0.25, 0.5, 0.75, 1.0):
        p = dict(base)
        p["delay_feedback"] = 0.0
        p["delay_drywet"] = nv
        y = r.render(imp(n, AT), p)[0]
        dry = float(np.max(np.abs(y[AT:AT + 200])))
        wet = float(np.max(np.abs(y[AT + D - 700:AT + D + 700])))
        wetpk[nv] = wet
        dg = dry / AMP
        lin = dg / (1.0 - nv) if nv < 1.0 else float("nan")
        eqp = dg / np.cos(nv * np.pi / 2) if nv < 1.0 else float("nan")
        print(f"  {nv:6.2f} {dg:10.6f} {wet:12.4e} {lin:11.6f} {eqp:10.6f} "
              f"{wet / (wetpk[1.0] + 1e-30) if 1.0 in wetpk else float('nan'):10.6f}")

    # ------------------------------------------------------- 7. Sync Note 表
    hdr("7. Sync Note 档位的实际延迟（tempo=120 BPM, sync 见 §14.2）")
    print(f"  {'idx':>4} {'档位':>7} {'norm':>6} {'实测样点':>10} {'实测 ms':>10} "
          f"{'理论 ms@120':>12}")
    p0 = {"delay_drywet": 1.0, "delay_feedback": 0.0,
          V.DELAY_PARAMS["delay_note_ms"]: 0.0}   # Note 路径
    beat = 60000.0 / 120.0
    frac = {"1/64T": 1 / 64 * 2 / 3, "1/64": 1 / 64, "1/32T": 1 / 32 * 2 / 3,
            "1/64D": 1 / 64 * 1.5, "1/32": 1 / 32, "1/16T": 1 / 16 * 2 / 3,
            "1/32D": 1 / 32 * 1.5, "1/16": 1 / 16, "1/8T": 1 / 8 * 2 / 3,
            "1/16D": 1 / 16 * 1.5, "1/8": 1 / 8, "1/4T": 1 / 4 * 2 / 3,
            "1/8D": 1 / 8 * 1.5, "1/4": 1 / 4, "1/2T": 1 / 2 * 2 / 3,
            "1/4D": 1 / 4 * 1.5, "1/2": 1 / 2, "1/1T": 1 * 2 / 3,
            "1/2D": 1 / 2 * 1.5, "1/1": 1.0, "1/1D": 1.5}
    for i, lab in enumerate(V.DELAY_SYNC_NOTES):
        nv = i / (len(V.DELAY_SYNC_NOTES) - 1)
        p = dict(p0)
        p["delay_syncnote_l"] = nv
        p["delay_syncnote_r"] = nv
        y = r.render(imp(n, AT), p)[0]
        thr = float(np.max(np.abs(y[AT:]))) * 0.01
        idx = np.where(np.abs(y[AT:]) > thr)[0]
        got = int(idx[0]) if len(idx) else -1
        th = frac[lab] * beat * 4.0     # 1/4 音符 = 1 拍 ⇒ 全音符 = 4 拍
        print(f"  {i:4d} {lab:>7} {nv:6.2f} {got:10d} {got * 1000.0 / SR:10.3f} "
              f"{th:12.3f}")


if __name__ == "__main__":
    main()
