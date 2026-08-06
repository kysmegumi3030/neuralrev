"""逐档标定 `kFitT60ScaleKnotVal` —— 让 T60 预算倍数随 DECAY 档变化。

╔══════════════════════════════════════════════════════════════════════════╗
║ **本脚本已作废（2026-08-05），不要运行。** 替代品：fit_t60_broadband.py    ║
║                                                                          ║
║ 两处失效，各自独立成立（详见 docs/REFERENCE.md §12.10.1）：               ║
║                                                                          ║
║  1. **搜索网格方向反了。** COARSE = [0.50 … 1.06] 是为「T60 偏长、需要往下 ║
║     压」那一代扫的（3 阶插值 + fc=18400）。插值提到 15 阶、fc 收到 20000  ║
║     之后环内损耗方向反过来（T60 偏短），五档里四档的最优点落在 1.06       ║
║     **之上**（实测最高 1.2425）⇒ 本网格只会给出贴着上界的伪最优。        ║
║                                                                          ║
║  2. **目标函数用「逐带最差」是原理性错误。** 缩放同倍移动该档所有带 ⇒     ║
║     只能平移、不能收缩带间跨度；而逐带残差存在符号分裂（decay=0.50 的     ║
║     锚定 D：125 Hz +2.20、250 Hz −2.27 dB/s）。拿 max 当目标，会让一条    ║
║     **压不动的带**绑架整个扫描，把真正该压的宽带项挤掉 —— 与 §10.2.2     ║
║     tilt shelf 同一个坑。正确目标是 1 kHz 锚点带（定义上的纯宽带项）。   ║
║                                                                          ║
║ 另外它下面那张基线表是在**旧掩码**（非时间连通，§12.9.2）上测的，        ║
║ 数字本身也不可引用。                                                     ║
║                                                                          ║
║ 保留原因：下面的推导过程仍是「为什么需要逐档自由度」的完整记录。          ║
╚══════════════════════════════════════════════════════════════════════════╝

## 这个脚本存在的理由（以及它推翻了什么）

单常数 `kFitT60BudgetScale = 1.01` 的已知代价写在 REFERENCE §12.6：上端
DECAY 档 T60「一律偏短 6.5…10.5%」。**那组数字是全带口径测的，不可用于建律。**

用尾长稳定的逐带口径重测同一个 build（scale=1.01）：

    decay   逐带相对误差范围        最差    整体缩放最优倍数
    0.20    −11.0 … +35.2 %       35.2%   0.892
    0.50     −9.4 …  +9.4 %        9.4%   1.000
    0.70     −8.4 …  −6.2 % (n=5)   8.4%   1.078
    0.86    −13.2 …  +5.5 %       13.2%   1.040
    0.94     −9.2 … +18.5 %       18.5%   0.956

与全带口径的结论**方向相反**：全带说每档都要往上推（1.068…1.204），逐带说
0.20 与 0.94 要往下。0.94 档全带读「一律偏短」，而 4 k/8 k 实际偏长 +16…+18%
—— 全带把一次符号反转平均成了单向亏空（成因见 diag_t60_caliber.py：
候选逐带离散度 25.2× > 参考 20.0×，单直线拟合必然比任何单带都陡）。

**所以本脚本只用逐带口径，且必须走 t60_band_guard（尾长稳定）。**
不走 guard 会踩另一个坑：同一个 band_t60，尾长 6 s 与 8 s 在 125 Hz 上
读出 3.39 s 与 35.6 s。

## 目标函数

对每个节点档位**独立**扫描：

    min over s:  max over bands | T60_候选(s) / T60_参考 − 1 |

取 max 而非和式，与验收口径同形（逐带最差），理由同 fit_damping_t60.py。

逐档独立是**成立的**，因为分段线性的每个节点只影响它自己那一档的读数
（节点处 t=0 或 1，邻居权重为 0）。节点之间的插值不参与拟合，是保守假设。

## 单档能达到的下限（先算清，避免拟合一个拟不动的目标）

整体缩放同倍移动该档的所有带，所以单档最好成绩由该档的**带间跨度**决定：
s_opt = 2/((1+e_max)+(1+e_min))，此时最差 = 半跨度。上表最后一列就是它。
0.20 档半跨度 20.6%、0.94 档 13.2% —— **这两档缩放后仍不合格**，那是带间
跨度（线长/混合矩阵的量）决定的，不是本常数能压的。本脚本只负责把每档压到
它自己的下限，并把剩余量如实报出来。

用法：
    python3 tools/fit/fit_t60_scale_law.py            # 扫描，跑完回滚
    python3 tools/fit/fit_t60_scale_law.py --apply    # 写入 ReverbTuning.h
    python3 tools/fit/fit_t60_scale_law.py --norms 0.94
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
from t60_band_guard import (BANDS, SR, measure_guarded,              # noqa: E402
                            ref_guarded, rel_errors, tails_for)

TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

KNOTS = [0.20, 0.50, 0.70, 0.86, 0.94]

# 粗网格。
#
# 原先是 [0.86 … 1.10]，覆盖「插值器为 3 阶、kFitDampingHz=18400」那一代的
# 实测落点（0.89…1.08）。**该网格现已不够用**：把 kArchFracInterpOrder 提到 9
# 并把 damping 放松到 24000 之后，环内损耗大幅减少，上端档位的 T60 变得**偏长**
# （0.86 档 +56.8%、0.94 档 +97.9%），需要的 scale 落在 0.86 以下。
# 这正是教训 7 的复发：动过结构（插值器阶数）就必须重扫所有既有补偿常数，
# 而不是假设旧网格还框得住新的最优点。下界放到 0.50 以留足余量。
COARSE = [0.50, 0.58, 0.66, 0.74, 0.82, 0.90, 0.98, 1.06]
REFINE_STEP = 0.01          # 细化步长（邻域 ±1 格）


def read_knots() -> list[float]:
    s = open(TUNING).read()
    m = re.search(r"kFitT60ScaleKnotVal\[kFitT60ScaleKnotCount\]\s*=\s*\{([^}]*)\}", s)
    if not m:
        raise KeyError("ReverbTuning.h 里找不到 kFitT60ScaleKnotVal")
    return [float(x) for x in m.group(1).replace("\n", "").split(",") if x.strip()]


def write_knots(vals: list[float]) -> None:
    s = open(TUNING).read()
    body = ", ".join(f"{v:.6f}" for v in vals)
    new = ("kFitT60ScaleKnotVal[kFitT60ScaleKnotCount] = {\n    "
           + body + "\n}")
    s2 = re.sub(r"kFitT60ScaleKnotVal\[kFitT60ScaleKnotCount\]\s*=\s*\{[^}]*\}",
                new, s, count=1)
    if s2 == s:
        raise RuntimeError("写入 kFitT60ScaleKnotVal 失败")
    open(TUNING, "w").write(s2)


def rebuild() -> None:
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:800])


def worst_at(norm: float, idx: int, val: float, knots: list[float],
             ref: dict) -> tuple[float, dict]:
    """把第 idx 个节点设成 val，真重编译 → 真渲染 → 真测量，返回该档最差%。"""
    k = list(knots)
    k[idx] = val
    write_knots(k)
    rebuild()
    cand = measure_guarded(norm)
    e = rel_errors(ref, cand)
    if not e:
        return float("inf"), {}
    return max(abs(v) for v in e.values()), e


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--norms", type=float, nargs="+", default=KNOTS)
    a = ap.parse_args()

    orig = read_knots()
    print(f"当前节点值 = {[f'{v:.4f}' for v in orig]}")

    r = V.Vst3RefRenderer(sr=SR, block=SR // 94)  # block 不影响离线渲染结果
    best = list(orig)

    try:
        for norm in a.norms:
            idx = KNOTS.index(norm)
            ts, tl = tails_for(norm)
            print(f"\n{'=' * 66}\ndecay = {norm:.2f}   尾长 {ts:.0f}s / {tl:.0f}s"
                  f"\n{'=' * 66}")
            ref = ref_guarded(r, norm)
            print(f"参考侧稳定带 {len(ref)}/{len(BANDS)}："
                  f"{', '.join(f'{k} {v:.3f}' for k, v in ref.items())}")
            if len(ref) < 3:
                print("  ✗ 稳定带不足 3 条，跳过该档（读数不足以支撑拟合）")
                continue

            # ---- 粗扫 ----
            hist: dict[float, float] = {}
            for s in COARSE:
                w, e = worst_at(norm, idx, s, best, ref)
                hist[s] = w
                rng = (f"  [{min(e.values()):+.1f} … {max(e.values()):+.1f}]"
                       if e else "")
                print(f"  s={s:.4f}  最差 {w:6.2f}%{rng}")
                sys.stdout.flush()

            s0 = min(hist, key=hist.get)

            # ---- 邻域细化 ----
            lo, hi = s0 - 0.04, s0 + 0.04
            fine = [round(x, 4) for x in np.arange(lo, hi + 1e-9, REFINE_STEP)]
            for s in fine:
                if s in hist or s <= 0:
                    continue
                w, e = worst_at(norm, idx, s, best, ref)
                hist[s] = w
                rng = (f"  [{min(e.values()):+.1f} … {max(e.values()):+.1f}]"
                       if e else "")
                print(f"  s={s:.4f}  最差 {w:6.2f}%{rng}   ←细化")
                sys.stdout.flush()

            sb = min(hist, key=hist.get)
            best[idx] = sb
            print(f"  ⇒ decay={norm:.2f} 落点 {sb:.4f}，最差 {hist[sb]:.2f}%"
                  f"（起点 {hist.get(1.0, float('nan')):.2f}%）")

        print(f"\n{'=' * 66}\n拟合结果")
        for i, nv in enumerate(KNOTS):
            print(f"  decay={nv:.2f}   {orig[i]:.4f} → {best[i]:.4f}")

        if a.apply:
            write_knots(best)
            rebuild()
            print("\n✓ 已写入 ReverbTuning.h 并重编译")
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
