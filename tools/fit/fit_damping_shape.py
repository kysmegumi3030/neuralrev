"""标定 `kFitDampingHz`，目标是**锚定超额衰减的形状**（不是相对 T60）。

## 为什么不再用相对 T60 当目标（fit_damping_top.py 的口径）

相对误差 |T60_c/T60_r − 1| 在长尾档会**爆炸**：T60 = 60/R，固定的绝对速率
误差 ΔR 在 R 小的时候（高 DECAY 档）会放大成极大的百分比。实测同一个
物理失配在 8 kHz 上读出 +1.9%（decay=0.20）到 +108.4%（decay=0.94）。
拿这种量当 max 目标，等于让 0.94 档独占全部权重，而它恰好是信噪比最差、
渲染最慢的一档。

## 目标量：锚定超额衰减 E

    R(band) = 60 / T60(band)              [dB/s]
    E(band) = R(band) − R(1 kHz)
    D(band) = E_参考(band) − E_候选(band)

减 1 kHz 消掉的是**宽带**项（每条线的 g_i 按 budget 编，budget 与频率无关），
剩下的 E 只含环内**频率相关**损耗：damping 一阶低通 + 分数延迟插值。

实测 D 在各 DECAY 档上稳定（4 kHz 跨度 0.87 / 均值 2.30；8 kHz 跨度 1.35 /
均值 5.40，见 tools/fit/diag_excess_shape.py），符合「每圈固定滤波器」的物理预期，
所以**一个与档位无关的 fc 就能补**，且单档测量即可代表全档。

## 这个口径让 fc 与 scale 正交

`kFitT60BudgetScale` 是频率平坦的宽带增益预算，在 E 里**整体抵消**。
所以本脚本只扫 fc，不碰 scale —— 过去的 2D 网格是在跟一个被锚定消掉的
耦合较劲。宽带项（1 kHz 自身的误差）交给逐档 `kFitT60ScaleKnotVal`，
那才是它的职责。分开拟合还顺带避免了「用一个标量去追一片逐带散布」的
老坑（§10.2.2 的 tilt shelf 失败）。

## 一阶低通的形状够不够

需要的相对每圈损耗是 4 kHz 0.193 dB、8 kHz 0.500 dB，比值 2.6；
一阶低通在该区间给的比值约 3.7（渐近 f²）。所以单极点**略偏陡**，
无法同时精确命中两带，最优解会把 8 kHz 对准、4 kHz 欠补约 0.05 dB/圈。
这是已知的模型缺项，不是拟合失败 —— 若 max 误差卡在 4 kHz 且 fc 被推向
边界，那才是该换二阶/两级串联的信号（教训 5：参数跑出物理范围 = 模型缺项）。

用法：
    python3 tools/fit/fit_damping_shape.py              # 扫描，跑完回滚
    python3 tools/fit/fit_damping_shape.py --apply      # 写入 ReverbTuning.h
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "fit"))

import fit_damping_t60 as FD                                        # noqa: E402
import t60_band_guard as G                                          # noqa: E402
from plugin_match import vst3_ref as V                              # noqa: E402

ANCHOR = "1 kHz"

# 拟合档位：D 已证实档位无关，用 0.50 档（尾长 6.0/7.7 s，最快且信噪比好）。
# 0.94 档尾长 40/51 s，单点要渲染 4 次 ⇒ 不适合放进扫描内层。
FIT_NORM = 0.50

# band 集必须覆盖顶端八度 —— 这正是这次验收反馈指向的区域，
# 而它在 fit_damping_t60.BANDS 里被一条已被推翻的理由排除在外（教训 7 同型）。
BANDS = list(G.BANDS) + [
    ("13.5 kHz", 11360, 16000),
    ("18 kHz", 16000, 20000),
]

TOL = G.TOL


def _guard(y_short, y_long):
    out = {}
    for nm, lo, hi in BANDS:
        a = FD.band_t60(FD.bandpass(y_short, lo, hi))
        b = FD.band_t60(FD.bandpass(y_long, lo, hi))
        if not (a == a and b == b) or a <= 0 or b <= 0:
            continue
        if abs(b / a - 1.0) > TOL:
            continue
        out[nm] = float(0.5 * (a + b))
    return out


def excess(t60s):
    """E(band) = 60/T60(band) − 60/T60(1 kHz)，dB/s。锚点缺失则返回 None。"""
    if ANCHOR not in t60s or t60s[ANCHOR] <= 0:
        return None
    anchor = 60.0 / t60s[ANCHOR]
    return {nm: 60.0 / v - anchor for nm, v in t60s.items() if v > 0}


_ref_E = None


def ref_excess(r):
    global _ref_E
    if _ref_E is None:
        ts, tl = G.tails_for(FIT_NORM)
        E = excess(_guard(G.render_ref(r, FIT_NORM, ts),
                          G.render_ref(r, FIT_NORM, tl)))
        if E is None:
            raise RuntimeError("参考侧锚点带被守卫弃用，无法锚定")
        _ref_E = E
    return _ref_E


def measure(r):
    """返回 (最差|D|, Σ|D|, 逐带 D)。"""
    R = ref_excess(r)
    ts, tl = G.tails_for(FIT_NORM)
    C = excess(_guard(G.render_cand(FIT_NORM, ts), G.render_cand(FIT_NORM, tl)))
    if C is None:
        return float("inf"), float("inf"), {}
    D = {nm: R[nm] - C[nm] for nm in R if nm in C}
    vals = [abs(v) for v in D.values()]
    return (max(vals) if vals else float("inf")), sum(vals), D


def trial(r, hz):
    FD.write_const("kFitDampingHz", hz, "{:.1f}")
    FD.rebuild()
    return measure(r)


def show(D):
    for nm, _, _ in BANDS:
        if nm in D:
            print(f"   {nm:>9}: D = {D[nm]:+7.2f} dB/s")
        else:
            print(f"   {nm:>9}: —（守卫弃用）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    hz0 = FD.read_const("kFitDampingHz")
    print(f"起点 kFitDampingHz = {hz0:.1f}   拟合档位 decay={FIT_NORM}")

    r = V.Vst3RefRenderer(sr=G.SR, block=512)
    R = ref_excess(r)
    print("\n参考侧 E(band)（锚定 1 kHz，双尾长守卫后）:")
    for nm, _, _ in BANDS:
        print(f"   {nm:>9}: " + (f"{R[nm]:+7.2f} dB/s" if nm in R else "—（弃用）"))

    w0, s0, D0 = trial(r, hz0)          # 基线必须真测（教训 3）
    print(f"\n基线 最差|D| {w0:.2f} dB/s   Σ|D| {s0:.2f}")
    show(D0)

    best = (w0, s0, hz0, D0)
    seen = {hz0}

    # 需要**增加**高频损耗 ⇒ fc 要往低走。上界仍留 24 k 作对照，
    # 下界 8 k：再低会咬进 2 kHz（参考侧 E 在 2 kHz 只有 +0.48 dB/s）。
    grid = [8000.0, 11000.0, 13000.0, 15000.0, 17000.0, 19000.0, 21000.0]
    print("\n--- 粗扫 fc ---")
    for hz in grid:
        if hz in seen:
            continue
        w, s, D = trial(r, hz)
        seen.add(hz)
        mark = ""
        if (round(w, 4), round(s, 3)) < (round(best[0], 4), round(best[1], 3)):
            best = (w, s, hz, D)
            mark = "  ← 最优"
        hi = "  ".join(f"{nm.split()[0]}:{D[nm]:+6.2f}"
                       for nm in ("4 kHz", "8 kHz", "18 kHz") if nm in D)
        print(f"  fc={hz:8.0f}  最差|D| {w:6.2f}  Σ {s:6.2f}   {hi}{mark}")

    print(f"\n粗扫最优 fc={best[2]:.0f}  最差|D| {best[0]:.2f} dB/s")

    print("\n--- 细化 ---")
    cw, cs, chz, cD = best
    for step in (1500.0, 500.0):
        improved = True
        while improved:
            improved = False
            for sgn in (+1, -1):
                hz = round(chz + sgn * step, 1)
                if hz < 6000.0 or hz > 26000.0 or hz in seen:
                    continue
                w, s, D = trial(r, hz)
                seen.add(hz)
                if (round(w, 4), round(s, 3)) < (round(cw, 4), round(cs, 3)):
                    cw, cs, chz, cD = w, s, hz, D
                    print(f"  fc → {hz:.0f}   最差|D| {w:.2f} dB/s")
                    improved = True
                    break
    best = (cw, cs, chz, cD)

    w1, s1, D1 = trial(r, best[2])
    print(f"\n落点 kFitDampingHz = {best[2]:.1f}")
    print(f"最差|D| {w0:.2f} → {w1:.2f} dB/s   Σ|D| {s0:.2f} → {s1:.2f}"
          f"（{len(seen)} 次试探）")
    show(D1)

    if not a.apply:
        FD.write_const("kFitDampingHz", hz0, "{:.1f}")
        FD.rebuild()
        print("\n（未加 --apply，已回滚）")
    else:
        print("\n已写入 ReverbTuning.h")
        print("注意：fc 变了会改动宽带 T60 ⇒ 必须接着重标 kFitT60ScaleKnotVal，")
        print("      再跑 tools/fit/band_report.py 复核平滑谱。")


if __name__ == "__main__":
    _save = FD.read_const("kFitDampingHz")
    try:
        main()
    except BaseException:
        FD.write_const("kFitDampingHz", _save, "{:.1f}")
        try:
            FD.rebuild()
        except Exception:
            pass
        print(f"\n[中断] 已回滚 kFitDampingHz={_save}")
        raise
