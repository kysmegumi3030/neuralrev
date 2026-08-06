"""测出 `kFitT60BudgetScale` **应当随档位怎么变**，并判决它服从哪个损耗模型。

## 为什么需要这个（问题的由来）

`kFitT60BudgetScale` 现在是**单一常数** 1.01。它在 decay=0.5 附近是对的
（逐带 T60 最差 9.5%，符号正负交替），但在上端一律偏短：

    norm    0.90    0.94    0.96    0.98
    相对%  −10.5   −8.5    −7.8    −6.5

即**上端需要更大的 scale**（旧值 1.13 在上端反而漂亮，见 REFERENCE §12.1）。
一个常数照顾不了两端 —— 这与 §7.4 结尾的老结论同形，只是那时说的是低档位。

## 但**不能直接**去拟合一个「随档位变化」的函数

先得知道它**该长什么样**。两个候选模型（§7.4 判过一次，但那次是在
**线性插值年代**、且只跨档位不跨 scale 判的，现在必须重判）：

    A) 固定每圈损耗 Δ dB（damping + 插值，与 g 无关）
       ⇒ 1/T60_实际 = 1/(T60_目标·s) + ε   ，ε = Δ·sr/(60·L) 为常数
       ⇒ 所需 s = 1/(1 − ε·T60_目标)      ← **随目标 T60 单调上升，且有极点**
       极点 T60 = 1/ε 就是网络的天花板（g→1 也到不了），与 §12.4 的饱和现象同源。

    B) 比例损耗
       ⇒ 1/T60_实际 = (1/s + k)/T60_目标 ，k 为常数
       ⇒ 所需 s = 1/(1 − k)               ← **与档位无关**（即常数就够）

**观测事实已经排除了 B**：若 B 成立，一个常数就能同时对上两端，而实测做不到。
所以真实行为至少含有 A 的成分。本工具要定量测出 ε，并检验它是否真的是常数。

## 判决 A 与 B 的关键实验：**同一档位、两个不同的 s**

这是 §7.4 当年没做的那一刀。定义每档的「额外损耗率」

    ε_eff ≡ 1/T60_实际 − 1/(T60_目标·s)

- A 成立 ⇒ ε_eff **与 s 无关**（它是环内固定损耗，g 变大变小都一样）；
- B 成立 ⇒ ε_eff = k/(T60_目标·s)，即**与 s 成反比**。

只跨档位测是分不开的（两个模型都能给出「ε_eff 随档位变」的表象），
必须同一档位换 s 再测一次。s 是编译期常数、decay 是运行期参数
⇒ 总共只需 **2 次重编译**，每个 s 下把所有档位跑一遍。

## 目标定义：对齐**参考**，不是对齐模型

所需 scale 直接按「让候选 T60 等于**参考实测** T60」解：

    1/T60_参考 = 1/(T60_目标·s_req) + ε_eff
    ⇒ s_req = 1 / ( T60_目标 · (1/T60_参考 − ε_eff) )

这样连 §7.2 那条律本身的 4.4% 拟合误差也一并被吸收掉 —— 我们要的是
最终对上参考，而不是对上中间模型。

## 测法纪律（照抄既有口径，勿改）

- T60 一律用 **RMS 包络线性回归**，不用 EDC（反向累积在窗末必然归零，
  尾巴超窗长时 T60 被系统性低估且随窗长漂移 —— §7.0 踩过）；
- 包络窗自适应取 T60/40；跳过前 3 个窗（早期扩散段不是指数）；
- 报告回归**跨度** span_dB，跨度 < 25 dB 的读数只当下界、**不进拟合**；
- 渲染窗长按各档预期 T60 给足（见 WINDOW_PRIOR 的说明）。

用法：
    python3 tools/fit/diag_t60_scale_law.py          # 跑完自动回滚 scale
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

from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)

TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

# 拟合档位。**必须同时覆盖中段与上端** —— 这正是 §7.6 那次拟合漏掉的维度
# （教训 7：拟合集要覆盖所有已对齐过的维度，不只是当前要修的那个）。
# 上限取 0.94：0.96 以上参考 T60 ≥37 s，45 s 窗内跌不到 25 dB，
# 读数只是下界，进拟合会把整条曲线拽偏（§7.0 / §12.2 的老坑）。
DECAYS = [0.20, 0.50, 0.70, 0.86, 0.94]

# 各档预期 T60（秒），**只用来决定渲染窗长与包络窗**，不作为任何结果。
# 来源 REFERENCE §7.1 的参考实测表。窗长取 ~1.8×T60 并夹在 [6, 45] s：
# 太短则回归跨度不够，太长则每次渲染都在算已经衰到底噪的部分。
WINDOW_PRIOR = {0.20: 1.371, 0.50: 2.469, 0.70: 4.476, 0.86: 10.258, 0.94: 24.817}

# 判决实验用的两个 scale。取当前落点与旧落点：跨度 12%，
# 足够把「ε_eff 与 s 无关」和「ε_eff ∝ 1/s」分开（两者预测差约 12%）。
PROBE_SCALES = [1.01, 1.13]

_ref_cache: dict = {}


def write_const(name, value, fmt="{:.6f}"):
    s = open(TUNING).read()
    pat = re.compile(rf"({name}\s*=\s*)([-\d.eE+]+)")
    if not pat.search(s):
        raise KeyError(f"ReverbTuning.h 里找不到 {name}")
    open(TUNING, "w").write(pat.sub(lambda m: m.group(1) + fmt.format(value),
                                    s, count=1))


def read_const(name):
    m = re.search(rf"{name}\s*=\s*([-\d.eE+]+)", open(TUNING).read())
    if not m:
        raise KeyError(f"ReverbTuning.h 里找不到 {name}")
    return float(m.group(1))


def rebuild():
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:800])


def env_t60(x, t60_prior):
    """RMS 包络线性回归求 T60，返回 (T60, 回归跨度 dB)。

    不用 EDC：见模块文档串的「测法纪律」。
    """
    win = max(int(0.005 * SR), int(t60_prior / 40.0 * SR))
    k = np.ones(win) / win
    e = np.sqrt(np.convolve(x.astype(np.float64) ** 2, k, mode="valid") + 1e-300)
    db = 20 * np.log10(e)
    t = np.arange(len(db)) / SR
    # 跳过前 3 个窗：早期反射/扩散还不是指数段
    s0 = min(3 * win, max(0, len(db) - 8))
    db, t = db[s0:], t[s0:]
    if len(db) < 32:
        return float("nan"), 0.0
    pk = float(db.max())
    m = (db <= pk - 5.0) & (db >= pk - 45.0)
    if m.sum() < 32:
        return float("nan"), 0.0
    span = float(db[m].max() - db[m].min())
    A = np.vstack([t[m], np.ones(int(m.sum()))]).T
    slope = float(np.linalg.lstsq(A, db[m], rcond=None)[0][0])
    if slope >= -1e-9:
        return float("nan"), span
    return -60.0 / slope, span


def tail_for(norm):
    return float(np.clip(1.8 * WINDOW_PRIOR[norm], 6.0, 45.0))


def ref_t60(r, norm):
    """参考侧与候选常数无关，缓存。"""
    if norm not in _ref_cache:
        n = BASE_AT + int(tail_for(norm) * SR)
        x = np.zeros(n, dtype=np.float32)
        x[BASE_AT] = 1.0
        P = dict(drywet=1.0, predelay=0.5, decay=norm, lowcut=0.0, highcut=1.0)
        y = r.render(x, params={f"reverb_{k}": v for k, v in P.items()})
        ir = y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]
        _ref_cache[norm] = env_t60(ir, WINDOW_PRIOR[norm])
    return _ref_cache[norm]


def cand_t60(norm):
    n = BASE_AT + int(tail_for(norm) * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    P = dict(drywet=1.0, predelay=0.5, decay=norm, lowcut=0.0, highcut=1.0)
    c = NrevRenderer(sr=SR, block=512)
    ir = c.render(x, params=P).astype(np.float64)[0][BASE_AT:]
    return env_t60(ir, WINDOW_PRIOR[norm])


def target_t60(norm):
    """复现 ReverbTuning.h 的 t60FromDecaySec(decaySecFromNorm(norm))。

    必须与头文件逐字一致，否则解出来的 ε_eff 里会混进模型不一致的偏差。
    """
    bound = read_const("kMeasInvT60Bound")
    scale = read_const("kMeasInvT60Scale")
    expo = read_const("kMeasInvT60Exponent")
    dmin, dmax = 0.5, 8.0
    d = dmin + (dmax - dmin) * norm
    slack = bound - d
    if slack <= 0.0:
        return 1.0e4
    inv = scale * (slack ** expo)
    return 1.0 / inv if inv > 1.0e-4 else 1.0e4


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()

    orig_scale = read_const("kFitT60BudgetScale")
    r = V.Vst3RefRenderer(sr=SR, block=512)

    print("参考实测 T60（缓存一次，与 scale 无关）")
    print("  norm   目标T60(模型)   参考T60    跨度")
    tgt, ref = {}, {}
    for nv in DECAYS:
        tgt[nv] = target_t60(nv)
        t, sp = ref_t60(r, nv)
        ref[nv] = t
        mark = "" if sp >= 25.0 else "  ← 跨度不足，仅参考"
        print(f"  {nv:.2f}   {tgt[nv]:9.3f}   {t:8.3f}  {sp:6.1f}{mark}")

    # ---- 判决实验：同一档位、两个 s ----
    eps: dict = {}
    for sc in PROBE_SCALES:
        write_const("kFitT60BudgetScale", sc)
        rebuild()
        print(f"\n候选 @ kFitT60BudgetScale = {sc:.4f}")
        print("  norm   候选T60    跨度    相对参考%     ε_eff(1/s)")
        eps[sc] = {}
        for nv in DECAYS:
            t, sp = cand_t60(nv)
            if not (t == t):
                print(f"  {nv:.2f}        n/a")
                eps[sc][nv] = float("nan")
                continue
            rel = (t / ref[nv] - 1.0) * 100.0
            e = 1.0 / t - 1.0 / (tgt[nv] * sc)
            eps[sc][nv] = e
            print(f"  {nv:.2f}   {t:8.3f}  {sp:6.1f}   {rel:+8.1f}    {e:+.6f}")

    # ---- 模型判决 ----
    a, b = PROBE_SCALES
    print("\n=== 模型判决 ===")
    print("  A) 固定每圈损耗  ⇒ ε_eff 与 s **无关**（比值应 ≈ 1.00）")
    print(f"  B) 比例损耗      ⇒ ε_eff ∝ 1/s（比值应 ≈ {b / a:.3f}）")
    print(f"\n  norm   ε_eff@{a:.2f}   ε_eff@{b:.2f}   比值(前/后)")
    ratios = []
    for nv in DECAYS:
        ea, eb = eps[a][nv], eps[b][nv]
        if not (ea == ea and eb == eb) or eb == 0:
            continue
        ratios.append(ea / eb)
        print(f"  {nv:.2f}   {ea:+.6f}   {eb:+.6f}   {ea / eb:8.3f}")
    if ratios:
        mr = float(np.mean(ratios))
        print(f"\n  比值均值 {mr:.3f}   "
              f"（A 预测 1.000，B 预测 {b / a:.3f}）")
        print("  判决：" + ("**A（固定每圈损耗）**" if abs(mr - 1.0) < abs(mr - b / a)
                          else "**B（比例损耗）**"))

    # ---- ε 是否为常数（A 的进一步检验）----
    print("\n=== ε 跨档位是否恒定（A 要求恒定）===")
    for sc in PROBE_SCALES:
        v = [eps[sc][nv] for nv in DECAYS if eps[sc][nv] == eps[sc][nv]]
        if v:
            print(f"  s={sc:.2f}: " + "  ".join(f"{x:+.5f}" for x in v)
                  + f"   离散 {max(v) / min(v):.2f}×" if min(v) > 0 else "")

    # ---- 所需 scale 与推荐落点 ----
    print("\n=== 每档所需 scale（对齐参考实测）===")
    print("  s_req = 1 / ( T60_目标 · (1/T60_参考 − ε_eff) )")
    print("\n  norm   T60_目标   T60_参考   s_req@ε(1.01)  s_req@ε(1.13)")
    for nv in DECAYS:
        row = [f"  {nv:.2f}   {tgt[nv]:8.3f}   {ref[nv]:8.3f}"]
        for sc in PROBE_SCALES:
            e = eps[sc][nv]
            if not (e == e):
                row.append("      n/a    ")
                continue
            den = 1.0 / ref[nv] - e
            row.append(f"   {1.0 / (tgt[nv] * den):10.4f}" if den > 0
                       else "   不可达(ε过大)")
        print("".join(row))

    print("\n  「不可达」= 该档的环内损耗已超过参考的总衰减率，"
          "任何 g 都到不了 ⇒ 那是天花板，不是标定问题。")

    write_const("kFitT60BudgetScale", orig_scale)
    rebuild()
    print(f"\n（已回滚 kFitT60BudgetScale = {orig_scale:.6f}）")


if __name__ == "__main__":
    main()
