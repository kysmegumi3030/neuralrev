"""直接以**实测逐带误差**为目标的线长坐标下降（取代 chamfer 代理与 spread 盲扫）。

前两次尝试及其失败原因，记录在此以免重走：
  1. `fit_lines.py`（spread 盲扫）：只改分布宽度、不动具体位置。
     目标 60.35 → 60.24，无实质改善。
  2. `fit_modes.py`（chamfer 代理）：把参考峰位当成「各条线的梳状模式之并集」
     去对齐。**前提错误** —— Hadamard 全耦合 FDN 的低频峰是**整网的特征模**，
     不是各线独立梳状模式的并集。结果解退化成近似重复的长度
     （2911/3037/3029/3031），会产生金属味，且实测只到 15.09 dB。

本脚本不用代理：每次试探都真编译、真渲染、真测逐带误差。
代价是慢（每次试探一次 rebuild + 两次渲染），所以：
  * 只动**最长的 K 条**线（低频峰位主要由它们决定）；
  * 参考 IR 全程缓存，只重算候选；
  * 步长由粗到细（±5% → ±2% → ±1%）。

退化保护（chamfer 那次的教训）：
  * 任意两条线的相对间距不得小于 kMinSepRatio；
  * 保持奇数且两两互素；
  * 均值偏离初始不超过 ±12%（否则整体混响时间会跑掉）。

目标函数：各带 max 超出「可达下界」的部分之和，按缺口加权
（下界来自 tools/measure/ref_band_floor.py，是参考自比的实测值）。

用法：
    python3 tools/fit/fit_lines_direct.py --topk 4 --rounds 2        # 只报告
    python3 tools/fit/fit_lines_direct.py --topk 4 --rounds 2 --apply
"""
from __future__ import annotations

import argparse
import math
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
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000), (2000, 20000)]
FLOOR = np.array([0.35, 1.15, 1.38, 1.72, 1.05])

# 拟合档位：只用两档（默认 + 短衰减）以控制耗时；短衰减档对线长最敏感
FIT_POINTS = [
    ("default",  dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)),
    ("decay-min", dict(drywet=1.0, predelay=0.5, decay=0.0, lowcut=0.0, highcut=1.0)),
]

kMinSepRatio = 1.04      # 相邻线长至少差 4%，避免模式重合/退化
kMeanTol = 0.12          # 均值偏离初始不超过 ±12%

_ref: dict[str, np.ndarray] = {}


def rebuild():
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:600])


def read_lines(name):
    s = open(TUNING).read()
    m = re.search(rf"kArch{name}\s*\{{([^}}]*)\}}", s)
    return [int(t) for t in re.findall(r"\d+", m.group(1))]


def write_lines(name, values):
    s = open(TUNING).read()
    m = re.search(rf"kArch{name}\s*\{{([^}}]*)\}}", s)
    if not m:
        raise KeyError(f"找不到 kArch{name}")
    body = ", ".join(str(int(v)) for v in values)
    open(TUNING, "w").write(s[:m.start(1)] + "\n    " + body + "\n" + s[m.end(1):])


def smooth(y, of=1 / 12):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def ref_curve(r, name, p):
    if name not in _ref:
        n = BASE_AT + int(4.0 * SR)
        x = np.zeros(n, dtype=np.float32)
        x[BASE_AT] = 1.0
        y = r.render(x, params={f"reverb_{k}": v for k, v in p.items()})
        _ref[name] = smooth(y.astype(np.float64)[0][BASE_AT + REF_LATENCY:])
    return _ref[name]


def cand_curve(p):
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return smooth(c.render(x, params=p).astype(np.float64)[0][BASE_AT:])


def objective(r):
    """各档各带 max 超出下界的部分之和（dB）。越小越好。"""
    tot = 0.0
    per = np.zeros(len(BANDS))
    for name, p in FIT_POINTS:
        A, B = ref_curve(r, name, p), cand_curve(p)
        err = np.abs(20 * np.log10(np.maximum(B, 1e-30) / np.maximum(A, 1e-30)))
        for i, (lo, hi) in enumerate(BANDS):
            m = (F >= lo) & (F <= hi)
            ex = max(0.0, float(err[m].max()) - FLOOR[i])
            tot += ex
            per[i] += ex / len(FIT_POINTS)
    return tot, per


def valid(vals, init_mean):
    s = sorted(vals)
    for a, b in zip(s, s[1:]):
        if b / a < kMinSepRatio:
            return False
    for i, a in enumerate(s):
        for b in s[i + 1:]:
            if math.gcd(int(a), int(b)) != 1:
                return False
    m = float(np.mean(vals))
    return abs(m / init_mean - 1.0) <= kMeanTol


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=4, help="只动最长的 K 条")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    r = V.Vst3RefRenderer(sr=SR, block=512)
    orig_a, orig_b = read_lines("LinesA"), read_lines("LinesB")
    init_mean_a, init_mean_b = float(np.mean(orig_a)), float(np.mean(orig_b))

    rebuild()
    t0, p0 = objective(r)
    print(f"起点：目标 {t0:.3f}")
    print("  逐带超额：" + "  ".join(
        f"{lo}-{hi}:{v:.2f}" for (lo, hi), v in zip(BANDS, p0)))
    print(f"  A={orig_a}\n  B={orig_b}\n")

    cur_a, cur_b = list(orig_a), list(orig_b)
    best = t0
    evals = 1

    for rd, frac in enumerate([0.05, 0.02, 0.01][:a.rounds]):
        print(f"--- round {rd + 1}（步长 ±{frac * 100:.0f}%）---")
        for which in ("A", "B"):
            arr = cur_a if which == "A" else cur_b
            imean = init_mean_a if which == "A" else init_mean_b
            # 只动最长的 topk 条
            order = sorted(range(len(arr)), key=lambda i: -arr[i])[:a.topk]
            for i in order:
                base = arr[i]
                cands = []
                for s in (+1, -1):
                    d = int(round(base * frac))
                    v = base + s * d
                    v = v | 1
                    cands.append(v)
                for v in cands:
                    trial = list(arr)
                    trial[i] = v
                    if not valid(trial, imean):
                        continue
                    write_lines(f"Lines{which}", trial)
                    rebuild()
                    t, _ = objective(r)
                    evals += 1
                    if t < best - 1e-6:
                        best = t
                        arr[i] = v
                        print(f"    Lines{which}[{i}] {base} → {v}   目标 {t:.3f}")
                        break
                # 恢复到当前最优
                write_lines(f"Lines{which}", arr)
            rebuild()
        print(f"  round {rd + 1} 结束：目标 {best:.3f}")

    write_lines("LinesA", cur_a)
    write_lines("LinesB", cur_b)
    rebuild()
    t1, p1 = objective(r)
    print(f"\n目标 {t0:.3f} → {t1:.3f}（{evals} 次试探）")
    print("  逐带超额：" + "  ".join(
        f"{lo}-{hi}:{v:.2f}" for (lo, hi), v in zip(BANDS, p1)))
    print(f"  A={cur_a}\n  B={cur_b}")

    if not a.apply:
        write_lines("LinesA", orig_a)
        write_lines("LinesB", orig_b)
        rebuild()
        print("\n（未加 --apply，已回滚）")
    else:
        print("\n已写入 ReverbTuning.h")


if __name__ == "__main__":
    main()
