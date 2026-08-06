"""延迟段 LFO 的完整刻画 —— 以及它**能否被一个常数对齐**。

## 为什么这个脚本是延迟工作的闸门

三个地板脚本（`ref_delay_floor*.py`）把验收口径的问题收窄成了一个：

| 位移（= LFO 相位误差的代理） | 原始逐 bin max @ −40 dB | 相对波形 max\|Δ\| |
|---|---|---|
| 1 样点   | **0.49 dB** | 1.46e-03 |
| 16 样点  | 1.97 dB | 1.12e-02 |
| 48 样点  | 2.35 dB | 2.11e-02 |
| 480 样点 | 8.57 dB | 1.64e-01 |
| 4800 样点| 23.73 dB | 1.84e-01 |

即：**只要 LFO 相位对齐到 ±48 样点以内，用户原始口径（逐 bin ≤3 dB）就是可达的**
—— 这与混响段截然不同（混响自比在 1 样点位移下最差 bin 已 8.2 dB，见 REFERENCE
§10，无论怎么对齐都过不了，只能退到平滑谱）。延迟不必放宽口径。

但这个结论**有一个前提**：LFO 的相位得是可对齐的。相位不可直接观测，只能靠
拟合一个常数补上；而「拟合一个常数」成立需要三件事同时为真：

1. **确定性** —— 同参数重复渲染，相位轨迹必须逐点一致（否则常数无意义）；
2. **固定原点** —— 相位由某个可复现的时间原点决定（渲染起点 / setActive），
   而不是进程启动时刻或随机种子；
3. **相位精度可达** —— LFO 周期 ~585 ms ≈ 28070 样点，对齐到 ±48 样点
   = 周期的 **0.17%**。这是个一维标量，扫描可达，但精度要求写下来才知道多紧。

第 2 条是最容易想漏、也最容易致命的一条：如果相位锚在「进程启动」而非
「渲染起点」，那它在我们的实现里根本无法复现（我们的插件在宿主里连续运行，
没有「进程启动」这个事件）。所以本脚本用**同一激励在不同渲染长度 / 不同
前导长度下的相位**来分离这两种可能。

## 测法：冲激列 + 质心

单次渲染里放一列冲激（间隔 4000 样点 = 83.33 ms），每个冲激的回声质心给出
LFO 在该时刻的一次采样。`delay_drywet=1.0` + `delay_feedback=0` ⇒ 输出里只有
回声、每个冲激恰好一个、间隔 4000 远大于回声宽度（~360）⇒ 互不重叠。
这比「N 次独立渲染各测一个相位」快 N 倍（N 次渲染实测 2 分钟超时）。

**必须 AMP=1e-3**：延迟段有静态奇对称饱和（amp≤0.03 线性，amp=1.0 增益从
0.432732 掉到 0.402685），满幅激励会把非线性混进质心。

用法：
    python3 tools/measure/ref_delay_lfo.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
AMP = 1e-3            # 线性区（见模块文档）
SPACING = 4000        # 冲激间隔（样点）= 83.33 ms，LFO 采样率 12 Hz
WIN = 400             # 回声质心窗宽（回声实测宽 ~360 样点）
GUARD = 100           # 窗起点相对回声起点的提前量

BASE = {
    "delay_drywet":   1.0,   # 纯湿：输出里只有回声
    "delay_feedback": 0.0,   # 每个冲激恰好一个回声，互不重叠
    "delay_lowpass":  1.0,
    "delay_highpass": 0.0,
}


def train(n: int, start: int, taps: int) -> np.ndarray:
    """冲激列：start 起，每 SPACING 一个，共 taps 个。"""
    x = np.zeros(n, dtype=np.float32)
    for k in range(taps):
        p = start + k * SPACING
        if p < n:
            x[p] = AMP
    return x


def onset(y: np.ndarray, start: int) -> int:
    """第一个回声的起点（相对 start）：峰值 1% 门限。"""
    seg = y[start:]
    thr = float(np.max(np.abs(seg))) * 0.01
    idx = np.where(np.abs(seg) > thr)[0]
    return int(idx[0]) if len(idx) else -1


def centroids(y: np.ndarray, start: int, taps: int, off: int) -> np.ndarray:
    """每个 tap 的回声能量质心（相对各自窗起点，样点）。"""
    out = []
    for k in range(taps):
        a = start + k * SPACING + off - GUARD
        seg = y[a:a + WIN].astype(np.float64)
        e = seg * seg
        s = e.sum()
        out.append(float((np.arange(len(seg)) * e).sum() / s) if s > 0 else np.nan)
    return np.array(out)


def fine_rate(c: np.ndarray, fs: float) -> tuple[float, float]:
    """去均值后零填充 DFT 找峰，抛物线插值细化 —— 返回 (Hz, 相对调制深度)。"""
    d = c - np.nanmean(c)
    d = np.nan_to_num(d)
    nfft = 1 << 16
    m = np.abs(np.fft.rfft(d * np.hanning(len(d)), nfft))
    f = np.fft.rfftfreq(nfft, 1.0 / fs)
    i = int(np.argmax(m[1:]) + 1)
    # 抛物线插值（三点）细化峰位
    if 1 <= i < len(m) - 1:
        a, b, cc = m[i - 1], m[i], m[i + 1]
        den = a - 2 * b + cc
        delta = 0.5 * (a - cc) / den if den != 0 else 0.0
    else:
        delta = 0.0
    return float(f[i] + delta * (f[1] - f[0])), float(m[i])


def hdr(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def measure(r, n: int, start: int, taps: int, params: dict, ch: int = 0) -> np.ndarray:
    p = dict(BASE)
    p.update(params)
    y = r.render(train(n, start, taps), p)[ch]
    off = onset(y, start)
    return centroids(y, start, taps, off), off


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    P4 = {"delay_time_l": 0.4, "delay_time_r": 0.4}

    # ---------------------------------------------------- 1. 精确速率与形状
    hdr("1. 精确速率（20 s 冲激列 ⇒ 34 个 LFO 周期）")
    taps = 240
    n = 2 * SR + taps * SPACING + 4 * SR
    c, off = measure(r, n, 2 * SR, taps, P4)
    rate, _ = fine_rate(c, SR / SPACING)
    print(f"  回声起点 = {off} 样点   质心均值 = {np.nanmean(c):.4f}")
    print(f"  峰峰 = {np.nanmax(c) - np.nanmin(c):.4f} 样点   std = {np.nanstd(c):.4f}")
    print(f"  **速率 = {rate:.5f} Hz**   周期 = {SR / rate:.1f} 样点 = {1000 / rate:.2f} ms")
    print(f"  （混响段实测 1.71 Hz，REFERENCE §10）")

    # 形状：与同相位同幅度的正弦 / 三角比残差
    hdr("2. 波形形状：正弦还是三角")
    d = np.nan_to_num(c - np.nanmean(c))
    t = np.arange(len(d)) / (SR / SPACING)
    ph = 2 * np.pi * rate * t
    # 最小二乘拟合正弦（同时定相位与幅度）
    A = np.column_stack([np.sin(ph), np.cos(ph)])
    coef, *_ = np.linalg.lstsq(A, d, rcond=None)
    sin_fit = A @ coef
    amp = float(np.hypot(*coef))
    phase0 = float(np.arctan2(coef[1], coef[0]))
    # 三角波（同频同相同幅）
    tri = amp * (2.0 / np.pi) * np.arcsin(np.sin(ph + phase0))
    for name, fit in (("正弦", sin_fit), ("三角", tri)):
        res = float(np.linalg.norm(d - fit) / (np.linalg.norm(d) + 1e-30))
        print(f"  {name}拟合 残差/信号 = {res * 100:7.3f}%")
    print(f"  幅度 = ±{amp:.4f} 样点（峰峰 {2 * amp:.4f}）   初相 = {np.degrees(phase0):+.2f}°")

    # ---------------------------------------------------- 3. 确定性与相位原点
    hdr("3. 相位是否确定 / 锚在哪（决定能不能用一个常数对齐）")
    taps_s = 64
    n_s = 2 * SR + taps_s * SPACING + 4 * SR
    c_a, _ = measure(r, n_s, 2 * SR, taps_s, P4)
    c_b, _ = measure(r, n_s, 2 * SR, taps_s, P4)
    print(f"  重复渲染      max|Δ质心| = {np.nanmax(np.abs(c_a - c_b)):.3e}"
          f"   {'✓ 确定性' if np.nanmax(np.abs(c_a - c_b)) < 1e-9 else '✗ 不确定'}")

    # 更长的渲染（尾部加长）：若相位锚在渲染起点，前 64 tap 的轨迹应完全不变
    c_c, _ = measure(r, n_s + 4 * SR, 2 * SR, taps_s, P4)
    print(f"  渲染加长 4 s  max|Δ质心| = {np.nanmax(np.abs(c_a - c_c)):.3e}"
          f"   {'✓ 与总长无关' if np.nanmax(np.abs(c_a - c_c)) < 1e-9 else '✗ 依赖总长'}")

    # 前导加长：激励整体后移一整个 LFO 周期的整数倍 vs 半个周期
    per = SR / rate
    for label, shift in (("整周期", int(round(per))), ("半周期", int(round(per / 2)))):
        c_d, _ = measure(r, n_s + 2 * SR, 2 * SR + shift, taps_s, P4)
        dev = float(np.nanmax(np.abs(c_a - c_d)))
        # 若相位锚在渲染起点（绝对时间），整周期后移 ⇒ 轨迹重合；半周期 ⇒ 反相
        print(f"  前导 +{label}({shift:6d})  max|Δ质心| = {dev:8.4f} 样点"
              f"   {'✓ 重合' if dev < 0.5 else '✗ 不重合'}")

    # ---------------------------------------------------- 4. L / R 是否同相
    hdr("4. L / R 的 LFO：同相、反相，还是独立速率")
    taps_s = 96
    n_s = 2 * SR + taps_s * SPACING + 4 * SR
    cl, ol = measure(r, n_s, 2 * SR, taps_s, {"delay_time_l": 0.4, "delay_time_r": 0.4}, ch=0)
    cr, orr = measure(r, n_s, 2 * SR, taps_s, {"delay_time_l": 0.4, "delay_time_r": 0.4}, ch=1)
    rl, _ = fine_rate(cl, SR / SPACING)
    rr, _ = fine_rate(cr, SR / SPACING)
    print(f"  L: 起点 {ol:6d}  速率 {rl:.5f} Hz  峰峰 {np.nanmax(cl) - np.nanmin(cl):.4f}")
    print(f"  R: 起点 {orr:6d}  速率 {rr:.5f} Hz  峰峰 {np.nanmax(cr) - np.nanmin(cr):.4f}")
    dl = np.nan_to_num(cl - np.nanmean(cl))
    dr = np.nan_to_num(cr - np.nanmean(cr))
    corr = float(np.dot(dl, dr) / (np.linalg.norm(dl) * np.linalg.norm(dr) + 1e-30))
    # 互相关找滞后（以 tap 为单位，再折算成度）
    xc = np.correlate(dl, dr, mode="full")
    lag = int(np.argmax(np.abs(xc)) - (len(dr) - 1))
    deg = lag * SPACING / (SR / rl) * 360.0
    print(f"  corr(L,R) = {corr:+.6f}   滞后 = {lag} tap = {deg:+.1f}°")

    # ---------------------------------------------------- 5. 深度 vs 延迟时长
    hdr("5. 调制深度是否随 Delay Time 变（决定 LFO 是调时长还是调样点数）")
    print(f"  {'time norm':>10} {'显示 ms':>10} {'回声起点':>10} "
          f"{'峰峰':>9} {'速率 Hz':>9} {'相对深度':>10}")
    taps_s = 96
    n_s = 2 * SR + taps_s * SPACING + 4 * SR
    for nv in (0.0, 0.2, 0.4, 0.8, 1.0):
        cc, oo = measure(r, n_s, 2 * SR, taps_s, {"delay_time_l": nv, "delay_time_r": nv})
        rr2, _ = fine_rate(cc, SR / SPACING)
        pp = float(np.nanmax(cc) - np.nanmin(cc))
        ms = V.delay_time_ms(nv)
        print(f"  {nv:10.2f} {ms:10.2f} {oo:10d} {pp:9.4f} {rr2:9.5f} "
              f"{pp / (oo + 1e-30) * 100:9.4f}%")

    hdr("判读")
    print("  第 3 节全 ✓ ⇒ 相位可由一个常数对齐 ⇒ **用户原始口径（逐 bin ≤3 dB、")
    print("  波形 <1e-3）对延迟段是可达的**，不必像混响那样退到平滑谱。")
    print("  第 5 节若「相对深度」恒定 ⇒ LFO 乘在延迟样点数上；若「峰峰」恒定 ⇒ 加在其上。")


if __name__ == "__main__":
    main()
