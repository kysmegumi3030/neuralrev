"""DECAY 上端极限（norm → 1.0）的单独对拍。

为什么必须单独测：
  1. 参考在 norm=1.0 的 T60 至少 **526 s**（45 s 窗包络回归的下界，REFERENCE §7.1）。
     常规 4 s 的 IR 窗口里衰减量不到 1 dB，**测不出斜率**，
     所以主对拍脚本（abtest_reverb.py）刻意只到 decay=0.8。
     注意别用 EDC 读这一档：它随窗长漂移（20 s→47.8 s、25 s→60.2 s、
     45 s→526 s），漂移本身就是截断偏置的指纹，见 decay_slope 的文档串。
  2. 候选侧的「参数 → T60」律曾用 1/T60 线性拟合，它在 decaySec 7.7145 s
     （norm 0.9619）穿过零点，之后全靠钳位 —— 是**无效外推**。
     已由 `tools/fit/fit_decay_law.py` 换成幂律距上界形式
         1/T60 = 0.0840614·(8.075 − d)^1.1958
     本脚本负责验证换形式之后上端确实跟上了参考。
  3. 反馈趋近 1 时要确认候选**不发散**（g 只要 >1 就指数增长）。

本脚本做四件事：
  A) 长窗（LONG_SEC）测两侧的实际衰减斜率，逐档扫 norm ∈ 0.90…1.00；
  B) 稳定性：候选在 norm=1.0 长时间渲染后的包络是否单调不增（不发散）；
  C) 平滑谱对拍（主口径）在这些档位上的误差；
  D) 反解参考在该区间的真实 1/T60，给出比线性钳位更好的模型建议。

用法：python3 tools/measure/ref_decay_extreme.py [--long 20]
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "fit"))   # 复用 env_t60_auto

from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
NFFT = 65536
F = np.fft.rfftfreq(NFFT, 1.0 / SR)

# 扫描档位：只看上端，密一点（零点在 0.9619 附近）
LEVELS = [0.90, 0.94, 0.96, 0.98, 1.00]

# 拟合常数：**从 ReverbTuning.h 现读**，不写死。
# 写死过一次就会出现「脚本打印的模型 ≠ 插件实际用的模型」，
# 而这个脚本的全部意义就是检验模型，硬编码会让它自我欺骗。
def _read_tuning(name: str) -> float:
    import re
    path = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
    m = re.search(rf"{name}\s*=\s*([-\d.eE+]+)", open(path).read())
    if not m:
        raise KeyError(f"ReverbTuning.h 里找不到 {name}")
    return float(m.group(1))


INV_BOUND = _read_tuning("kMeasInvT60Bound")
INV_SCALE = _read_tuning("kMeasInvT60Scale")
INV_EXP = _read_tuning("kMeasInvT60Exponent")


def model_inv_t60(decay_sec: float) -> float:
    slack = INV_BOUND - decay_sec
    return INV_SCALE * (slack ** INV_EXP) if slack > 0 else 0.0


def smooth(y, of=1 / 12):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def params(decay):
    return dict(drywet=1.0, predelay=0.5, decay=decay, lowcut=0.0, highcut=1.0)


def render_ref(r, decay, long_sec):
    n = BASE_AT + int(long_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    p = params(decay)
    y = r.render(x, params={f"reverb_{k}": v for k, v in p.items()})
    return y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]


def render_cand(decay, long_sec):
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(long_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return c.render(x, params=params(decay)).astype(np.float64)[0][BASE_AT:]


def decay_slope(y, sr=SR):
    """衰减斜率（dB/s）与 T60，用 **RMS 包络回归**（与 fit_decay_law.py 同口径）。

    为什么不用 EDC：反向累积能量在窗末必然归零，尾巴比窗长的档位会出现
    人造膝点，T60 被系统性低估，且**结果随窗长漂移**（同一 norm=1.0 档，
    20 s 窗 47.8 s、25 s 窗 60.2 s、45 s 窗 526 s）。本脚本专测上端，
    正是该偏置最严重的区间，必须换成无截断偏置的包络法。

    返回 (斜率 dB/s, T60 秒, 回归段跨越的 dB)。
    跨度是可信度指标：< 25 dB 时 T60 只能当**下界**读。
    """
    from fit_decay_law import env_t60_auto                  # 单一实现，避免漂移
    t60, _nw, span, _win = env_t60_auto(y, sr=sr)
    if not np.isfinite(t60) or t60 <= 0:
        return float("nan"), float("nan"), 0.0
    return -60.0 / t60, t60, span


def env_db(y, win_sec=0.5, sr=SR):
    """逐 win_sec 的 RMS 包络（dB），用于看是否发散。"""
    w = int(win_sec * sr)
    n = len(y) // w
    out = []
    for i in range(n):
        seg = y[i * w:(i + 1) * w]
        out.append(20 * np.log10(max(np.sqrt(np.mean(seg ** 2)), 1e-30)))
    return np.array(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--long", type=float, default=20.0, help="长窗秒数")
    a = ap.parse_args()
    L = a.long

    r = V.Vst3RefRenderer(sr=SR, block=512)

    print(f"长窗 = {L:.0f} s（默认 4 s 窗在该区间测不出斜率）")
    print("口径 = RMS 包络回归（无 EDC 截断偏置）\n")
    print("A) 衰减斜率与 T60")
    print(f"{'norm':>6} {'参考T60':>10} {'跨度':>7} {'候选T60':>10} {'跨度':>7} "
          f"{'相对%':>8} {'模型T60':>10}")

    rows = []
    for v in LEVELS:
        yr = render_ref(r, v, L)
        yc = render_cand(v, L)
        kr, t60r, sr_span = decay_slope(yr)
        kc, t60c, sc_span = decay_slope(yc)
        decay_sec = 0.5 + 7.5 * v
        inv = model_inv_t60(decay_sec)
        mt60 = (1.0 / inv) if inv > 1e-4 else 1.0e4
        rows.append((v, kr, t60r, kc, t60c, inv, mt60, yr, yc))
        rel = 100.0 * (t60c / t60r - 1.0) if t60r > 0 else float("nan")
        # 「≥」直接贴在跨度不足的那个读数前面，避免读者把它当成整行的标记
        sr_s = ("≥" if sr_span < 25 else " ") + f"{t60r:9.2f}"
        sc_s = ("≥" if sc_span < 25 else " ") + f"{t60c:9.2f}"
        print(f"{v:6.2f} {sr_s} {sr_span:7.1f} {sc_s} {sc_span:7.1f} "
              f"{rel:+8.1f} {mt60:10.2f}")

    print("\n   T60 前带「≥」= 该读数的回归跨度 < 25 dB，窗内没跌够，只能当下界读；"
          "\n   两侧只要有一个是下界，该档的相对% 就只是量级参考，不是精度结论。")

    print("\nB) 稳定性（候选，norm=1.0）：逐 0.5 s 的 RMS 包络（dB）")
    yc1 = [row[8] for row in rows if abs(row[0] - 1.0) < 1e-9][0]
    e = env_db(yc1)
    step = max(1, len(e) // 12)
    print("   " + "  ".join(f"{v:.1f}" for v in e[::step]))
    rise = float(np.max(np.diff(e))) if len(e) > 1 else 0.0
    net = float(e[-1] - e[0])
    print(f"   包络最大单步上升 = {rise:+.3f} dB   末段−首段 = {net:+.2f} dB")
    print(f"   峰值 |y| = {np.max(np.abs(yc1)):.4f}   "
          f"有 NaN/Inf：{not np.all(np.isfinite(yc1))}")
    # 判据说明：单步上升**不能**单独当发散判据 —— 模式拍频会让 0.5 s 的 RMS
    # 窗出现零点几 dB 的起伏，这在衰减的混响里完全正常。
    # 发散的充要特征是**净增长**（末段高于首段）或数值失控。
    ok = net < 0.0 and np.all(np.isfinite(yc1)) and np.max(np.abs(yc1)) < 10.0
    if not ok:
        print("   ✗ 发散或数值异常")
    else:
        print(f"   ✓ 未发散（净衰减 {-net:.2f} dB；单步起伏 {rise:+.2f} dB "
              f"是模式拍频，非增长）")

    print("\nC) 主口径（1/12 oct 平滑谱）对拍")
    BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000), (2000, 20000)]
    print(f"{'norm':>6} {'全带max':>9} " +
          " ".join(f"{lo}-{hi}".rjust(11) for lo, hi in BANDS))
    for v, kr, t60r, kc, t60c, inv, mt60, yr, yc in rows:
        A, B = smooth(yr), smooth(yc)
        err = np.abs(20 * np.log10(np.maximum(B, 1e-30) / np.maximum(A, 1e-30)))
        m = (F >= 20) & (F <= 20000)
        cells = [f"{err[(F >= lo) & (F <= hi)].max():11.2f}" for lo, hi in BANDS]
        flag = "✓" if err[m].max() <= 3.0 else "✗"
        print(f"{v:6.2f} {err[m].max():8.2f}{flag} " + " ".join(cells))

    print("\nD) 反解参考在上端的真实 1/T60，检验幂律模型的偏差")
    print(f"{'norm':>6} {'decaySec':>9} {'实测1/T60':>11} {'幂律模型':>10} "
          f"{'偏差':>9} {'相对%':>8}")
    for v, kr, t60r, kc, t60c, inv, mt60, yr, yc in rows:
        ds = 0.5 + 7.5 * v
        meas_inv = 1.0 / t60r if np.isfinite(t60r) and t60r > 0 else 0.0
        rel = 100.0 * (inv / meas_inv - 1.0) if meas_inv > 0 else float("nan")
        print(f"{v:6.2f} {ds:9.3f} {meas_inv:11.5f} {inv:10.5f} "
              f"{meas_inv - inv:+9.5f} {rel:+8.1f}")
    print(f"\n   幂律上界 kMeasInvT60Bound = {INV_BOUND:.4f} s > 参数上限 8.0 s")
    print("   ⇒ 参考在 norm=1.0 时反馈**尚未**到 1（故不发散），但已很接近。")


if __name__ == "__main__":
    main()
