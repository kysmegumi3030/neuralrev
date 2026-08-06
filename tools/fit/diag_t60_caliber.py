"""定位「逐带 T60 都合格、全带 T60 却更差」这个矛盾出在哪个频段。

## 矛盾的事实（本工具的由来）

同一档位 decay=0.5、同一个 build（kFitT60BudgetScale = 1.01）：

    逐带 T60（fit_damping_t60 口径，125 Hz–8 kHz）
        −2.8  −9.2  +3.3  +9.4  −7.0  −8.6  −9.4 %   ⇒ 最差 9.4%
    全带 T60（env_t60_auto 口径，45 s 窗）      −11.5%
    全带 T60（diag_t60_scale_law 口径）        −12.2%

全带比**每一个**单带都差。两个独立的全带口径互相吻合（−11.5 / −12.2），
所以这不是某个脚本的口径 bug，是真实现象。只有两种可能：

  1) **未被覆盖的频段**：`BANDS` 只覆盖 88 Hz–11.4 kHz。低于它的能量
     （20–88 Hz，本网络在这里能量并不低 —— §10.2 实测 20–40 Hz 峰值
     只比全谱峰值低 4.4 dB）从未被测过衰减率。
  2) **多指数求和的几何效应**：全带包络是各带指数之和。若候选各带 T60 的
     **离散度**比参考大，对 −5…−40 dB 做单一直线拟合会拟出比任何单带都陡的
     斜率（早段被短带主导、晚段被长带主导，直线夹在中间反而更陡）。

这两个的修法完全不同：(1) 是真·实现缺陷（低频衰减太快，且此前所有拟合
都看不见它）；(2) 是口径产物（各带都对上了，全带读数本身就没有单一意义）。
**必须先分清，否则会去「修」一个不存在的缺陷** —— 与 §10.2.2 低架那次同型。

## 为什么低频带需要**自适应** RMS 窗（不能照抄 20 ms）

`fit_damping_t60.band_t60()` 固定 20 ms 窗。20 ms 在 31.5 Hz 处**不足一个周期**
（31.7 ms），RMS 包络会跟着载波自己起伏，回归斜率变成噪声。所以那份 BANDS
的下限 125 Hz 其实有两个理由，注释里只写了模式错位那一个。
本工具按 `win = max(20 ms, 10 / f_lo)` 自适应：至少 10 个周期。

代价是低频带的时间分辨率变粗（31.5 Hz 带的窗长 450 ms），所以低频带的 T60
读数只用来回答「是否显著偏短」，不参与任何拟合落点。

## 输出的读法

- `能量占比`：该带在全带总能量里的份额。全带包络由占比大的带主导，
  所以判断 (1) 是否成立要看**偏差大的带是否也占比大**。
- `离散度`：候选与参考各自的逐带 T60 极差比。若候选明显更散，(2) 成立。

用法：
    python3 tools/fit/diag_t60_caliber.py            # decay 0.5 与 0.86
    python3 tools/fit/diag_t60_caliber.py --decays 0.5
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "fit"))

from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402
from fit_decay_law import env_t60_auto                              # noqa: E402
# 逐带 T60 一律走既有口径，不自造（见 band_t60 的文档串）
from fit_damping_t60 import band_t60 as _ref_band_t60                # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)

# 扩展到 22 Hz：**下端两带是本工具存在的理由**（此前从未测过衰减率）。
# 上端补 16 kHz 带以确认 HIGH CUT 拐点以上没有异常。
BANDS = [
    ("31 Hz", 22, 44),
    ("63 Hz", 44, 88),
    ("125 Hz", 88, 177),
    ("250 Hz", 177, 355),
    ("500 Hz", 355, 710),
    ("1 kHz", 710, 1420),
    ("2 kHz", 1420, 2840),
    ("4 kHz", 2840, 5680),
    ("8 kHz", 5680, 11360),
    ("16 kHz", 11360, 20000),
]

# 各档预期 T60（REFERENCE §7.1），只用于定渲染窗长。
T60_PRIOR = {0.20: 1.371, 0.50: 2.469, 0.70: 4.476, 0.86: 10.258, 0.94: 24.817}


def bandpass(x, lo, hi):
    """零相位 FFT 带通。

    不用 IIR：IIR 自身的相位会给包络注入一个瞬态，而我们要测的正是包络形状。
    """
    n = len(x)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / SR)
    X[(f < lo) | (f >= hi)] = 0.0
    return np.fft.irfft(X, n)


def band_t60(y, f_lo):
    """逐带 T60。**直接复用既有口径** `fit_damping_t60.band_t60`。

    返回 (T60, 回归跨度 dB)。

    ## 为什么不自己写一份（本函数第一版是自己写的，读数全错）

    第一版按「窗长 ≥10 个周期」自适应、并用 `mode="same"` + 掐掉首尾各一窗。
    结果参考侧 500 Hz 读出 10.3 s、1 kHz 读出 4.7 s，而既有口径对**同一插件
    同一档位同一频带**给 3.15 / 3.10 s。判据是**每一条的 span 都恰好是 30.0 dB**
    —— 那正是 `−5…−35` 窗口本身的宽度，说明回归段落在一段近乎水平的地板上，
    拟合出来的是地板噪声的斜率，不是衰减。根因是 `mode="same"` 的补零把
    包络两端拉低、掐窗后剩下的区间已经不是主衰减段。

    教训 6 的直接应用：新脚本的度量函数必须与既有口径**逐字一致**，
    并先对基线交叉验证。所以这里改为 import 既有实现，宁可放弃低频带
    （20 ms 固定窗在 31 Hz 不足一个周期，那两带的读数不可信）也不再自造口径。
    低频带的判决因此**只用能量占比**（判据 1），它与 T60 读数无关。
    """
    t = _ref_band_t60(y)
    return t, float("nan")


def tail_for(norm):
    return float(np.clip(2.2 * T60_PRIOR[norm], 8.0, 48.0))


def ir_ref(r, norm):
    n = BASE_AT + int(tail_for(norm) * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    P = dict(drywet=1.0, predelay=0.5, decay=norm, lowcut=0.0, highcut=1.0)
    y = r.render(x, params={f"reverb_{k}": v for k, v in P.items()})
    return y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]


def ir_cand(norm):
    n = BASE_AT + int(tail_for(norm) * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    P = dict(drywet=1.0, predelay=0.5, decay=norm, lowcut=0.0, highcut=1.0)
    c = NrevRenderer(sr=SR, block=512)
    return c.render(x, params=P).astype(np.float64)[0][BASE_AT:]


def report(norm, ir_r, ir_c):
    print(f"\n{'=' * 74}\ndecay = {norm:.2f}   （渲染窗 {tail_for(norm):.0f} s）\n{'=' * 74}")

    er_tot = float(np.sum(ir_r ** 2))
    ec_tot = float(np.sum(ir_c ** 2))

    print("   频带     参考T60   候选T60    相对%    能量占比(参/候)")
    rows = []
    for nm, lo, hi in BANDS:
        br, bc = bandpass(ir_r, lo, hi), bandpass(ir_c, lo, hi)
        tr, _ = band_t60(br, lo)
        tc, _ = band_t60(bc, lo)
        wr = float(np.sum(br ** 2)) / er_tot * 100.0
        wc = float(np.sum(bc ** 2)) / ec_tot * 100.0
        rel = (tc / tr - 1.0) * 100.0 if (tr == tr and tc == tc and tr > 0) else float("nan")
        rows.append((nm, tr, tc, rel, wr, wc))
        rs = f"{rel:+7.1f}" if rel == rel else "    n/a"
        # 31/63 Hz 标注不可信：20 ms 固定窗在这两带不足一个周期
        warn = "  ←窗长不足,勿引用" if lo < 88 else ""
        print(f"  {nm:>7}   {tr:7.3f}   {tc:7.3f}  {rs}     "
              f"{wr:5.1f}% /{wc:5.1f}%{warn}")

    # 全带（既有 env_t60_auto 口径，与 fit_t60_scale.py 逐字一致）
    tr, _n, srb, _w = env_t60_auto(ir_r)
    tc, _n, scb, _w = env_t60_auto(ir_c)
    print(f"\n  全带(env_t60_auto)  参考 {tr:.3f} (跨度 {srb:.1f})   "
          f"候选 {tc:.3f} (跨度 {scb:.1f})   {100 * (tc / tr - 1):+.1f}%")

    # ---- 判据 1：偏差大的带是否也占比大 ----
    print("\n  判据 1（未覆盖频段是否是主因）")
    low = [x for x in rows if x[0] in ("31 Hz", "63 Hz")]
    for nm, a, b, rel, wr, wc in low:
        if rel == rel:
            print(f"    {nm}: 相对 {rel:+.1f}%，占参考总能量 {wr:.1f}%")
    lw = sum(x[4] for x in low)
    print(f"    下端两带合计占比 {lw:.1f}%"
          + ("  ⇒ 占比可观，若偏短则是全带读数的实质来源"
             if lw >= 5.0 else "  ⇒ 占比很小，难以主导全带读数"))

    # ---- 判据 2：候选逐带离散是否比参考大 ----
    # 只用 125 Hz 以上：31/63 Hz 的读数受固定 20 ms 窗污染（见 band_t60 文档串）。
    ok = [x for x in rows if x[1] == x[1] and x[2] == x[2]
          and x[0] not in ("31 Hz", "63 Hz")]
    if ok:
        rr = max(x[1] for x in ok) / min(x[1] for x in ok)
        cc = max(x[2] for x in ok) / min(x[2] for x in ok)
        print("\n  判据 2（多指数求和的几何效应）")
        print(f"    逐带 T60 离散度：参考 {rr:.2f}×   候选 {cc:.2f}×")
        print("    " + ("候选更散 ⇒ 全带单直线拟合会更陡，部分偏差是口径产物"
                        if cc > rr * 1.05 else
                        "两者离散度相当 ⇒ 几何效应不足以解释全带偏差"))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decays", type=float, nargs="+", default=[0.50, 0.86])
    a = ap.parse_args()

    r = V.Vst3RefRenderer(sr=SR, block=512)

    # ---- 口径对账（教训 6：新脚本先跟既有工具对上，再看结论）----
    # 本脚本的渲染窗更长（decay=0.5 时 8 s vs fit_damping_t60 的 6 s），
    # 所以不要求逐位相同，但同一带的参考 T60 必须落在同一量级、差异 <15%。
    import fit_damping_t60 as F
    base = F.ref_t60s(r)
    ir_r0 = ir_ref(r, 0.50)
    print("口径对账（参考侧逐带 T60，decay=0.5）")
    print("   频带    本脚本   fit_damping_t60   偏差%")
    bad = []
    for nm, lo, hi in BANDS:
        if nm not in base or not (base[nm] == base[nm]):
            continue
        t, _ = band_t60(bandpass(ir_r0, lo, hi), lo)
        if not (t == t):
            continue
        d = (t / base[nm] - 1.0) * 100.0
        flag = "" if abs(d) < 15.0 else "   ← 不一致"
        if abs(d) >= 15.0 and lo >= 88:
            bad.append(nm)
        print(f"  {nm:>7}  {t:7.3f}   {base[nm]:7.3f}   {d:+7.1f}{flag}")
    if bad:
        print(f"\n  ✗ 与既有口径不一致的带：{bad}")
        print("  ⇒ 先修口径再看结论（教训 6）。不要用下面的表下判断。")
    else:
        print("\n  ✓ 125 Hz 以上各带与既有口径一致（<15%）")

    for nv in a.decays:
        if nv not in T60_PRIOR:
            print(f"（跳过 {nv}：T60_PRIOR 里没有该档的窗长先验）")
            continue
        report(nv, ir_ref(r, nv), ir_cand(nv))


if __name__ == "__main__":
    main()
