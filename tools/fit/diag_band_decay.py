"""逐频带的**时间分辨**能量曲线：区分「电平错」与「衰减率错」。

为什么需要这个（用户验收反馈「中频偏多、高频偏少」）：
band_report.py 只给整段 IR 的频谱误差，那是一个**时间积分**量。
同一个 +2 dB 的中频均值误差，可以由两种完全不同的病因产生：

  A) **电平错**：候选的中频从 t=0 起就偏高，衰减率与参考一致。
     ⇒ 修法是总线上一级静态滤波（与 kFitTiltShelf 同类），代价可控。

  B) **衰减率错**：候选的高频衰减**过快**（环内 damping 低通过深）。
     ⇒ 此时误差**随时间增长**。用静态滤波去修是错的：静态滤波按
        时间积分后的平均量补偿，会让早期补过头、晚期仍然不够，
        且听感上尾巴的音色仍然会越来越暗。必须改 kFitDampingHz。

两者的判据很干净：把 IR 切成若干时间窗，逐窗算各带能量比
`20·log10(候选/参考)`。
  * 曲线**平坦** ⇒ 电平错（A）
  * 曲线**单调下行**（高频窗越晚越负）⇒ 衰减率错（B）
斜率的单位是 dB/s，可直接与 REFERENCE §5 的「超额衰减 dB/s」对照。

同时输出各带的 T60（候选 vs 参考），因为衰减率错的等价表述就是
「该带 T60 偏短」，这个数比 dB/s 更容易和 §7 的衰减律对上。

口径与其它脚本一致：参考走 VST3 原生宿主，候选走 nrev_render，
两边同一个冲激、同一个采样率、参考侧扣掉 51 样点固有延迟。

用法：python3 tools/fit/diag_band_decay.py [--decay 0.5]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
TAIL_SEC = 4.0

# 用 1/1 oct 的宽带，因为这里要的是**时间**分辨率，不是频率分辨率。
# 带太窄则每窗内的 bin 数不足，能量估计噪声会盖住斜率。
BANDS = [
    ("125 Hz", 88, 177),
    ("250 Hz", 177, 355),
    ("500 Hz", 355, 710),
    ("1 kHz", 710, 1420),
    ("2 kHz", 1420, 2840),
    ("4 kHz", 2840, 5680),
    ("8 kHz", 5680, 11360),
]

# 时间窗：前段密、后段疏（能量按指数衰减，晚窗需要更长才有足够信噪比）
WINDOWS = [(0.00, 0.05), (0.05, 0.15), (0.15, 0.30), (0.30, 0.50),
           (0.50, 0.80), (0.80, 1.20), (1.20, 2.00)]


def bandpass(x, lo, hi):
    """零相位带通（频域乘窗后逆变换）。

    用 FFT 而不是 IIR：这里要比较两条 IR 的**能量包络**，IIR 的相位
    响应会在起振段引入自身的瞬态，污染最早那个 5 ms 窗。
    """
    n = int(2 ** np.ceil(np.log2(len(x))))
    X = np.fft.rfft(x, n)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    X[(f < lo) | (f > hi)] = 0.0
    return np.fft.irfft(X, n)[:len(x)]


def band_t60(env_db, t, lo_db=-5.0, hi_db=-35.0):
    """在 [-5, -35] dB 段线性回归求 T60。

    掩码除电平外还必须**时间连通**：起点取峰值时刻、终点取首次跌破 hi_db 处。
    纯电平掩码会把**峰前起振段**（扩散级摊开冲激的那一段，电平同样落在
    −5…−35 dB 内）选进回归，把斜率压平 ⇒ T60 偏长；且候选侧起振更慢，
    被拉长得更多，实测比值到 1.83（参考侧 1.39）。这个不对称曾伪造出
    「随 DECAY 档变化的环内高频损耗」。完整推导见
    fit_damping_t60.band_t60 的文档串。
    """
    pk = env_db.max()
    ipk = int(np.argmax(env_db))
    m = (env_db <= pk + lo_db) & (env_db >= pk + hi_db)
    m[:ipk] = False
    below = np.where(env_db[ipk:] <= pk + hi_db)[0]
    if below.size:
        m[ipk + int(below[0]) + 1:] = False
    if m.sum() < 8:
        return float("nan"), float("nan")
    A = np.vstack([t[m], np.ones(m.sum())]).T
    slope, _ = np.linalg.lstsq(A, env_db[m], rcond=None)[0]
    if slope >= -1e-9:
        return float("nan"), slope
    return -60.0 / slope, slope


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decay", type=float, default=0.5)
    a = ap.parse_args()

    P = dict(drywet=1.0, predelay=0.5, decay=a.decay, lowcut=0.0, highcut=1.0)

    n = BASE_AT + int(TAIL_SEC * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0

    r = V.Vst3RefRenderer(sr=SR, block=512)
    ref = r.render(x, params={f"reverb_{k}": v for k, v in P.items()}
                   ).astype(np.float64)[0][BASE_AT + REF_LATENCY:]
    cand = NrevRenderer(sr=SR, block=512).render(
        x, params=P).astype(np.float64)[0][BASE_AT:]

    ln = min(len(ref), len(cand))
    ref, cand = ref[:ln], cand[:ln]

    print(f"档位 decay={a.decay}  （drywet=1 纯湿声）\n")
    print("① 逐带 / 逐时间窗的能量比 20·log10(候选/参考)，单位 dB")
    print("   平坦 ⇒ 电平错（总线静态滤波可修）")
    print("   随时间下行 ⇒ 该带衰减过快（须改环内 damping）\n")

    hdr = "".join(f"{lo:.2f}-{hi:.2f}s".rjust(11) for lo, hi in WINDOWS)
    print(f"{'频带':>8}{hdr}{'斜率':>11}")

    rows = []
    for name, lo, hi in BANDS:
        rb, cb = bandpass(ref, lo, hi), bandpass(cand, lo, hi)
        cells, mids, vals = [], [], []
        for w0, w1 in WINDOWS:
            i0, i1 = int(w0 * SR), int(w1 * SR)
            er = float(np.sum(rb[i0:i1] ** 2))
            ec = float(np.sum(cb[i0:i1] ** 2))
            if er <= 1e-24 or ec <= 1e-24:
                cells.append("     n/a")
                continue
            d = 10 * np.log10(ec / er)
            cells.append(f"{d:+11.2f}")
            mids.append(0.5 * (w0 + w1))
            vals.append(d)
        slope = float("nan")
        if len(vals) >= 3:
            A = np.vstack([np.array(mids), np.ones(len(mids))]).T
            slope = float(np.linalg.lstsq(A, np.array(vals), rcond=None)[0][0])
        print(f"{name:>8}{''.join(cells)}{slope:+10.2f}")
        rows.append((name, lo, hi, rb, cb, slope))

    print("\n   斜率单位 dB/s：负值＝候选该带衰减比参考快（越晚越吃亏）")

    print("\n② 逐带 T60（候选 vs 参考）")
    print(f"{'频带':>8}{'参考 T60':>11}{'候选 T60':>11}{'相对误差':>11}")
    for name, lo, hi, rb, cb, _ in rows:
        t = np.arange(ln) / SR
        # 包络：短窗 RMS，窗长取 T60/40 的量级（这里固定 20 ms 足够）
        w = int(0.020 * SR)
        k = np.ones(w) / w

        def env(y):
            e = np.sqrt(np.convolve(y ** 2, k, mode="same") + 1e-30)
            return 20 * np.log10(e)

        tr, _ = band_t60(env(rb), t)
        tc, _ = band_t60(env(cb), t)
        rel = (tc / tr - 1.0) * 100 if (tr == tr and tc == tc and tr > 0) else float("nan")
        print(f"{name:>8}{tr:11.3f}{tc:11.3f}{rel:+10.1f}%")

    print("\n③ 整段能量比（与 band_report.py 的均值项对应）")
    for name, lo, hi, rb, cb, _ in rows:
        er, ec = float(np.sum(rb ** 2)), float(np.sum(cb ** 2))
        if er > 1e-24 and ec > 1e-24:
            print(f"{name:>8}{10 * np.log10(ec / er):+10.2f} dB")


if __name__ == "__main__":
    main()
