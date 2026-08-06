"""按**模式位置**拟合延迟线长度（取代盲扫 spread 因子）。

为什么换方法（diag_lowmodes.py 的判决）：
    20–40 Hz  均值 +5.81 dB，去均值后 std 8.43、max 14.48
    40–80 Hz  均值 +2.51 dB，去均值后 std 4.46、max 10.76
⇒ 低频误差以**错位项**为主，不是电平项。调增益/滤波器无用；
  fit_lines.py 的 spread 盲扫也无用（它只改分布宽度，不对齐具体位置，
  实测目标 60.35→60.24，几乎无改善）。

FDN 里每条长度 D 的延迟线贡献一族梳状模式，间隔 sr/D。
低频（<100 Hz）的峰位主要由**最长的几条线**决定。因此：
  1. 从参考的细谱里提取 20–120 Hz 的峰频 f_k；
  2. 对每条候选线长 D_i，其模式频率为 m·sr/D_i；
  3. 以「参考峰集合与候选模式集合的对齐误差」为目标，
     用坐标下降逐条调整 D_i（每条只在 ±10% 内动，保持互素奇数），
     使候选的低频模式落到参考的峰位上。

目标函数用双向倒角距离（chamfer）：每个参考峰找最近的候选模式，
反向亦然，避免「候选把所有模式挤到一个参考峰上」的退化解。

用法：python3 tools/fit/fit_modes.py [--rounds 3] [--apply]
默认只报告；--apply 才写回 ReverbTuning.h。
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

P = dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)

F_LO, F_HI = 20.0, 120.0     # 拟合模式位置的频段
BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000), (2000, 20000)]


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


def peaks_hz(curve, f_lo=F_LO, f_hi=F_HI, min_prom_db=1.5):
    """提取显著局部极大的频率。"""
    d = 20 * np.log10(np.maximum(curve, 1e-30))
    m = (F >= f_lo) & (F <= f_hi)
    i0 = int(np.argmax(m))
    c = d[m]
    ff = F[m]
    out = []
    for i in range(1, len(c) - 1):
        if c[i] > c[i - 1] and c[i] >= c[i + 1]:
            lo = c[max(0, i - 12):i].min() if i > 0 else c[i]
            hi = c[i + 1:i + 13].min() if i + 1 < len(c) else c[i]
            if c[i] - max(lo, hi) >= min_prom_db:
                out.append(ff[i])
    del i0
    return np.array(out)


def modes_of(lines, f_lo=F_LO, f_hi=F_HI):
    """给定线长集合，列出落在 [f_lo,f_hi] 的所有模式频率。"""
    out = []
    for D in lines:
        if D <= 0:
            continue
        base = SR / float(D)
        m_lo = max(1, int(math.floor(f_lo / base)))
        m_hi = int(math.ceil(f_hi / base))
        for k in range(m_lo, m_hi + 1):
            f = k * base
            if f_lo <= f <= f_hi:
                out.append(f)
    return np.array(sorted(out))


def chamfer(ref_pk, cand_md):
    """双向倒角距离（Hz），对数频率上算——低频等百分比误差更有意义。"""
    if len(ref_pk) == 0 or len(cand_md) == 0:
        return 1e6
    a = np.log2(ref_pk)[:, None]
    b = np.log2(cand_md)[None, :]
    d = np.abs(a - b)
    return float(d.min(axis=1).mean() + d.min(axis=0).mean())


def coprime_odd(target, taken, lo, hi):
    for dd in range(0, 600):
        for c in ({target + dd, target - dd} if dd else {target}):
            n = int(c)
            if n % 2 == 0 or n < lo or n > hi:
                continue
            if all(math.gcd(n, t) == 1 for t in taken):
                return n
    return None


def band_report(r, ref_cache):
    """返回 (全带 max, 逐带 max 列表)。"""
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    B = smooth(c.render(x, params=P).astype(np.float64)[0][BASE_AT:])
    A = ref_cache
    err = np.abs(20 * np.log10(np.maximum(B, 1e-30) / np.maximum(A, 1e-30)))
    m = (F >= 20) & (F <= 20000)
    return float(err[m].max()), [float(err[(F >= lo) & (F <= hi)].max())
                                 for lo, hi in BANDS]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    r = V.Vst3RefRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    ref = r.render(x, params={f"reverb_{k}": v for k, v in P.items()}
                   ).astype(np.float64)[0][BASE_AT + REF_LATENCY:]
    A = smooth(ref)

    ref_pk = peaks_hz(A)
    print(f"参考在 {F_LO:.0f}–{F_HI:.0f} Hz 的显著模式峰（{len(ref_pk)} 个）：")
    print("  " + "  ".join(f"{v:.2f}" for v in ref_pk))

    orig_a, orig_b = read_lines("LinesA"), read_lines("LinesB")
    print(f"\n当前 A：{orig_a}")
    print(f"当前 B：{orig_b}")

    base_all = orig_a + orig_b
    print(f"\n当前模式倒角距离（log2 Hz）= {chamfer(ref_pk, modes_of(base_all)):.5f}")

    cur_a, cur_b = list(orig_a), list(orig_b)

    for rd in range(a.rounds):
        improved = False
        for which, arr in (("A", cur_a), ("B", cur_b)):
            for i in range(len(arr)):
                others = [v for j, v in enumerate(cur_a + cur_b)
                          if not (which == "A" and j == i)
                          and not (which == "B" and j == i + len(cur_a))]
                base = arr[i]
                lo, hi = int(base * 0.82), int(base * 1.18)
                best_v, best_s = base, chamfer(ref_pk, modes_of(cur_a + cur_b))
                for cand in range(lo | 1, hi + 1, 2):
                    if math.gcd(cand, 1) and any(math.gcd(cand, t) != 1 for t in others):
                        continue
                    trial = list(arr)
                    trial[i] = cand
                    allv = (trial + cur_b) if which == "A" else (cur_a + trial)
                    s = chamfer(ref_pk, modes_of(allv))
                    if s < best_s - 1e-9:
                        best_s, best_v = s, cand
                if best_v != base:
                    arr[i] = best_v
                    improved = True
        sc = chamfer(ref_pk, modes_of(cur_a + cur_b))
        print(f"  round {rd + 1}: 倒角 = {sc:.5f}")
        if not improved:
            break

    print(f"\n拟合后 A：{cur_a}")
    print(f"拟合后 B：{cur_b}")
    print(f"倒角距离 {chamfer(ref_pk, modes_of(base_all)):.5f}"
          f" → {chamfer(ref_pk, modes_of(cur_a + cur_b)):.5f}")

    # 实测验证：写入 → 重编译 → 逐带
    print("\n实测验证（写入后重编译）：")
    write_lines("LinesA", cur_a)
    write_lines("LinesB", cur_b)
    rebuild()
    gm, bands = band_report(r, A)
    print(f"  全带 max = {gm:.2f} dB")
    print("  逐带 max：" + "  ".join(
        f"{lo}-{hi}:{v:.2f}" for (lo, hi), v in zip(BANDS, bands)))

    if not a.apply:
        write_lines("LinesA", orig_a)
        write_lines("LinesB", orig_b)
        rebuild()
        print("\n（未加 --apply，已回滚到原值）")
    else:
        print("\n已写入 ReverbTuning.h")


if __name__ == "__main__":
    main()
