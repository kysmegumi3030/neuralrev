"""延迟线长度**分布**的拟合（不只是整体缩放）。

为什么需要这一步：整体缩放（fit_network.py --stage scale）把目标从 107.7 压到
92.7，但逐频带看，缺口集中在两端（默认档位，1/12 oct 平滑）：

    20–40 Hz    max 15.56 dB   p95 15.31   ← 候选低频**过弱**（20 Hz 差 −23 dB）
    40–80 Hz    max 11.51      p95  9.64
    80–300 Hz   max  8.05      p95  5.26
    300–2000 Hz max  4.42      p95  3.11   ← 中频已接近口径
    2k–20k Hz   max  7.60      p95  7.44   ← 候选高频**衰减过快**

低频不足的物理原因：FDN 的最低模式频率 ≈ sr / (最长延迟线)。
当前最长线 ≈ 3925×1.4 ≈ 5495 样点 → 最低模式 ≈ 8.7 Hz，看似够低，
但**模式密度**在 20–40 Hz 极稀（只有一两条线贡献），
而参考在该频段有实打实的能量（20 Hz 仅 −10.4 dB）。
→ 需要把线长**分布**拉开：加长最长的几条以增加低频模式密度，
  同时保持互素性（避免模式重合产生音高感）。

本脚本扫描「分布展宽因子」spread：
    len_i = mean · (1 + spread·(r_i − 1))
其中 r_i 是原表归一化后的相对长度。spread=1 即原分布，>1 拉开，<1 收紧。
拉开后重新取最近的**互素奇数**。

用法：python3 tools/fit/fit_lines.py [--band low|all]
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
TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")
F = np.fft.rfftfreq(NFFT, 1.0 / SR)

FIT_POINTS = [
    ("default",   dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)),
    ("decay-min", dict(drywet=1.0, predelay=0.5, decay=0.0, lowcut=0.0, highcut=1.0)),
    ("decay-hi",  dict(drywet=1.0, predelay=0.5, decay=0.8, lowcut=0.0, highcut=1.0)),
]

BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000), (2000, 20000)]

_ref_cache: dict[str, np.ndarray] = {}


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
    body = ", ".join(str(int(v)) for v in values)
    open(TUNING, "w").write(s[:m.start(1)] + "\n    " + body + "\n" + s[m.end(1):])


def coprime_odd(target, taken):
    """取最接近 target 的奇数，且与已选的每个数互素。"""
    for d in range(0, 400):
        for cand in ({target + d, target - d} if d else {target}):
            n = int(cand)
            if n < 31 or n % 2 == 0:
                continue
            if all(math.gcd(n, t) == 1 for t in taken):
                return n
    return int(target) | 1


def reshape(base, spread):
    """按 spread 重排分布，保持均值不变。"""
    a = np.array(base, float)
    mean = a.mean()
    rel = a / mean
    out, taken = [], []
    for r_ in rel:
        t = mean * (1.0 + spread * (r_ - 1.0))
        n = coprime_odd(int(round(t)), taken)
        taken.append(n)
        out.append(n)
    return out


def ref_ir(r, name, params):
    if name not in _ref_cache:
        n = BASE_AT + int(4.0 * SR)
        x = np.zeros(n, dtype=np.float32)
        x[BASE_AT] = 1.0
        y = r.render(x, params={f"reverb_{k}": v for k, v in params.items()})
        _ref_cache[name] = y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]
    return _ref_cache[name]


def cand_ir(params):
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return c.render(x, params=params).astype(np.float64)[0][BASE_AT:]


def smooth(y, of=1 / 12):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = y[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def band_report(r):
    """返回 (目标值, 逐带 p95 列表)。目标 = 各档 max + 0.5·p95 之和。"""
    total = 0.0
    bands = np.zeros(len(BANDS))
    for name, p in FIT_POINTS:
        A, B = smooth(ref_ir(r, name, p)), smooth(cand_ir(p))
        err = np.abs(20 * np.log10(np.maximum(B, 1e-30) / np.maximum(A, 1e-30)))
        m = (F >= 20) & (F <= 20000)
        total += err[m].max() + 0.5 * np.percentile(err[m], 95)
        for i, (lo, hi) in enumerate(BANDS):
            bm = (F >= lo) & (F <= hi)
            bands[i] += np.percentile(err[bm], 95) / len(FIT_POINTS)
    return total, bands


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()

    r = V.Vst3RefRenderer(sr=SR, block=512)
    base_a, base_b = read_lines("LinesA"), read_lines("LinesB")
    print(f"当前 A 路线长：{base_a}")
    print(f"当前 B 路线长：{base_b}")

    rebuild()
    t0, b0 = band_report(r)
    print(f"\n起点：目标 {t0:.3f}")
    print("  逐带 p95：" + "  ".join(
        f"{lo}-{hi}:{v:.2f}" for (lo, hi), v in zip(BANDS, b0)))

    print("\n扫描分布展宽因子 spread：")
    best = (t0, 1.0)
    for spread in [1.0, 1.4, 1.8, 2.2, 2.8, 3.5]:
        write_lines("LinesA", reshape(base_a, spread))
        write_lines("LinesB", reshape(base_b, spread))
        rebuild()
        t, b = band_report(r)
        flag = ""
        if t < best[0]:
            best = (t, spread)
            flag = "  <-- best"
        print(f"  spread={spread:4.1f}  目标={t:8.3f}  "
              + " ".join(f"{v:5.2f}" for v in b) + flag)

    write_lines("LinesA", reshape(base_a, best[1]))
    write_lines("LinesB", reshape(base_b, best[1]))
    rebuild()
    t1, b1 = band_report(r)
    print(f"\n→ spread = {best[1]:.1f}，目标 {t0:.3f} → {t1:.3f}")
    print("  逐带 p95：" + "  ".join(
        f"{lo}-{hi}:{v:.2f}" for (lo, hi), v in zip(BANDS, b1)))
    print(f"  A 路：{read_lines('LinesA')}")
    print(f"  B 路：{read_lines('LinesB')}")


if __name__ == "__main__":
    main()
