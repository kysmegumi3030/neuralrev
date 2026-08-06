"""标定 `kFitT60BudgetScale` —— 目标 T60 → 反馈增益预算的实现损耗修正。

问题来源（tools/measure/ref_decay_extreme.py 的实测）：
即使「参数 → T60」律已经对上（幂律模型相对参考 ≤3.5%），
候选**实际渲染出来**的 T60 仍系统性偏短。原因是环内除了 g 还串着
damping 一阶低通与被调制延迟线的线性插值，两者都额外吃能量。

**先修的是另一个 bug**：原先 8 条线共用一个 g，而线长差 2.4 倍
⇒ 每秒衰减率也差 2.4 倍，尾巴是 8 个不同指数的混合，T60 无定义。
改成逐线 g_i = 10^(−3·L_i/(T60·sr)) 后（WetCore::setDecay），
偏差形状才变得干净、可建模。

**两种修正模型的判决**（逐线增益就位后实测，8 档 norm 0.20…0.98）：
  A) 固定每圈损耗 ΔdB ⇒ 1/T60 出现恒定加性偏移。
     实测「1/T60 额外量」= 0.154、0.101、0.037、0.0104、0.0068、
     0.0035、0.0029、0.0024 —— 跨 65 倍，**不是常数** ⇒ 否证。
  B) 比例损耗 ⇒ T60_实际/T60_目标 恒定。
     实测 = 0.826、0.801、0.857、0.904、0.910、0.921、0.901、0.853
     —— 落在 0.80…0.92，基本恒定 ⇒ **采用**，α ≈ 1/0.87 ≈ 1.15…1.21。

（模型 A 也真的试过：单一常数只能同时照顾一端，扫到 0.012 dB 时
上端 −0.4…+3.5% 但低档位仍 −18…−22%。记录在此以免重走。）

标定做法：一维扫 kFitT60BudgetScale，目标 = 各档「候选 T60 / 参考 T60 − 1」
的绝对值之和（用相对误差，否则长 T60 档独占权重）。
参考侧 T60 只测一次并缓存（每档 45 s 渲染很贵）。

**落点 1.110000**，目标 1.0258 → 0.3253。逐档残差：
    norm     0.20  0.50  0.70  0.86  0.90  0.94  0.96  0.98
    相对%   −8.1  −8.8  −5.8  +0.5  +1.3  +1.4  −0.1  −6.6
上端 0.86–0.96 已在 ±1.4% 内。残差是 **U 形**不是平坦的 ⇒ 比例并非严格
恒定（damping 低通在不同衰减率下的作用时长不同）。要再压得让修正随档位
变化（分段或逐线标定），自由量更多；而「参数 → T60」律本身就有 3.1%
拟合误差，故停在单常数。

用法：
    python3 tools/fit/fit_t60_scale.py              # 扫描 + 落点
    python3 tools/fit/fit_t60_scale.py --no-write   # 只看不写
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
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402
from fit_decay_law import env_t60_auto                              # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

# 档位：覆盖全程，但上端要密（那里对 ΔdB 最敏感）。
# 排除 norm=1.0：它的 T60 在任何可承受的窗长里都只是下界（跨度 5 dB），
# 拿它当拟合目标等于拿噪声当目标。
LEVELS = [0.20, 0.50, 0.70, 0.86, 0.90, 0.94, 0.96, 0.98]

LONG_SEC = 45.0
MIN_SPAN_DB = 25.0          # 回归跨度门限，低于此的档位不计入目标


def params(decay):
    return dict(drywet=1.0, predelay=0.5, decay=decay, lowcut=0.0, highcut=1.0)


def render_ref(r, decay):
    n = BASE_AT + int(LONG_SEC * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    y = r.render(x, params={f"reverb_{k}": v for k, v in params(decay).items()})
    return y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]


def render_cand(decay):
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(LONG_SEC * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return c.render(x, params=params(decay)).astype(np.float64)[0][BASE_AT:]


def write_const(name, value, fmt="{:.6f}"):
    s = open(TUNING).read()
    pat = re.compile(rf"({name}\s*=\s*)([-\d.eE+]+)")
    if not pat.search(s):
        raise KeyError(f"ReverbTuning.h 里找不到 {name}")
    open(TUNING, "w").write(pat.sub(lambda m: m.group(1) + fmt.format(value),
                                    s, count=1))


def rebuild():
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:800])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()

    r = V.Vst3RefRenderer(sr=SR, block=512)

    print(f"参考侧 T60（窗长 {LONG_SEC:.0f} s，包络回归口径）")
    ref_t60, use = {}, []
    for v in LEVELS:
        t60, _nw, span, _w = env_t60_auto(render_ref(r, v))
        ref_t60[v] = t60
        ok = np.isfinite(t60) and span >= MIN_SPAN_DB
        if ok:
            use.append(v)
        print(f"  norm {v:.2f}  T60 = {t60:8.3f} s  跨度 {span:5.1f} dB"
              f"{'' if ok else '   ← 跨度不足，不计入目标'}")
    print(f"\n计入目标的档位：{use}")

    def objective(verbose=False):
        tot = 0.0
        rows = []
        for v in use:
            t60, _nw, span, _w = env_t60_auto(render_cand(v))
            rel = t60 / ref_t60[v] - 1.0 if np.isfinite(t60) else 10.0
            tot += abs(rel)
            rows.append((v, t60, rel))
        if verbose:
            for v, t60, rel in rows:
                print(f"      norm {v:.2f}  候选 {t60:8.3f}  "
                      f"参考 {ref_t60[v]:8.3f}  相对 {100*rel:+7.1f}%")
        return tot, rows

    NAME = "kFitT60BudgetScale"
    m0 = re.search(rf"{NAME}\s*=\s*([-\d.eE+]+)", open(TUNING).read())
    orig = float(m0.group(1)) if m0 else None

    print(f"\n扫描 {NAME}：")
    best = None
    # 范围依据：实测 T60_实际/T60_目标 ∈ [0.80, 0.92] ⇒ 需放大 1.09…1.25 倍。
    for val in [1.00, 1.09, 1.13, 1.15, 1.18, 1.21, 1.25, 1.30]:
        write_const(NAME, val, "{:.6f}")
        rebuild()
        obj, _ = objective()
        flag = ""
        if best is None or obj < best[0]:
            best = (obj, val)
            flag = "  <-- best"
        print(f"  {NAME} = {val:.6f}   目标 = {obj:.4f}{flag}")

    step = 0.01
    center = best[1]
    print(f"\n细扫（{center:.4f} ± {2*step:.2f}）：")
    for val in [center + k * step for k in (-2, -1, 1, 2)]:
        if val <= 0:
            continue
        write_const(NAME, val, "{:.6f}")
        rebuild()
        obj, _ = objective()
        flag = ""
        if obj < best[0]:
            best = (obj, val)
            flag = "  <-- best"
        print(f"  {NAME} = {val:.6f}   目标 = {obj:.4f}{flag}")

    final = orig if (a.no_write and orig is not None) else best[1]
    write_const(NAME, final, "{:.6f}")
    rebuild()
    print(f"\n→ {NAME} = {best[1]:.6f}（目标 {best[0]:.4f}）")
    if a.no_write:
        print(f"   --no-write：文件已还原为 {final:.6f}")
    print("   落点逐档明细：")
    objective(verbose=True)


if __name__ == "__main__":
    main()
