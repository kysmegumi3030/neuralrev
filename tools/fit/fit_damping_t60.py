"""联立标定 `kFitDampingHz` 与 `kFitT60BudgetScale`，目标是**逐带 T60**。

## 为什么要新写一个（而不是用 fit_network.py --stage damping）

那个 stage 的目标是「平滑谱误差」，而平滑谱是对整段 IR 的**时间积分**量，
它对**衰减率**错误几乎不敏感 —— 一条「早期偏亮 + 尾巴过暗」的 IR
与一条「全程正确」的 IR 可以积分出很相近的频谱。
所以它把 kFitDampingHz 拟合到 3600 Hz 就「收敛」了，而
tools/fit/diag_band_decay.py 的时间分辨测量显示：

    频带    参考 T60   候选 T60   相对
    2 kHz     3.005     2.301    −23.4%
    4 kHz     2.723     1.365    −49.9%
    8 kHz     2.231     0.993    −55.5%

高频尾巴衰减快了约 2 倍。逐时间窗的能量比也证实误差**随时间增长**
（4 kHz 斜率 −15.1 dB/s，8 kHz −13.0 dB/s），这正是衰减率错的指纹；
若是纯电平错，斜率应当接近 0（低频三带就是这样，|斜率| ≤2.5）。

**结论：这个病不能用总线上的静态滤波去修。** 静态滤波按积分后的平均量
补偿，会让早期补过头、晚期仍不够，听感上尾巴依旧越来越暗。
必须把环内 damping 本身放松。

## 为什么两个常数必须**一起**动

kFitT60BudgetScale 的物理含义就是「补偿环内 damping + 插值的宽带损耗」
（见 ReverbTuning.h 的推导，落点 1.13）。放松 damping 会**减少**那份损耗，
于是原来的 1.13 就补过头了，整体 T60 会偏长。
两者是同一条环路上的耦合量，分开扫会来回打架：
先修 damping 则全带 T60 偏长，再修 scale 又把高频压回去。
所以这里做**二维网格 + 邻域细化**。

## 目标函数

    max_over_bands | T60_候选 / T60_参考 − 1 |

用**相对**误差（各带 T60 差 1.5 倍，绝对值会让长尾带独占权重），
取 **max** 而不是和式 —— 与验收口径同形（逐带最差），
理由与 fit_tilt.py 那次踩的坑相同，详见该文件的注释。
和式作同分时的次序。

参考侧逐带 T60 只测一次并缓存（它与候选常数无关）。

用法：
    python3 tools/fit/fit_damping_t60.py              # 扫描，跑完回滚
    python3 tools/fit/fit_damping_t60.py --apply      # 写入 ReverbTuning.h
    python3 tools/fit/fit_damping_t60.py --coarse     # 只跑粗网格
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
TAIL_SEC = 6.0

TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

# 1/1 oct 宽带：这里要的是每带 T60 的稳定估计，不是频率细节。
# 覆盖 125 Hz–8 kHz —— 低于 125 Hz 的带受模式错位污染（去均值 max >10 dB，
# 见 REFERENCE §10.2），T60 估计噪声大。
#
# ⚠️ 上沿停在 8 kHz（11360 Hz）的理由**已被推翻**。原注释写的是
# 「高于 8 kHz 受 HIGH CUT 拐点影响」，但实测 18–20 kHz 的亏空在
# HIGH CUT 三档上是 −3.47 / −3.39 / −3.38 dB，在 DECAY 两档上是
# −3.36 / −3.73 dB —— 几乎不动。真受 HIGH CUT 支配的话读数必随之变化。
# 后果：顶端八度**从未进入任何拟合目标**，一路错到人耳验收
# （18 kHz 的 T60 曾比参考短 37.9%）。
# 覆盖顶端的版本见 tools/fit/fit_damping_top.py，那才是现在的标定入口；
# 本文件保留作历史口径与 --check-decay 的复核工具。
BANDS = [
    ("125 Hz", 88, 177),
    ("250 Hz", 177, 355),
    ("500 Hz", 355, 710),
    ("1 kHz", 710, 1420),
    ("2 kHz", 1420, 2840),
    ("4 kHz", 2840, 5680),
    ("8 kHz", 5680, 11360),
]

# 拟合档位：decay=0.5（默认）。
# 只用一档是有意的：damping 的实测结论是「超额衰减与 DECAY 档无关」
# （REFERENCE §5），即它是每圈固定的滤波器。若跨档拟合出现矛盾，
# 那说明「与档位无关」这条实测被推翻了，属于结构性发现 —— 用
# --check-decay 单独复核，不混进主拟合。
FIT_DECAY = 0.5

_ref_t60: dict | None = None


def write_const(name, value, fmt="{:.6f}"):
    s = open(TUNING).read()
    pat = re.compile(rf"({name}\s*=\s*)([-\d.eE+]+)")
    if not pat.search(s):
        raise KeyError(f"ReverbTuning.h 里找不到 {name}")
    open(TUNING, "w").write(pat.sub(lambda m: m.group(1) + fmt.format(value),
                                    s, count=1))


def read_const(name):
    s = open(TUNING).read()
    m = re.search(rf"{name}\s*=\s*([-\d.eE+]+)", s)
    if not m:
        raise KeyError(f"ReverbTuning.h 里找不到 {name}")
    return float(m.group(1))


def rebuild():
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:800])


def bandpass(x, lo, hi):
    """零相位带通。用 FFT 而非 IIR：IIR 的相位响应会在起振段引入自身瞬态。"""
    n = int(2 ** np.ceil(np.log2(len(x))))
    X = np.fft.rfft(x, n)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    X[(f < lo) | (f > hi)] = 0.0
    return np.fft.irfft(X, n)[:len(x)]


def band_t60(y):
    """RMS 包络线性回归求 T60。

    用包络回归而不是 EDC：EDC 的反向累积在窗末必然归零，尾巴超过窗长的
    带会出现人造膝点、T60 被系统性低估且随窗长漂移。这个坑在 §7 的
    DECAY 律标定上已经踩过一次（20 s 窗 47.8 s vs 45 s 窗 526 s）。

    ## 掩码必须在**时间上连通**（2026-08-05 修）

    原先掩码是纯电平判据 `(db <= pk−5) & (db >= pk−35)`，**没有时间下界**。
    峰值之前的**起振段**（早期反射累积 + 扩散级把冲激摊开）同样满足
    「比峰值低 5…35 dB」，于是那些早期、低电平的样点被选进回归，把最小二乘
    直线压平 ⇒ T60 系统性偏长。

    实测污染量（`峰前样点%` 与 现口径/时间连通口径 的比值）：

        decay=0.20   参考 A/E 1.22…1.39      候选 A/E 1.20…**1.83**
        decay=0.50   参考 A/E 1.11…1.20      候选 A/E 1.11…1.39
        decay=0.94   参考 A/E 1.01…1.04      候选 A/E 1.01…1.04

    偏差**不对称**：候选的扩散级更长、起振更慢，峰前样点更多，所以它被
    拉长得更多；而档位越高、尾巴越长，起振段占比越小、污染越轻。
    两者叠加**伪造出一个「随 DECAY 档变化的环内高频损耗」** ——
    我曾据此以为候选存在档位相关的损耗机制，并差点去拟合逐档标量补偿它。
    换成时间连通掩码后，两侧的逐带超额衰减都变回**档位无关**（各约
    +7.5…9.0 dB/s 与 +2.7…3.9 dB/s @8 kHz），即一个干净的固定形状失配。

    所以现在同时施加两个时间边界：起点取**峰值时刻**、终点取**首次**跌破
    −35 dB 处。后者防的是另一个方向的污染（晚期回涨的样点重新落进电平窗，
    实测占比 0…25%），两个边界一起才能保证掩码对应一段单调衰减。
    """
    w = int(0.020 * SR)
    k = np.ones(w) / w
    e = np.sqrt(np.convolve(y ** 2, k, mode="same") + 1e-30)
    db = 20 * np.log10(e)
    t = np.arange(len(db)) / SR
    pk = db.max()
    ipk = int(np.argmax(db))

    m = (db <= pk - 5.0) & (db >= pk - 35.0)
    m[:ipk] = False                      # 砍掉起振段
    below = np.where(db[ipk:] <= pk - 35.0)[0]
    if below.size:
        m[ipk + int(below[0]) + 1:] = False   # 砍掉首次触底之后的回涨

    if m.sum() < 16:
        return float("nan")
    A = np.vstack([t[m], np.ones(int(m.sum()))]).T
    slope = np.linalg.lstsq(A, db[m], rcond=None)[0][0]
    if slope >= -1e-9:
        return float("nan")
    return float(-60.0 / slope)


def ir_of(render_fn, decay):
    n = BASE_AT + int(TAIL_SEC * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return render_fn(x, decay)


def ref_t60s(r, decay=FIT_DECAY):
    global _ref_t60
    if _ref_t60 is None:
        P = dict(drywet=1.0, predelay=0.5, decay=decay, lowcut=0.0, highcut=1.0)
        y = ir_of(lambda x, d: r.render(
            x, params={f"reverb_{k}": v for k, v in P.items()}), decay)
        ir = y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]
        _ref_t60 = {nm: band_t60(bandpass(ir, lo, hi)) for nm, lo, hi in BANDS}
    return _ref_t60


def cand_t60s(decay=FIT_DECAY):
    P = dict(drywet=1.0, predelay=0.5, decay=decay, lowcut=0.0, highcut=1.0)
    c = NrevRenderer(sr=SR, block=512)
    y = ir_of(lambda x, d: c.render(x, params=P), decay)
    ir = y.astype(np.float64)[0][BASE_AT:]
    return {nm: band_t60(bandpass(ir, lo, hi)) for nm, lo, hi in BANDS}


def measure(r):
    """返回 (最差相对误差, 和式, 逐带相对误差 dict)。"""
    R, C = ref_t60s(r), cand_t60s()
    rel = {}
    for nm, _, _ in BANDS:
        a, b = R[nm], C[nm]
        rel[nm] = float("nan") if not (a == a and b == b and a > 0) else (b / a - 1.0)
    vals = [abs(v) for v in rel.values() if v == v]
    return (max(vals) if vals else float("inf")), sum(vals), rel


def trial(r, hz, scale):
    write_const("kFitDampingHz", hz, "{:.1f}")
    write_const("kFitT60BudgetScale", scale)
    rebuild()
    return measure(r)


def show(tag, worst, tot, rel):
    print(f"{tag}最差 {worst * 100:+.1f}%  和式 {tot * 100:.1f}%")
    print("      " + "  ".join(
        f"{nm}:{rel[nm] * 100:+.1f}%" if rel[nm] == rel[nm] else f"{nm}:n/a"
        for nm, _, _ in BANDS))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--coarse", action="store_true")
    a = ap.parse_args()

    orig_hz = read_const("kFitDampingHz")
    orig_scale = read_const("kFitT60BudgetScale")

    r = V.Vst3RefRenderer(sr=SR, block=512)

    R = ref_t60s(r)
    print("参考逐带 T60（缓存一次）：")
    print("      " + "  ".join(f"{nm}:{R[nm]:.3f}s" for nm, _, _ in BANDS))
    print(f"\n起点（Hz={orig_hz:.1f} scale={orig_scale:.4f}）")
    w0, t0, rel0 = measure(r)
    show("    ", w0, t0, rel0)

    # 粗网格 —— 第二轮（Lagrange 插值落地后）。
    #
    # 第一轮的解跑到 25.2 kHz（**超过 Nyquist**，等于把 damping 关掉），
    # 8 kHz 的 T60 仍差 −13.3% ⇒ 那时候环内还有第二个高频损耗源，
    # 光调 damping 调不出来。它是 ModulatedDelay 的**线性插值**
    # （frac=0.5 时 −1.25 dB @8 kHz，每圈一次，约 −14 dB/s）。
    # 换成 3 阶 Lagrange 后该项降到约 −2.5 dB/s，damping 才重新有可调空间。
    #
    # 现在的方向**反过来**了：换插值后全带 T60 偏长（8 kHz +9.0%），
    # 所以 fc 要从「关掉」往**低**收（重新引入受控的高频损耗），
    # scale 也要从 1.13 往下降（环内宽带损耗变小，原值补过头）。
    grid_hz = [4000.0, 6000.0, 8000.0, 11000.0, 15000.0, 20000.0]
    grid_scale = [1.13, 1.09, 1.05, 1.01, 0.97]

    best = (w0, t0, orig_hz, orig_scale)
    seen = {}
    print("\n--- 粗网格 ---")
    for hz in grid_hz:
        for sc in grid_scale:
            w, t, rel = trial(r, hz, sc)
            seen[(hz, sc)] = w
            flag = ""
            if (round(w, 5), round(t, 4)) < (round(best[0], 5), round(best[1], 4)):
                best = (w, t, hz, sc)
                flag = "  ← 最优"
            hi = "  ".join(f"{nm.split()[0]}:{rel[nm] * 100:+6.1f}"
                           for nm in ("2 kHz", "4 kHz", "8 kHz"))
            print(f"  Hz={hz:7.0f} sc={sc:.2f}  最差 {w * 100:+7.1f}%  {hi}{flag}")

    print(f"\n粗网格最优：Hz={best[2]:.0f} scale={best[3]:.4f} "
          f"最差 {best[0] * 100:+.1f}%")

    if not a.coarse:
        print("\n--- 细化 ---")
        cw, ct, chz, csc = best
        for step in (2000.0, 800.0):
            improved = True
            while improved:
                improved = False
                for s in (+1, -1):
                    hz = round(chz + s * step, 1)
                    # 上界 20 kHz：一阶低通的 fc 超过 Nyquist（24 kHz @48k）后
                    # 通带内已几乎全平，再往上是**无意义的自由度** —— 第一轮就是
                    # 这样跑到 25.2 kHz 的，看着「还在改善」，其实只是在把 damping
                    # 关得更彻底，掩盖了真正的损耗源（插值）。
                    if hz < 2000.0 or hz > 20000.0 or (hz, csc) in seen:
                        continue
                    w, t, _ = trial(r, hz, csc)
                    seen[(hz, csc)] = w
                    if (round(w, 5), round(t, 4)) < (round(cw, 5), round(ct, 4)):
                        cw, ct, chz = w, t, hz
                        print(f"  Hz → {hz:.0f}   最差 {w * 100:+.1f}%")
                        improved = True
                        break
        for step in (0.03, 0.01):
            improved = True
            while improved:
                improved = False
                for s in (+1, -1):
                    sc = round(csc + s * step, 4)
                    if sc < 0.90 or sc > 1.40 or (chz, sc) in seen:
                        continue
                    w, t, _ = trial(r, chz, sc)
                    seen[(chz, sc)] = w
                    if (round(w, 5), round(t, 4)) < (round(cw, 5), round(ct, 4)):
                        cw, ct, csc = w, t, sc
                        print(f"  scale → {sc:.4f}   最差 {w * 100:+.1f}%")
                        improved = True
                        break
        best = (cw, ct, chz, csc)

    w1, t1, rel1 = trial(r, best[2], best[3])
    print(f"\n落点：kFitDampingHz={best[2]:.1f}  kFitT60BudgetScale={best[3]:.4f}")
    show("    ", w1, t1, rel1)
    print(f"\n最差逐带 T60 误差 {w0 * 100:+.1f}% → {w1 * 100:+.1f}%"
          f"（{len(seen) + 1} 次试探）")

    if not a.apply:
        write_const("kFitDampingHz", orig_hz, "{:.1f}")
        write_const("kFitT60BudgetScale", orig_scale)
        rebuild()
        print("\n（未加 --apply，已回滚）")
    else:
        print("\n已写入 ReverbTuning.h")
        print("注意：本次改动会影响平滑谱与整体 T60，必须再跑")
        print("      tools/fit/band_report.py 与 tools/fit/diag_band_decay.py 复核。")


if __name__ == "__main__":
    main()
