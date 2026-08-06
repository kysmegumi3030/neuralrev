"""逐档标定 `kFitT60ScaleKnotVal`，目标是**宽带项**（不是逐带最差）。

## 为什么不用 fit_t60_scale_law.py

那个脚本有两处已经失效，且第二处是原理性的。

**一、搜索网格方向反了。** 它的 COARSE 是 [0.50 … 1.06]，为「T60 偏长、
需要往下压」那一代扫的（3 阶插值 + fc=18400 的环路）。现在插值提到 15 阶、
fc 收到 20000，环内损耗方向反过来：T60 **偏短**，五档里四档的最优点
（按 s* ≈ 1/(1+e) 估）落在 1.01…1.22，其中四个**超出它的上界 1.06**。
这是它自己注释里那条教训 7 的第三次复发 —— 动过结构就必须重扫网格边界，
而不是假设旧网格还框得住。本脚本的网格按当前实测残差定，并留双向余量。

**二、目标函数用「逐带最差」是错的。** 缩放**同倍**移动该档所有带，
所以它只能治**宽带**（所有带同向）的误差。而逐带残差里存在**符号分裂**：
decay=0.50 时锚定衰减率 D 在 125 Hz 是 +2.20、250 Hz 是 −2.27 dB/s。
拿 max 当目标，会让一条**压不动的带**主导整个扫描，把真正该压的宽带项挤掉
—— 这正是 §10.2.2 tilt shelf 踩过的坑（用一个标量去追一片逐带散布）。

## 目标量：1 kHz 锚点带的相对误差

    e(norm) = T60_候选(1 kHz) / T60_参考(1 kHz) − 1

为什么用 1 kHz 单带而不是各带平均：本轮已确立的分解是
**形状项**（频率相关，归 kFitDampingHz）+ **宽带项**（频率无关，归本常数），
两者由「锚定 1 kHz」正交化（见 §12.9.3）。1 kHz 就是那个锚点，
它的误差**定义上**是纯宽带项，不含任何形状成分。
用各带平均反而会把形状残差（4 kHz +1.07、8 kHz +2.78 dB/s 等）
混进目标，让本常数去追一个它治不了的量。

代价要说清：压平 1 kHz 不保证压平每一带。带间散布由拓扑决定，
本常数无能为力，脚本会把逐带范围如实报出来供对账。

## 逐档独立扫描是成立的

分段线性的每个节点只影响它自己那一档的读数（节点处邻居权重为 0）。
节点之间的插值不参与拟合 —— 保守假设，但避免了跨档耦合。

## 口径

T60 走 `t60_band_guard`（双尾长守卫 + 时间连通掩码）。**不要**绕过它：
- 尾长不够时回归段会滑到截断平台上（125 Hz 曾读出 3.39 vs 35.6 s）；
- 掩码若只按电平选点，峰前起振段会把 T60 拉长，且两侧不对称（§12.9.2）。

用法：
    python3 tools/fit/fit_t60_broadband.py             # 扫描，跑完回滚
    python3 tools/fit/fit_t60_broadband.py --apply     # 写入 ReverbTuning.h
    python3 tools/fit/fit_t60_broadband.py --norms 0.70
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
sys.path.insert(0, os.path.join(ROOT, "tools", "fit"))

from plugin_match import vst3_ref as V                              # noqa: E402
from t60_band_guard import (BANDS, SR, measure_guarded,             # noqa: E402
                            ref_guarded, rel_errors, tails_for)

TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

KNOTS = [0.20, 0.50, 0.70, 0.86, 0.94]
ANCHOR = "1 kHz"

# 目标带宽：|e| ≤ 该值即认为该档收敛，停止细化（再细化是在追测量噪声）。
# 1.5% 的依据：双尾长守卫的容差 TOL 是 5%，两次读数取均值后残余抖动约 1…2%。
TOL_PCT = 1.5

# 起点残差（本轮实测，1 kHz 相对误差 %）。仅用来定各档的初始搜索中心，
# 不是硬编码落点 —— 每档实际范围由 grid_for() 现算，且基线一律真测。
E0 = {0.20: -8.6, 0.50: -13.3, 0.70: -18.2, 0.86: -11.9, 0.94: -1.3}


def read_knots() -> list[float]:
    s = open(TUNING).read()
    m = re.search(r"kFitT60ScaleKnotVal\[kFitT60ScaleKnotCount\]\s*=\s*\{([^}]*)\}", s)
    if not m:
        raise KeyError("ReverbTuning.h 里找不到 kFitT60ScaleKnotVal")
    return [float(x) for x in m.group(1).replace("\n", "").split(",") if x.strip()]


def write_knots(vals: list[float]) -> None:
    """把节点表写回 ReverbTuning.h。

    失败判据用「正则是否匹配」，**不能**用「文本是否改变」：写入与当前值
    相同的一组数（基线试探、以及跑完回滚时必然发生）会产生逐字相同的文本，
    那时 s2 == s 是正常结果，不是错误。上一版按 s2 == s 报错，于是基线试探
    第一步就炸了。
    """
    s = open(TUNING).read()
    body = ", ".join(f"{v:.6f}" for v in vals)
    new = ("kFitT60ScaleKnotVal[kFitT60ScaleKnotCount] = {\n    " + body + "\n}")
    s2, n = re.subn(r"kFitT60ScaleKnotVal\[kFitT60ScaleKnotCount\]\s*=\s*\{[^}]*\}",
                    new, s, count=1)
    if n != 1:
        raise RuntimeError("ReverbTuning.h 里匹配不到 kFitT60ScaleKnotVal 的初始化式")
    open(TUNING, "w").write(s2)


def rebuild() -> None:
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:800])


def grid_for(norm: float) -> list[float]:
    """该档的粗网格：以 s* ≈ 1/(1+e0) 为中心，双向各留 ~8% 余量。

    双向留余量而不是单向：T60 对 s 未必严格线性（环内损耗也随 budget 变），
    线性外推给的只是初值。留双向余量才能让网格框住真实最优点 ——
    上一代脚本的网格就是因为只朝一个方向留余量而失效的。
    """
    e0 = E0.get(norm, 0.0) / 100.0
    center = 1.0 / (1.0 + e0)
    return [round(center + d, 4) for d in (-0.08, -0.04, -0.02, 0.0,
                                           +0.02, +0.04, +0.08)]


def probe(norm: float, idx: int, val: float, knots: list[float],
          ref: dict) -> tuple[float, dict]:
    """把第 idx 个节点设成 val，真编译 → 真渲染 → 真测量。

    返回 (1 kHz 相对误差%, 逐带相对误差 dict)。
    锚点带被守卫弃用时返回 inf —— 宁可跳过该点，也不用别的带替代
    （替代会把形状项混进宽带目标）。
    """
    k = list(knots)
    k[idx] = val
    write_knots(k)
    rebuild()
    cand = measure_guarded(norm)
    e = rel_errors(ref, cand)
    if ANCHOR not in e:
        return float("inf"), e
    return e[ANCHOR], e


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--norms", type=float, nargs="+", default=KNOTS)
    a = ap.parse_args()

    orig = read_knots()
    print(f"当前节点值 = {[f'{v:.4f}' for v in orig]}")
    print(f"目标：|1 kHz 相对误差| ≤ {TOL_PCT}%（宽带项）")

    r = V.Vst3RefRenderer(sr=SR, block=512)
    best = list(orig)
    summary: dict[float, tuple] = {}

    try:
        for norm in a.norms:
            idx = KNOTS.index(norm)
            ts, tl = tails_for(norm)
            print(f"\n{'=' * 70}\ndecay = {norm:.2f}   尾长 {ts:.0f}s / {tl:.0f}s"
                  f"\n{'=' * 70}")
            ref = ref_guarded(r, norm)
            print(f"参考侧稳定带 {len(ref)}/{len(BANDS)}")
            if ANCHOR not in ref:
                print(f"  ✗ 锚点带 {ANCHOR} 被守卫弃用 ⇒ 该档无法定宽带项，跳过")
                continue

            hist: dict[float, float] = {}

            # 基线一律真测（教训 3：不能拿上次的结果或解析估计当基线）
            e_base, eb = probe(norm, idx, orig[idx], best, ref)
            hist[orig[idx]] = e_base
            rng = (f"  逐带 [{min(eb.values()):+.1f} … {max(eb.values()):+.1f}]"
                   if eb else "")
            print(f"  s={orig[idx]:.4f}  1kHz {e_base:+6.2f}%{rng}   ←基线")
            sys.stdout.flush()

            for s in grid_for(norm):
                if s in hist or s <= 0:
                    continue
                e1, ee = probe(norm, idx, s, best, ref)
                hist[s] = e1
                rng = (f"  逐带 [{min(ee.values()):+.1f} … {max(ee.values()):+.1f}]"
                       if ee else "")
                print(f"  s={s:.4f}  1kHz {e1:+6.2f}%{rng}")
                sys.stdout.flush()
                if abs(e1) <= TOL_PCT:
                    break

            # 细化：在符号变化的相邻两点之间做一次线性插值（割线法一步）。
            # 只走一步是有意的：每次试探 = 一次真编译 + 4 次渲染，
            # 而 0.94 档单次渲染 51 s。收益递减，且 TOL 已按测量抖动定。
            valid = {s: e for s, e in hist.items() if e == e and abs(e) < 1e6}
            if valid and min(abs(v) for v in valid.values()) > TOL_PCT:
                pos = [(s, e) for s, e in valid.items() if e > 0]
                neg = [(s, e) for s, e in valid.items() if e < 0]
                if pos and neg:
                    s_p, e_p = min(pos, key=lambda t: t[1])
                    s_n, e_n = max(neg, key=lambda t: t[1])
                    s_i = round(s_n + (s_p - s_n) * (-e_n) / (e_p - e_n), 4)
                    if s_i not in hist and s_i > 0:
                        e_i, ei = probe(norm, idx, s_i, best, ref)
                        hist[s_i] = e_i
                        rng = (f"  逐带 [{min(ei.values()):+.1f} … "
                               f"{max(ei.values()):+.1f}]" if ei else "")
                        print(f"  s={s_i:.4f}  1kHz {e_i:+6.2f}%{rng}   ←割线细化")
                        sys.stdout.flush()

            valid = {s: e for s, e in hist.items() if e == e and abs(e) < 1e6}
            if not valid:
                print("  ✗ 该档无有效读数，保持原值")
                continue
            sb = min(valid, key=lambda s: abs(valid[s]))
            best[idx] = sb
            _, ef = probe(norm, idx, sb, best, ref)
            worst = max(abs(v) for v in ef.values()) if ef else float("nan")
            summary[norm] = (orig[idx], sb, e_base, valid[sb], worst, ef)
            print(f"  ⇒ 落点 {sb:.4f}   1kHz {e_base:+.2f}% → {valid[sb]:+.2f}%"
                  f"   逐带最差 {worst:.1f}%")

        print(f"\n{'=' * 70}\n拟合结果\n{'=' * 70}")
        print(f"{'decay':>7}{'原值':>9}{'落点':>9}{'1kHz前':>9}{'1kHz后':>9}"
              f"{'逐带最差':>10}")
        for nv in KNOTS:
            i = KNOTS.index(nv)
            if nv in summary:
                o, s, b, aa, w, _ = summary[nv]
                print(f"{nv:>7.2f}{o:>9.4f}{s:>9.4f}{b:>+9.2f}{aa:>+9.2f}{w:>10.1f}")
            else:
                print(f"{nv:>7.2f}{orig[i]:>9.4f}{best[i]:>9.4f}"
                      f"{'—':>9}{'—':>9}{'—':>10}")

        print("\n注：『逐带最差』不是本常数的目标，只作对账。它由带间散布决定，")
        print("    缩放同倍移动所有带、治不了符号分裂（§10.2.2 / §12.9）。")
        print("    若某档 1 kHz 已压平而逐带最差仍大 ⇒ 剩余量是拓扑账。")

        if a.apply:
            write_knots(best)
            rebuild()
            print("\n✓ 已写入 ReverbTuning.h 并重编译")
            print("  必须接着跑 tools/fit/band_report.py 复核平滑谱六档。")
        else:
            write_knots(orig)
            rebuild()
            print("\n（未加 --apply，已回滚并重编译）")
    except BaseException:
        write_knots(orig)
        rebuild()
        print("\n（异常退出，已回滚并重编译）")
        raise


if __name__ == "__main__":
    main()
