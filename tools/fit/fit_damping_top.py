"""联立重标 `kFitDampingHz` × `kFitT60BudgetScale`，**band 集覆盖顶端八度**。

## 为什么要新写一个（而不是直接跑 fit_damping_t60.py）

`fit_damping_t60.py` 的 `BANDS` 到 8 kHz（上沿 11360 Hz）为止，注释给的理由是
「高于 8 kHz 受 HIGH CUT 拐点影响」。**这条理由已被实测推翻**：18–20 kHz 的
亏空在 HIGH CUT 三档上是 −3.47 / −3.39 / −3.38 dB，在 DECAY 两档上是
−3.36 / −3.73 dB —— 几乎不动。若真受 HIGH CUT 支配，读数必随该参数变化。

于是顶端八度**从来没有进入过任何拟合目标**，这正是它一路错到验收的原因
（教训 7 的同型复发：拟合集必须覆盖所有要验收的维度；这次漏的是频带）。
本脚本把 band 集延伸到 16–20 kHz，其余口径与既有工具逐字一致。

## 口径（三条硬约束，都是过去踩坑换来的）

1. **T60 用 t60_band_guard 的双尾长守卫**，不是单尾长直读。
   同一个 `band_t60`，尾长 6 s 与 8 s 在 125 Hz 上曾读出 3.39 s 与 35.6 s
   （差 950%）—— 回归段滑到了截断平台上。漂移 >5% 的带直接弃用。
   顶端带 T60 短（约 1.7 s），本不易踩，但守卫是无条件的。
2. **度量函数复用 fit_damping_t60 的 `band_t60` / `bandpass`**，不自己写。
   手写过一次，span 恰好等于回归窗宽（30.0 dB）⇒ 拟合落在噪声地板上。
3. **每个候选点都是真编译 + 真渲染**，没有解析代理。

## 目标函数

    max_over_bands | T60_候选 / T60_参考 − 1 |

取 max 而非和式：与验收口径（逐带最差）同形。和式只作同分时的次序。
理由与 fit_tilt.py 那次踩的坑相同 —— 和式会拿一个带的退化去换别带的改善。

## 与插值器阶数的关系（先修结构、再拟合参数）

顶端亏空的**主因**是分数延迟插值器的阶数，不是 damping：damping 是一阶
低通（渐近 −6 dB/oct），插值损耗是 sinc 型、在 Nyquist 附近急陡，两者形状
不同，单极点无论取什么 fc 都表达不出来。所以先把 `kArchFracInterpOrder`
从 3 提到 9（18 kHz 每圈损耗 2.806 → 1.179 dB），**再**跑本脚本收残差。
顺序反了的话，fit 会再一次把 fc 推出物理范围（上一轮推到 25.2 kHz，
超过 Nyquist，那是模型缺项的信号 —— 教训 5）。

用法：
    python3 tools/fit/fit_damping_top.py             # 扫描，跑完回滚
    python3 tools/fit/fit_damping_top.py --apply     # 写入 ReverbTuning.h
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
from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402

SR = FD.SR
REF_LATENCY = FD.REF_LATENCY
BASE_AT = FD.BASE_AT
FIT_DECAY = 0.5

# 与 fit_damping_t60.BANDS 相同的 125 Hz–8 kHz，**外加**两条顶端带。
# 13.5 k 与 18 k 是这次验收反馈直接指向的区域。
BANDS = list(FD.BANDS) + [
    ("13.5 kHz", 11360, 16000),
    ("18 kHz", 16000, 20000),
]

# 双尾长守卫（口径同 t60_band_guard）：短尾 / 长尾相对差 >TOL 的带弃用。
TOL = 0.05
TAIL_SHORT = 6.0
TAIL_LONG = 7.7          # ≈ 1.28×，与 t60_band_guard 的 GROW 一致

_ref_cache: dict | None = None


def _render_ref(r, tail, decay):
    P = dict(drywet=1.0, predelay=0.5, decay=decay, lowcut=0.0, highcut=1.0)
    n = BASE_AT + int(tail * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    y = r.render(x, params={f"reverb_{k}": v for k, v in P.items()})
    return y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]


def _render_cand(tail, decay):
    P = dict(drywet=1.0, predelay=0.5, decay=decay, lowcut=0.0, highcut=1.0)
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(tail * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return c.render(x, params=P).astype(np.float64)[0][BASE_AT:]


def _guarded(y_short, y_long):
    """双尾长一致的带才返回读数。漂移的带宁可缺失也不带进目标。"""
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


def ref_guarded(r, decay=FIT_DECAY):
    global _ref_cache
    if _ref_cache is None:
        _ref_cache = _guarded(_render_ref(r, TAIL_SHORT, decay),
                              _render_ref(r, TAIL_LONG, decay))
    return _ref_cache


def measure(r, decay=FIT_DECAY):
    R = ref_guarded(r, decay)
    C = _guarded(_render_cand(TAIL_SHORT, decay), _render_cand(TAIL_LONG, decay))
    rel = {}
    for nm, _, _ in BANDS:
        if nm in R and nm in C and R[nm] > 0:
            rel[nm] = C[nm] / R[nm] - 1.0
    vals = [abs(v) for v in rel.values()]
    return (max(vals) if vals else float("inf")), sum(vals), rel


def trial(r, hz, scale):
    FD.write_const("kFitDampingHz", hz, "{:.1f}")
    FD.write_const("kFitT60BudgetScale", scale)
    FD.rebuild()
    return measure(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    hz0 = FD.read_const("kFitDampingHz")
    sc0 = FD.read_const("kFitT60BudgetScale")
    print(f"起点: kFitDampingHz={hz0}  kFitT60BudgetScale={sc0}")

    r = V.Vst3RefRenderer(sr=SR, block=512)
    R = ref_guarded(r)
    print("参考侧逐带 T60（双尾长守卫后）:")
    for nm, _, _ in BANDS:
        print(f"   {nm:>9}: " + (f"{R[nm]:.3f} s" if nm in R else "弃用（尾长漂移）"))

    # 基线必须是当前落点的真实读数（教训 3：不能拿上次的结果当基线）
    base_worst, base_sum, base_rel = trial(r, hz0, sc0)
    print(f"\n基线 最差 {base_worst*100:.2f}%  和式 {base_sum*100:.1f}%")
    for nm, v in base_rel.items():
        print(f"   {nm:>9}: {v*100:+7.2f}%")

    best = (base_worst, base_sum, hz0, sc0, base_rel)

    # damping 放松会让顶端 T60 变长，scale 收紧会把全带压回去 —— 耦合，必须联立。
    HZ = [18400.0, 21000.0, 24000.0, 28000.0, 34000.0, 44000.0]
    SC = [0.97, 0.99, 1.01, 1.03]
    print(f"\n粗网格 {len(HZ)}×{len(SC)}：")
    for hz in HZ:
        for sc in SC:
            w, s, rel = trial(r, hz, sc)
            mark = ""
            if (w, s) < (best[0], best[1]):
                best = (w, s, hz, sc, rel)
                mark = "  <= 最优"
            print(f"  fc={hz:>8.1f} scale={sc:.2f}  最差 {w*100:6.2f}%{mark}")

    w, s, hz, sc, rel = best
    print(f"\n粗网格最优: fc={hz:.1f} scale={sc:.2f} 最差 {w*100:.2f}%")

    # 邻域细化
    for hz2 in [hz * f for f in (0.92, 0.96, 1.0, 1.04, 1.08)]:
        for sc2 in [round(sc + d, 3) for d in (-0.01, 0.0, 0.01)]:
            if abs(hz2 - hz) < 1e-9 and abs(sc2 - sc) < 1e-9:
                continue
            w2, s2, rel2 = trial(r, hz2, sc2)
            if (w2, s2) < (best[0], best[1]):
                best = (w2, s2, hz2, sc2, rel2)
                print(f"  细化 fc={hz2:8.1f} scale={sc2:.3f}  最差 {w2*100:6.2f}%  <= 最优")

    w, s, hz, sc, rel = best
    print(f"\n落点: kFitDampingHz={hz:.1f}  kFitT60BudgetScale={sc:.3f}")
    print(f"最差 {w*100:.2f}%（基线 {base_worst*100:.2f}%）")
    for nm, v in rel.items():
        print(f"   {nm:>9}: {v*100:+7.2f}%   (基线 {base_rel.get(nm, float('nan'))*100:+7.2f}%)")

    if a.apply:
        FD.write_const("kFitDampingHz", hz, "{:.1f}")
        FD.write_const("kFitT60BudgetScale", sc)
        FD.rebuild()
        print("已写入 ReverbTuning.h 并重编译。")
    else:
        FD.write_const("kFitDampingHz", hz0, "{:.1f}")
        FD.write_const("kFitT60BudgetScale", sc0)
        FD.rebuild()
        print("已回滚（未加 --apply）。")


if __name__ == "__main__":
    # 中断（Ctrl-C / 异常）也必须回滚，否则常数文件会停在某个中间候选值上，
    # 而下一次运行会把它当成「基线」—— 教训 3 的那个坑。
    _hz_save = FD.read_const("kFitDampingHz")
    _sc_save = FD.read_const("kFitT60BudgetScale")
    try:
        main()
    except BaseException:
        FD.write_const("kFitDampingHz", _hz_save, "{:.1f}")
        FD.write_const("kFitT60BudgetScale", _sc_save)
        try:
            FD.rebuild()
        except Exception:
            pass
        print(f"\n[中断] 已回滚到 fc={_hz_save} scale={_sc_save}")
        raise
