"""联立标定**湿声总增益** `kWetTrim` 与低架深度 `kFitTiltShelfDb`。

## 为什么现在要做这个（换插值 + 重标 damping 之后）

修掉 ModulatedDelay 的线性插值损耗（REFERENCE §7.5）并重标 damping/T60 预算
（§7.6）之后，逐带整段能量比变成了这样：

    125 Hz  −1.14 / 250 +1.85 / 500 +2.96 / 1k +3.11 / 2k +2.86
    4k +2.92 / 8k +2.97  dB

对比修之前（+2.18 / +3.05 / +2.59 / +0.81 / −0.79 / −1.40）：
**倾斜没了**（250 Hz–8 kHz 的离散从 3.58 dB 收到 1.26 dB），
剩下的是一条几乎**平的 +3 dB 宽带过量**。

平的偏差正是静态增益能修的那一类 —— 这跟 §7.5 里「衰减率错不能用静态滤波修」
的结论不冲突：那条说的是**斜率**，这条说的是斜率修好之后剩下的**电平**。
诊断量也支持：diag_band_decay.py 的逐窗斜率现在全为正（8 kHz +3.14 dB/s），
不再有随时间增长的误差。

## 为什么 trim 和低架深度必须一起动

不能只把 kWetTrim 压 3 dB：125 Hz 现在已经是 **−1.14 dB**（偏低），
因为 kFitTiltShelfDb = −4.25 的低架正压着它。整体压 3 dB 会让低频从
−1.14 掉到 −4 dB 以上，20–40 / 40–80 两带立刻恶化 —— 而它们本来就是
最差带（12.68 / 14.11 dB）。

两者的作用区**部分重叠**（低架在 235 Hz 以下全量、以上渐失效），
所以是一对耦合量：trim 压全带，低架只把低频那部分**抬回来**。
分开扫会来回打架，故做二维网格 + 邻域细化。

## 目标函数

沿用 fit_tilt.py 的口径不变（否则两个脚本的「谁更好」没法比）：

    max_带 ( 该带在所有档上的最差 max误差 − 该带可达下界 )

min–max 而不是和式，理由同 fit_tilt.py 的注释：验收是逐 bin ≤3 dB，
卡住的永远是最差带，和式会为几个宽带的小改善去牺牲它。

档位集也照抄 fit_tilt.py 的四档（含被两次覆盖漏洞逼出来的 decay-hi
与 predelay-hi）—— 不要**猜**哪一档最差，那个坑在 fit_tilt.py 里踩过两次。

用法：
    python3 tools/fit/fit_trim_tilt.py            # 扫描，跑完回滚
    python3 tools/fit/fit_trim_tilt.py --apply    # 写入
    python3 tools/fit/fit_trim_tilt.py --coarse   # 只跑粗网格
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
NFFT = 65536
F = np.fft.rfftfreq(NFFT, 1.0 / SR)

TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
EFFECT = os.path.join(ROOT, "src", "dsp", "ReverbEffect.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000), (2000, 20000)]
FLOOR = np.array([0.35, 1.15, 1.38, 1.72, 1.05])

# 与 fit_tilt.py 完全一致的四档（理由见该文件 FIT_POINTS 的注释）
FIT_POINTS = [
    ("default",     dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)),
    ("lowcut-mid",  dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.5, highcut=1.0)),
    ("decay-hi",    dict(drywet=1.0, predelay=0.5, decay=0.8, lowcut=0.0, highcut=1.0)),
    ("predelay-hi", dict(drywet=1.0, predelay=0.9, decay=0.5, lowcut=0.0, highcut=1.0)),
]

_ref_cache: dict = {}


def write_const(path, name, value, fmt="{:.6f}"):
    s = open(path).read()
    pat = re.compile(rf"({name}\s*=\s*)([-\d.eE+]+)(f?)")
    if not pat.search(s):
        raise KeyError(f"{os.path.basename(path)} 里找不到 {name}")
    open(path, "w").write(
        pat.sub(lambda m: m.group(1) + fmt.format(value) + m.group(3), s, count=1))


def read_const(path, name):
    m = re.search(rf"{name}\s*=\s*([-\d.eE+]+)f?", open(path).read())
    if not m:
        raise KeyError(f"{os.path.basename(path)} 里找不到 {name}")
    return float(m.group(1))


def rebuild():
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:800])


def smooth(y, of=1 / 12):
    """平滑幅度谱 —— **必须与 fit_tilt.py / band_report.py 逐字一致**。

    窗是 [f·2^−of, f·2^+of]（即宽度 2·of 个八度），不是 ±of/2。
    这个差别不是小数点问题：我第一版写成 ±of/2（窗窄一半），保留了更多模式
    起伏，于是同一组常数算出的基线是 25.46 dB，而 band_report.py 给 14.11 dB。
    两个口径不可比 ⇒ 拟合出的落点也不可用（第一次跑出的 trim=−3.5 dB 就是
    这么来的，已废弃）。
    改口径等于改验收标准，只能整条工具链一起改，不能在单个脚本里悄悄换。
    """
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def ref_curve(r, name, p):
    """参考侧与候选常数无关，缓存一次。"""
    if name not in _ref_cache:
        n = BASE_AT + int(4.0 * SR)
        x = np.zeros(n, dtype=np.float32)
        x[BASE_AT] = 1.0
        y = r.render(x, params={f"reverb_{k}": v for k, v in p.items()})
        _ref_cache[name] = smooth(y.astype(np.float64)[0][BASE_AT + REF_LATENCY:])
    return _ref_cache[name]


def cand_curve(p):
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return smooth(c.render(x, params=p).astype(np.float64)[0][BASE_AT:])


def measure(r):
    """返回 (min–max 目标, 和式, 逐带超额, 逐带均值)。"""
    nb = len(BANDS)
    tot = 0.0
    per = np.zeros(nb)
    mean = np.zeros(nb)
    for name, p in FIT_POINTS:
        A, B = ref_curve(r, name, p), cand_curve(p)
        d = 20 * np.log10(np.maximum(B, 1e-30) / np.maximum(A, 1e-30))
        err = np.abs(d)
        for i, (lo, hi) in enumerate(BANDS):
            m = (F >= lo) & (F <= hi)
            ex = max(0.0, float(err[m].max()) - FLOOR[i])
            tot += ex
            per[i] = max(per[i], ex)          # 各档取最差，不取平均
            mean[i] += float(d[m].mean()) / len(FIT_POINTS)
    return float(per.max()), tot, per, mean


def trial(r, trim_db, shelf_db):
    """trim 以 dB 给出（更直观），写入时换成线性倍数。"""
    write_const(EFFECT, "kWetTrim", 10.0 ** (trim_db / 20.0), "{:.6f}")
    write_const(TUNING, "kFitTiltShelfDb", shelf_db, "{:.4f}")
    rebuild()
    return measure(r)


def report(tag, worst, tot, per, mean):
    print(f"{tag}目标 {worst:.2f} dB  和式 {tot:.2f}")
    print("      超额 " + "  ".join(
        f"{lo}-{hi}:{v:.2f}" for (lo, hi), v in zip(BANDS, per)))
    print("      均值 " + "  ".join(
        f"{lo}-{hi}:{v:+.2f}" for (lo, hi), v in zip(BANDS, mean)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--coarse", action="store_true")
    a = ap.parse_args()

    orig_trim = read_const(EFFECT, "kWetTrim")
    orig_shelf = read_const(TUNING, "kFitTiltShelfDb")
    orig_trim_db = 20.0 * np.log10(orig_trim)
    print(f"起点：kWetTrim {orig_trim:.6f}（{orig_trim_db:+.2f} dB）"
          f"  kFitTiltShelfDb {orig_shelf:+.4f}")

    r = V.Vst3RefRenderer(sr=SR, block=512)

    # **先重编译再测基线。** 不能假设磁盘上的 nrev_render 与头文件一致：
    # 上一次拟合（哪怕是被中断/回滚的那次）留下的二进制可能是别的常数。
    # 这个坑真踩过：手改回头文件常数但没重编译，基线测出 16.62 dB，
    # 而同一组常数经 trial() 重编译后是 13.12 —— 差 3.5 dB 全是陈旧二进制。
    rebuild()
    w0, t0, p0, m0 = measure(r)
    report("    ", w0, t0, p0, m0)
    print("      （基线已重编译；应与 band_report.py 在同一常数下的读数对上）")

    # trim 往下扫：250 Hz–8 kHz 一致偏高约 +3 dB。
    # 低架同时往**浅**扫：trim 压全带后，低频不再需要那么深的补偿
    #（125 Hz 当前已 −1.14 dB，再一起压会让最差的两个低频带更糟）。
    grid_trim = [0.0, -1.0, -2.0, -2.5, -3.0, -3.5]
    grid_shelf = [-4.25, -3.0, -2.0, -1.0, 0.0]

    best = (w0, t0, orig_trim_db, orig_shelf)
    seen = {}
    print("\n--- 粗网格 ---")
    for td in grid_trim:
        for sd in grid_shelf:
            w, t, per, mean = trial(r, td, sd)
            seen[(td, sd)] = w
            flag = ""
            if (round(w, 4), round(t, 3)) < (round(best[0], 4), round(best[1], 3)):
                best = (w, t, td, sd)
                flag = "  ← 最优"
            print(f"  trim={td:+.1f}dB shelf={sd:+.2f}  目标 {w:6.2f}  "
                  + " ".join(f"{lo}:{v:.1f}" for (lo, _), v in zip(BANDS, per))
                  + flag)

    print(f"\n粗网格最优：trim={best[2]:+.2f} dB  shelf={best[3]:+.2f} dB  "
          f"目标 {best[0]:.2f}")

    if not a.coarse:
        print("\n--- 细化 ---")
        cw, ct, ctd, csd = best
        for step in (0.5, 0.2):
            improved = True
            while improved:
                improved = False
                for s in (+1, -1):
                    td = round(ctd + s * step, 2)
                    if td < -8.0 or td > 3.0 or (td, csd) in seen:
                        continue
                    w, t, _, _ = trial(r, td, csd)
                    seen[(td, csd)] = w
                    if (round(w, 4), round(t, 3)) < (round(cw, 4), round(ct, 3)):
                        cw, ct, ctd = w, t, td
                        print(f"  trim → {td:+.2f} dB   目标 {w:.2f}")
                        improved = True
                        break
        for step in (0.5, 0.25):
            improved = True
            while improved:
                improved = False
                for s in (+1, -1):
                    sd = round(csd + s * step, 2)
                    if sd < -8.0 or sd > 0.0 or (ctd, sd) in seen:
                        continue
                    w, t, _, _ = trial(r, ctd, sd)
                    seen[(ctd, sd)] = w
                    if (round(w, 4), round(t, 3)) < (round(cw, 4), round(ct, 3)):
                        cw, ct, csd = w, t, sd
                        print(f"  shelf → {sd:+.2f} dB   目标 {w:.2f}")
                        improved = True
                        break
        best = (cw, ct, ctd, csd)

    w1, t1, p1, m1 = trial(r, best[2], best[3])
    lin = 10.0 ** (best[2] / 20.0)
    print(f"\n落点：kWetTrim = {lin:.6f}f（{best[2]:+.2f} dB）"
          f"  kFitTiltShelfDb = {best[3]:+.4f}")
    report("    ", w1, t1, p1, m1)
    print(f"\n目标 {w0:.2f} → {w1:.2f} dB（{len(seen) + 1} 次试探）")
    print("逐带超额变化：" + "  ".join(
        f"{lo}-{hi}:{v0:.2f}→{v1:.2f}" for (lo, hi), v0, v1 in zip(BANDS, p0, p1)))
    rise = [f"{lo}-{hi}" for (lo, hi), v0, v1 in zip(BANDS, p0, p1) if v1 > v0 + 0.10]
    if rise:
        print("注意：以下带退化（min–max 口径下是被有意换出去的）：" + ", ".join(rise))

    if not a.apply:
        write_const(EFFECT, "kWetTrim", orig_trim, "{:.6f}")
        write_const(TUNING, "kFitTiltShelfDb", orig_shelf, "{:.4f}")
        rebuild()
        print("\n（未加 --apply，已回滚）")
    else:
        print("\n已写入。必须再跑 tools/fit/band_report.py 全 6 档复核。")


if __name__ == "__main__":
    main()
