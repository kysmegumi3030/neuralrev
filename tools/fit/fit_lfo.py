"""LFO 深度的拟合：让候选的**时变行为**与参考一致。

参考的时变行为有一组可直接比对的量（docs/REFERENCE.md §10）：
    激励位移 1 ms（48 样点）→ nrmse 9.23%
    逐 10 ms 窗：0–40 ms 时不变（≤0.03%），40–50 ms 跳到 4.64%，
                 之后随时间单调增长到 190–200 ms 的 15.64%

这组量对 LFO 的**深度**极敏感、对初相不敏感（初相只决定具体波形，
不改变「挪多少 → 差多少」的统计）。所以它是拟合深度的正确目标，
也是本项目能对齐时变行为的唯一可观测抓手。

同时报告平滑谱误差，确认调制没有把已经对上的频响弄坏。

用法：python3 tools/fit/fit_lfo.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer, smoothed_spectrum_err_db  # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

P = dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)

# 参考实测（ref_shift_invariance.py / lfo_late 的输出）
REF_SHIFT_NRMSE = {48: 0.0923}          # 1 ms 位移
REF_WINDOW_NRMSE = {                     # 1 ms 位移下逐 10 ms 窗
    0: 0.0000, 10: 0.00007, 20: 0.00007, 30: 0.00032,
    40: 0.04638, 60: 0.05209, 100: 0.05999, 190: 0.15641,
}


def rebuild():
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:600])


def set_depth(v):
    """写回 kFitLfoDepthSamples。

    注意不能用「新旧文本不同」当成功判据：扫描时可能恰好写入与当前
    完全相同的值（例如收尾时把最优值再写一遍），那时 new == s 是正常的。
    正确的判据是**正则是否匹配到**。
    """
    s = open(TUNING).read()
    pat = re.compile(r"(kFitLfoDepthSamples\s*=\s*)([-\d.eE+]+)")
    if not pat.search(s):
        raise KeyError("在 ReverbTuning.h 里找不到 kFitLfoDepthSamples")
    open(TUNING, "w").write(pat.sub(lambda m: m.group(1) + f"{v:.4f}", s, count=1))


def cand_ir(at, tail=4.0):
    c = NrevRenderer(sr=SR, block=512)
    n = at + int(tail * SR)
    x = np.zeros(n, dtype=np.float32)
    x[at] = 1.0
    return c.render(x, params=P).astype(np.float64)[0][at:]


def nrmse(a, b):
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    g = float(np.dot(a, b) / max(np.dot(b, b), 1e-30))
    return float(np.sqrt(np.mean((a - g * b) ** 2)) / max(np.sqrt(np.mean(a ** 2)), 1e-30))


def measure_timevariance():
    """候选自身的时变行为：整体 nrmse + 逐窗 nrmse。"""
    base = cand_ir(BASE_AT)
    y = cand_ir(BASE_AT + 48)
    overall = nrmse(base, y)
    win = {}
    for a in (0, 10, 20, 30, 40, 60, 100, 190):
        lo, hi = int(a / 1000 * SR), int((a + 10) / 1000 * SR)
        if hi <= min(len(base), len(y)):
            win[a] = nrmse(base[lo:hi], y[lo:hi])
    return overall, win


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)

    # 参考侧的平滑谱（用于确认调制不破坏频响）
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    yr = r.render(x, params={f"reverb_{k}": v for k, v in P.items()})
    yr = yr.astype(np.float64)[0][BASE_AT + REF_LATENCY:]

    print(f"目标（参考实测）：1 ms 位移 nrmse = {REF_SHIFT_NRMSE[48]*100:.2f}%")
    print("  逐窗：" + "  ".join(f"{k}ms={v*100:.2f}%" for k, v in REF_WINDOW_NRMSE.items()))

    print("\n扫描 kFitLfoDepthSamples：")
    print("   depth   1ms位移nrmse   目标差    平滑谱max")
    best = None
    # 上一轮扫到 4.0 仍只有 3.4%（目标 9.23%）且随深度单调上升，
    # 故把上界大幅抬高再扫一轮。
    for depth in [0.0, 1.0, 2.0, 4.0, 7.0, 11.0, 16.0, 24.0, 36.0]:
        set_depth(depth)
        rebuild()
        overall, _ = measure_timevariance()
        yc = cand_ir(BASE_AT)
        gmax = smoothed_spectrum_err_db(yr, yc, sr=SR)[0]
        err = abs(overall - REF_SHIFT_NRMSE[48])
        flag = ""
        if best is None or err < best[0]:
            best = (err, depth)
            flag = "  <-- best"
        print(f"   {depth:5.2f}   {overall*100:9.3f}%   {err*100:7.3f}%"
              f"   {gmax:8.2f} dB{flag}")

    set_depth(best[1])
    rebuild()
    print(f"\n→ kFitLfoDepthSamples = {best[1]:.4f}")

    overall, win = measure_timevariance()
    print(f"\n最终时变行为对比（1 ms 位移）：")
    print(f"  整体 nrmse：候选 {overall*100:.3f}%  参考 {REF_SHIFT_NRMSE[48]*100:.3f}%")
    print("  逐 10 ms 窗：")
    print("     窗(ms)    候选      参考")
    for k in sorted(win):
        ref_v = REF_WINDOW_NRMSE.get(k)
        rs = f"{ref_v*100:7.3f}%" if ref_v is not None else "      -"
        print(f"     {k:4d}   {win[k]*100:7.3f}%  {rs}")


if __name__ == "__main__":
    main()
