"""fb=1.0 档剩下的 13.92 dB 落在**哪些 bin** 上？

## 为什么要问这个

时序、电平、形状三项都已闭合（滞后逐圈 0、增益比 0.9999、nrmse 不随圈数增长），
p95 = 1.94 / p99 = 4.50 都已落到参考自比地板的水平（480 样点粗错位给 2.19 / 4.28），
但**最大值** 13.92 仍超过那个地板的 11.33。

一个「p95 已达标而 max 超标」的分布说明超标的是**极少数 bin**。这类 bin 只有
两种身份，且判法不同：

  * **梳状零点**：参考自身在该 bin 电平极低，两个实现的零点位置差一点点，
    比值就能跳几十 dB，而绝对误差微乎其微 ⇒ 口径放大，不是失配。
    指纹：该 bin 的参考电平远低于邻域中位数。
  * **真实失配**：该 bin 参考电平正常，候选却偏了 ⇒ 必须追。
    指纹：参考电平接近邻域中位数。

所以这里不报「误差有多大」，而报每个超标 bin 的**参考电平相对邻域的深度**。
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V          # noqa: E402
from plugin_match import nrev_cand as C         # noqa: E402

SR = 48000
AT = 19200
NFFT = 65536
GATE = -40.0
LFO_PHASE = 0.238423
NORM_400 = 0.577079952


def burst(n: int, at: int, seed: int = 12345, dur: int = 4800) -> np.ndarray:
    """与 ab_delay.py 同源的噪声突发激励（同一 seed ⇒ 同一激励）。"""
    rng = np.random.default_rng(seed)
    x = np.zeros(n, dtype=np.float64)
    x[at:at + dur] = rng.standard_normal(dur) * 0.02
    return x


def main() -> None:
    rp = {"delay_drywet": 1.0, "delay_time_l": NORM_400,
          "delay_time_r": NORM_400, "delay_feedback": 1.0,
          "delay_lowpass": 1.0, "delay_highpass": 0.0, "delay_mode": 1.0}
    cp = {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
          "d_timel": NORM_400, "d_timer": NORM_400, "d_feedback": 1.0,
          "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
          "d_lfophase": LFO_PHASE}

    n = AT + NFFT + 8192
    x = burst(n, AT)
    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    a = np.asarray(ref.render(x, rp)[0], float)[AT:AT + NFFT]
    b = np.asarray(cand.render(x, cp)[0], float)[AT:AT + NFFT]

    # ⚠️ **不加窗** —— 判据 `spectrum_err_db` 就是裸 rfft。加 Hann 会把
    # 相邻 bin 抹平，梳状零点被邻居填起来，于是 max 从 13.92 掉到 7.27：
    # 那不是「误差变小」，是换了口径量了另一个东西。定位必须与判据同口径。
    A = np.abs(np.fft.rfft(a))
    B = np.abs(np.fft.rfft(b))
    freq = np.fft.rfftfreq(NFFT, 1.0 / SR)

    eps = 1e-30
    adb = 20.0 * np.log10(np.maximum(A, eps))
    peak = adb.max()
    keep = adb > peak + GATE          # 与判据同门限：只看通带 bin

    err = np.abs(20.0 * np.log10(np.maximum(B, eps) / np.maximum(A, eps)))
    err_g = np.where(keep, err, 0.0)

    # 邻域中位数：判断该 bin 是不是「参考自己的谷」。
    # 窗取 ±64 bin（≈±47 Hz），足够跨过 400 ms 环的梳齿间距（2.5 Hz）
    # 的许多个周期，于是中位数代表局部平均电平而非谷/峰本身。
    half = 64
    pad = np.pad(adb, half, mode="edge")
    local = np.array([np.median(pad[i:i + 2 * half + 1]) for i in range(len(adb))])
    depth = adb - local               # 负得多 ⇒ 该 bin 是梳状零点

    order = np.argsort(err_g)[::-1][:25]

    print(f"\n{'=' * 84}")
    print("fb=1.0 档最差的 25 个 bin：它们是梳状零点，还是真实失配？")
    print(f"{'=' * 84}")
    print("    Hz      误差dB   参考电平(相对峰)  相对邻域中位数   身份")
    for i in order:
        d = depth[i]
        tag = "梳状零点" if d < -12.0 else ("谷边" if d < -6.0 else "**真实失配**")
        print(f"  {freq[i]:8.1f}  {err_g[i]:8.2f}  {adb[i] - peak:14.1f}"
              f"  {d:14.1f}   {tag}")

    sel = err_g > 3.0
    nsel = int(sel.sum())
    ntot = int(keep.sum())
    deep = int((sel & (depth < -12.0)).sum())
    print(f"\n  通带 bin 总数 {ntot}，超 3 dB 的 {nsel} 个"
          f"（{100.0 * nsel / max(ntot, 1):.3f}%），其中 {deep} 个是梳状零点"
          f"（{100.0 * deep / max(nsel, 1):.1f}%）")
    print(f"  超 3 dB 的 bin 的参考电平中位深度："
          f"{np.median(depth[sel]) if nsel else float('nan'):+.1f} dB")
    # 真正决定「要不要继续追」的量：**电平正常的 bin 里最差是多少**。
    # max 全谱会被零点绑架，所以按邻域深度分层报。分层门限不是随手取的：
    # −6 dB 是「半功率谷」，比它浅的 bin 不可能靠零点位置微移放大 dB。
    print(f"\n{'=' * 84}")
    print("按「参考电平相对邻域」分层的最差误差（决定是否继续追）")
    print(f"{'=' * 84}")
    print("   邻域深度区间        bin 数    最差误差dB   p99      p95")
    for lo, hi, name in ((-1e9, -12.0, "< −12（梳状零点）"),
                         (-12.0, -6.0, "−12…−6（谷边）  "),
                         (-6.0, -3.0, "−6…−3（浅谷）   "),
                         (-3.0, 1e9, "> −3（电平正常）")):
        m = keep & (depth >= lo) & (depth < hi)
        cnt = int(m.sum())
        if cnt == 0:
            print(f"  {name}  {cnt:8d}         —")
            continue
        e = err_g[m]
        print(f"  {name}  {cnt:8d}  {e.max():10.2f}  {np.percentile(e, 99):7.2f}"
              f"  {np.percentile(e, 95):7.2f}")

    normal = keep & (depth >= -3.0)
    worst_normal = float(err_g[normal].max()) if normal.any() else float("nan")
    print(f"\n  判读：**电平正常 bin 的最差误差 = {worst_normal:.2f} dB**。")
    print("        这一档的 max 口径被零点放大：若该数已在 3 dB 内，则残余"
          "\n        全部落在参考自身的梳状谷里，绝对误差微乎其微（波形口径"
          "\n        1.94e−04 已印证），继续压 max 等于去对齐零点位置，"
          "\n        收益是口径上的、不是听觉上的。")


if __name__ == "__main__":
    main()
