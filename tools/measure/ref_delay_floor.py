"""延迟段的**可达下界**：把参考插件与它自己比。

为什么需要这个脚本（不能直接照搬用户给的 1e-3 / 3 dB 口径）：

`ref_delay_probe.py` 第 4 节实测到延迟段是**线性时变**的 —— 环内延迟线被一个
约 1.71 Hz 的 LFO 调制（回声能量恒定 5.48e-07 而质心随激励位置平滑漂移
246.85 → 246.04 样点，见 §14.3）。LFO 的**初相不可观测**，所以任何独立实现
都不可能逐样点对上参考：这不是实现精度问题，是信息不足。

混响那轮已经踩过同一个坑并确立了做法（REFERENCE §10 / §10.1）：
**先把参考与它自己比**（同插件、同参数，只挪激励位置 ⇒ 只差 LFO 相位），
得到一个谁都突破不了的地板，再用它判断用户口径在哪些量上是有意义的。

与混响那轮的两点不同：

1. **必须在线性区激励。** 延迟段还有一个静态奇对称饱和（amp≤0.03 时
   增益恒 0.432732，amp=1.0 掉到 0.402685；1 kHz 正弦只出 H3/H5，
   THD 0.03% → 12.5%）。用满幅冲激测「时变」会把饱和的非线性混进来 ——
   probe 里 5.8e-02 的齐次性误差就是它，与 LFO 无关。故本脚本一律用
   `AMP = 1e-3`。
2. **地板要按「回声是否重叠」分层报。** 延迟的能量集中在离散回声上，
   位移小于回声宽度时两条 IR 还在同一个回声上比，位移大了就变成
   比不同 LFO 相位下的**不同**回声，nrmse 会跳一个台阶。

用法：
    python3 tools/measure/ref_delay_floor.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
IMP_AT = 2.0          # 绕开起步淡入（probe 第 2 节：≥0.2 s 才完整通过）
DUR = 6.0
AMP = 1e-3            # **必须在线性区**（见模块文档第 1 点）
NFFT = 65536          # 用户口径指定的 FFT 长度

SHIFTS = (1, 16, 48, 480, 4800)

# 验收口径要看的频带（与混响 ref_band_floor.py 同一套边界，便于横向对照）
BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000), (2000, 20000)]

PARAMS = {
    "delay_drywet":   1.0,     # 纯湿，避免干声把误差摊平
    "delay_feedback": 0.5,     # 显示 0.25，出厂默认；要有多次绕环才看得到累积相位
    "delay_time_l":   0.4,     # 显示 317.15 ms
    "delay_time_r":   0.6,     # L≠R，同时覆盖两个通道的独立 LFO
    "delay_lowpass":  1.0,
    "delay_highpass": 0.0,
}


def impulse(n: int, at: int) -> np.ndarray:
    x = np.zeros(n, dtype=np.float32)
    x[at] = AMP
    return x


def smooth_frac_oct(mag: np.ndarray, freqs: np.ndarray, frac: float = 1.0 / 12.0) -> np.ndarray:
    """1/frac 倍频程几何平滑（对 LFO 相位不敏感的那个口径）。"""
    out = np.empty_like(mag)
    r = 2.0 ** (frac / 2.0)
    for i, f in enumerate(freqs):
        if f <= 0:
            out[i] = mag[i]
            continue
        lo, hi = np.searchsorted(freqs, f / r), np.searchsorted(freqs, f * r)
        hi = max(hi, lo + 1)
        out[i] = np.mean(mag[lo:hi])
    return out


def spec_db(y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    seg = y[:NFFT] if len(y) >= NFFT else np.pad(y, (0, NFFT - len(y)))
    m = np.abs(np.fft.rfft(seg.astype(np.float64) * np.hanning(NFFT)))
    return np.fft.rfftfreq(NFFT, 1.0 / SR), 20.0 * np.log10(m + 1e-30)


def hdr(t: str) -> None:
    print(f"\n{'=' * 72}\n{t}\n{'=' * 72}")


def main() -> None:
    n = int(DUR * SR)
    at = int(IMP_AT * SR)
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")

    base = r.render(impulse(n, at), PARAMS)[0]

    hdr("参考与自身比：只差 LFO 相位（激励幅度 1e-3，线性区内）")
    print(f"  {'位移':>8} {'波形 max|Δ|':>14} {'nrmse':>10} "
          f"{'原始逐 bin max':>16} {'平滑逐 bin max':>16} {'>3dB 占比':>11}")

    rows = []
    for sh in SHIFTS:
        other = r.render(impulse(n, at + sh), PARAMS)[0]
        m = min(len(base) - at, len(other) - at - sh)
        u, v = base[at:at + m], other[at + sh:at + sh + m]

        dev = float(np.max(np.abs(u - v)))
        nrmse = float(np.linalg.norm(u - v) / (np.linalg.norm(u) + 1e-30))

        f, a = spec_db(u)
        _, b = spec_db(v)
        # 只在有能量的 bin 上比（−80 dB 门限，与混响那轮同口径）
        keep = (f >= 20) & (f <= 20000) & (a > a.max() - 80.0)
        raw = np.abs(a - b)
        sa, sb = smooth_frac_oct(10 ** (a / 20.0), f), smooth_frac_oct(10 ** (b / 20.0), f)
        sm = np.abs(20.0 * np.log10(sa + 1e-30) - 20.0 * np.log10(sb + 1e-30))

        rmax = float(raw[keep].max())
        smax = float(sm[keep].max())
        over = float(np.mean(raw[keep] > 3.0) * 100.0)
        print(f"  {sh:8d} {dev:14.3e} {nrmse * 100:9.4f}% "
              f"{rmax:16.2f} {smax:16.2f} {over:10.2f}%")
        rows.append((sh, f, sm, keep))

    hdr("逐频带的可达下界（平滑口径，取最差位移）")
    print(f"  {'频带':>14} " + " ".join(f"{sh:>9}" for sh in SHIFTS) + f" {'下界':>9}")
    for lo, hi in BANDS:
        vals = []
        for sh, f, sm, keep in rows:
            sel = keep & (f >= lo) & (f < hi)
            vals.append(float(sm[sel].max()) if sel.any() else float("nan"))
        lab = f"{lo}-{hi} Hz"
        print(f"  {lab:>14} " + " ".join(f"{v:9.2f}" for v in vals)
              + f" {np.nanmax(vals):9.2f}")

    hdr("判读")
    print("  波形 1e-3：看第一列。若参考自比本身就超 1e-3，该口径对独立实现不可达。")
    print("  原始逐 bin 3 dB：看第四列，同理。")
    print("  平滑逐 bin 3 dB：看第五列与逐带表，这是混响那轮采用的口径。")


if __name__ == "__main__":
    main()
