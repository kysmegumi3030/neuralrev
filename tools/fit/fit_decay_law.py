"""重测并重拟合「DECAY 参数 → T60」律。

为什么要重做（`ref_decay_extreme.py` 的实测结论）：
  1. REFERENCE §7 里 norm=1.0 的「T60 ≈ 157 s」是**短窗外推的产物**。
     4 s 窗内该档只跌不到 2 dB，回归斜率基本是噪声。
     长窗（20 s）实测 T60 ≈ 47.8 s —— 参考在上端**并没有停止衰减**。
  2. 现行模型 `1/T60 = −0.117657·d + 0.907644` 在 d = 7.7145 s
     （norm 0.9619）穿过零点，之后靠 `1e3` 钳位。实测 1/T60 在上端
     仍为正且可测（0.0209 @ d=8.0），零点位置根本不存在。

所以：用**同一个足够长的窗**重扫全程，再拟合一个在整个定义域内
恒正、单调递减的形式。候选形式（都不越零）：

  A) 幂律距上界：  1/T60 = a · (D − d)^p ,  D > dmax
     物理直觉最好 —— 反馈 g→1 时 1/T60 → 0，D 是「g 恰好到 1」的
     虚拟参数值，实测 D > 8.0 说明参考在 norm=1 时 g 还没到 1。
  B) log 二次：     log(1/T60) = c0 + c1·d + c2·d²
     纯经验拟合，恒正但可能在域内出现极值（需检查单调性）。
  C) 指数拉伸：     1/T60 = a · exp(−b · d^q)

拟合口径用 **log(1/T60) 的残差**：1/T60 在全程跨 40 倍，
用线性残差会让低 decay 档独占权重，把上端的相对误差放飞。

窗长自适应：上端 T60 近 50 s，要测出 −5…−25 dB 段就得 ~20 s 以上。
统一用 --long（默认 25 s），低档位用不完也无害（EDC 自己会截）。

用法：
    python3 tools/fit/fit_decay_law.py                 # 只测+拟合，打印
    python3 tools/fit/fit_decay_law.py --write         # 拟合并写回 ReverbTuning.h
    python3 tools/fit/fit_decay_law.py --long 30
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V                              # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)
TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")

D_MIN, D_MAX = 0.5, 8.0          # DECAY 参数（秒）的真实范围，见 REFERENCE §2

# 扫描档位：低端稀、上端密（上端是曲率最大的地方，也是原模型失效的地方）
LEVELS = [0.00, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70,
          0.80, 0.86, 0.90, 0.94, 0.96, 0.98, 1.00]


def render_ref(r, norm, long_sec):
    n = BASE_AT + int(long_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    p = dict(drywet=1.0, predelay=0.5, decay=norm, lowcut=0.0, highcut=1.0)
    y = r.render(x, params={f"reverb_{k}": v for k, v in p.items()})
    return y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]


def edc_t60(y, lo_db=-5.0, hi_db=-25.0, sr=SR):
    """EDC 法（**仅用于低档位对照**，上端有系统性偏短）。

    ⚠ 截断偏置：反向累积能量在窗末必然归零，所以尾巴比窗长的档位
    会出现一个**人造膝点**，EDC 提前跌到 −25 dB，T60 被系统性低估。
    实测证据：同一 norm=1.0 档，20 s 窗给 47.8 s、25 s 窗给 60.2 s
    —— 结果随窗长漂移，即为截断偏置的指纹。
    另外 `d[-1]` 恒 ≈ −300 dB（归零所致），所以「窗内总跌幅」这个
    自适应放宽的判据是失效的，不能用它检测「窗不够长」。

    上端一律改用 `env_t60()`（RMS 包络回归），它没有截断偏置。
    """
    e = np.cumsum(y[::-1] ** 2)[::-1]
    if e[0] <= 0:
        return float("nan"), (0.0, 0.0), 0.0
    d = 10 * np.log10(np.maximum(e / e[0], 1e-30))
    m = (d <= lo_db) & (d >= hi_db)
    if m.sum() < 100:
        return float("nan"), (lo_db, hi_db), float(d[-1])
    t = np.arange(len(d))[m] / sr
    k, _ = np.polyfit(t, d[m], 1)
    return (float(-60.0 / k) if k < 0 else float("inf")), (lo_db, hi_db), float(d[-1])


def env_t60_auto(y, sr=SR, drop_db=40.0):
    """自适应窗长的包络回归：先用 EDC 粗估 T60，再取 win ≈ T60/40。

    为什么要自适应：固定 0.25 s 窗在快衰减档（T60≈1 s）只剩 8 个点，
    且跳过前 3 窗后回归段已落到 −45 dB 以下的深尾，
    那里可能是第二段斜率（双斜率衰减），测出来的不是主衰减。
    粗估只用来定窗长，偏 10% 完全无妨。
    """
    rough, _, _ = edc_t60(y, sr=sr)
    if not np.isfinite(rough) or rough <= 0:
        rough = 2.0
    win = float(np.clip(rough / 40.0, 0.01, 1.0))
    return env_t60(y, win_sec=win, sr=sr, drop_db=drop_db) + (win,)


def env_t60(y, win_sec=0.25, sr=SR, drop_db=40.0):
    """RMS 包络回归法（主口径）—— 无截断偏置。

    做法：逐 win_sec 求 RMS 的 dB，对**时间**线性回归取斜率 → T60。
    包络只反映「当前时刻的瞬时能量」，与窗末之后的内容无关，
    因此不会因为尾巴超出窗长而失真。

    有效区间的取法：
      * 起点跳过前 3 个窗（早期反射/扩散段还没进入指数衰减）；
      * 终点取「首峰下降 drop_db」处，或包络开始走平（触到数值地板）处，
        两者取先到的那个 —— 避免把地板段的水平线拉进回归。
    返回 (T60 秒, 用于回归的窗数, 回归段实际跨越的 dB)。
    """
    w = int(win_sec * sr)
    n = len(y) // w
    if n < 8:
        return float("nan"), 0, 0.0
    seg = y[:n * w].reshape(n, w)
    e = 20 * np.log10(np.maximum(np.sqrt(np.mean(seg ** 2, axis=1)), 1e-30))

    i0 = 3
    ref = float(np.max(e[i0:i0 + 4]))
    # 终点：跌够 drop_db，或包络不再单调下降（用 4 窗滑动均值判平）
    i1 = n
    for i in range(i0 + 4, n):
        if e[i] <= ref - drop_db:
            i1 = i
            break
    # 砍掉尾部可能的地板段：要求滑动均值仍在下降
    k = i1
    while k - i0 > 8:
        a = float(np.mean(e[k - 8:k - 4]))
        b = float(np.mean(e[k - 4:k]))
        if b < a - 0.05:      # 还在跌，可以留
            break
        k -= 1                # 走平了，往回缩
    i1 = max(k, i0 + 8)

    if i1 - i0 < 8:
        return float("nan"), i1 - i0, 0.0
    t = np.arange(i0, i1) * win_sec
    kk, _ = np.polyfit(t, e[i0:i1], 1)
    span = float(e[i0] - e[i1 - 1])
    return (float(-60.0 / kk) if kk < 0 else float("inf")), i1 - i0, span


# ---------------------------------------------------------------- 模型形式
def fit_power_to_bound(d, y, bounds=()):
    """A) 1/T60 = a·(D − d)^p。对 D 网格搜索，(log a, p) 有闭式最小二乘。

    bounds: [(d_sec, t60_lower)]，跨度不足档位的**下界**约束。
        这些档位不能进残差（斜率只反映窗内那一小段），但它们的
        「T60 至少这么长」是硬信息，必须让模型满足 —— 否则拟合会在
        定义域末端自由外推。实测代价：不加约束时最优 D=8.075，在 d=8.0
        处只给 258 s，而参考实测 ≥526 s，差 2 倍，DECAY=1.0 档因此
        衰减快了约 2 倍（ref_decay_extreme.py 的 A/B 抓到的就是这个）。
    """
    best = None
    # D 必须严格大于 d 的最大值，否则 log 无定义；上界给宽一点
    for D in np.arange(D_MAX + 0.02, D_MAX + 6.0, 0.005):
        u = np.log(D - d)
        A = np.vstack([np.ones_like(u), u]).T
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        aa, pp = float(np.exp(coef[0])), float(coef[1])
        # 下界约束：预测的 1/T60 不得高于 1/下界（即 T60 不得短于下界）
        if any(aa * max(D - db, 1e-9) ** pp > 1.0 / tb for db, tb in bounds):
            continue
        res = y - A @ coef
        rms = float(np.sqrt(np.mean(res ** 2)))
        if best is None or rms < best[0]:
            best = (rms, D, aa, pp)
    if best is None:
        return dict(kind="power", rms=float("inf"), D=float("nan"),
                    a=float("nan"), p=float("nan"),
                    f=lambda dd: np.full_like(np.asarray(dd, float), np.nan),
                    desc="无解（下界约束下 D 网格全被排除）")
    rms, D, a, p = best
    return dict(kind="power", rms=rms, D=D, a=a, p=p,
                f=lambda dd, D=D, a=a, p=p: a * np.power(np.maximum(D - dd, 1e-9), p),
                desc=f"1/T60 = {a:.6g}·({D:.4g} − d)^{p:.6g}")


def fit_log_quad(d, y):
    """B) log(1/T60) = c0 + c1·d + c2·d²。"""
    c = np.polyfit(d, y, 2)
    res = y - np.polyval(c, d)
    return dict(kind="logquad", rms=float(np.sqrt(np.mean(res ** 2))), c=c,
                f=lambda dd, c=c: np.exp(np.polyval(c, dd)),
                desc=f"log(1/T60) = {c[2]:.6g} + {c[1]:.6g}·d + {c[0]:.6g}·d²")


def fit_stretched_exp(d, y):
    """C) 1/T60 = a·exp(−b·d^q)。对 q 网格搜索，(log a, b) 闭式。"""
    best = None
    for q in np.arange(0.5, 6.0, 0.01):
        u = np.power(d, q)
        A = np.vstack([np.ones_like(u), u]).T
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        res = y - A @ coef
        rms = float(np.sqrt(np.mean(res ** 2)))
        if best is None or rms < best[0]:
            best = (rms, q, float(np.exp(coef[0])), float(-coef[1]))
    rms, q, a, b = best
    return dict(kind="sexp", rms=rms, a=a, b=b, q=q,
                f=lambda dd, a=a, b=b, q=q: a * np.exp(-b * np.power(dd, q)),
                desc=f"1/T60 = {a:.6g}·exp(−{b:.6g}·d^{q:.6g})")


def fit_linear_inv(d, y):
    """现行模型（线性 1/T60），只为对照 —— 会越零。"""
    inv = np.exp(y)
    k, b = np.polyfit(d, inv, 1)
    res = y - np.log(np.maximum(k * d + b, 1e-12))
    zero = -b / k if k < 0 else float("inf")
    return dict(kind="linear", rms=float(np.sqrt(np.mean(res ** 2))), k=k, b=b,
                f=lambda dd, k=k, b=b: k * dd + b,
                desc=f"1/T60 = {k:.6g}·d + {b:.6g}（零点 d={zero:.4f}）")


def monotone_ok(model):
    """在 [D_MIN, D_MAX] 上检查 1/T60 严格递减且恒正（模型形式的合法性）。"""
    dd = np.linspace(D_MIN, D_MAX, 2001)
    v = model["f"](dd)
    return bool(np.all(np.isfinite(v)) and np.all(v > 0) and np.all(np.diff(v) < 0))


def bounds_ok(model, bounds=()):
    """检查模型是否满足下界约束（T60 不得短于实测下界）。

    对所有模型形式统一施加，否则「谁的 rms 小」这个比较是不公平的 ——
    不受约束的形式可以靠在定义域末端乱跑来换取残差。
    """
    for db, tb in bounds:
        v = float(model["f"](np.array([db]))[0])
        if not np.isfinite(v) or v > 1.0 / tb:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--long", type=float, default=45.0, help="IR 窗长（秒）")
    ap.add_argument("--drop-floor", type=float, default=25.0,
                    help="包络回归至少要跨越的 dB；不够的档位只当下界")
    ap.add_argument("--write", action="store_true", help="把最优模型写回 ReverbTuning.h")
    a = ap.parse_args()

    drop_floor = a.drop_floor
    r = V.Vst3RefRenderer(sr=SR, block=512)
    print(f"参考侧重扫：{len(LEVELS)} 档，窗长 {a.long:.0f} s")
    print("主口径 = RMS 包络回归（无截断偏置）；EDC 仅作对照\n")
    print(f"{'norm':>6} {'d(s)':>7} {'包络T60':>9} {'1/T60':>9} "
          f"{'窗s':>6} {'窗数':>5} {'跨度dB':>8} {'EDC T60':>9} {'EDC/包络':>9}")

    d_list, inv_list, spans = [], [], []
    for v in LEVELS:
        y = render_ref(r, v, a.long)
        d_sec = D_MIN + (D_MAX - D_MIN) * v
        t60, nw, span, win = env_t60_auto(y)
        t60_edc, _, _ = edc_t60(y)
        if not np.isfinite(t60) or t60 <= 0:
            print(f"{v:6.2f} {d_sec:7.3f} {'—':>9}   测不出")
            continue
        d_list.append(d_sec)
        inv_list.append(1.0 / t60)
        spans.append(span)
        ratio = (t60_edc / t60) if np.isfinite(t60_edc) and t60 > 0 else float("nan")
        mark = "  ← 跨度不足，仅下界" if span < drop_floor else ""
        print(f"{v:6.2f} {d_sec:7.3f} {t60:9.3f} {1.0/t60:9.5f} "
              f"{win:6.3f} {nw:5d} {span:8.1f} {t60_edc:9.3f} {ratio:9.3f}{mark}")

    print("\n   EDC/包络 的比值：低档位应 ≈1（两法一致），上端 EDC 偏小即截断偏置。")
    print(f"   跨度 < {drop_floor:.0f} dB 的档位（窗内没跌够）只能当**下界**，"
          "不参与拟合。")

    # 跨度不足的档位不参与拟合：它们的斜率是「窗内看到的那一小段」，
    # 会把模型往「衰减更慢」的方向拽。上端恰好是曲率最大处，污染代价高。
    keep = [i for i, s in enumerate(spans) if s >= drop_floor]
    dropped = [(d_list[i], 1.0 / inv_list[i]) for i in range(len(d_list))
               if i not in keep]
    d_all, inv_all = np.array(d_list), np.array(inv_list)
    d_list = [d_list[i] for i in keep]
    inv_list = [inv_list[i] for i in keep]
    if dropped:
        print("   已排除：" + "、".join(
            f"d={dd:.3f}（T60≥{tt:.1f} s）" for dd, tt in dropped))

    d = np.array(d_list)
    y = np.log(np.array(inv_list))

    # 下界约束：跨度不足的档位虽不进残差，但「T60 至少这么长」要满足
    bnds = tuple(dropped)
    if bnds:
        print("   下界约束（不进残差，但模型必须满足）：" + "、".join(
            f"d={dd:.3f} ⇒ T60 ≥ {tt:.1f} s" for dd, tt in bnds))

    print("\n模型拟合（残差口径 = log(1/T60) 的 RMS，即相对误差）")
    models = [fit_power_to_bound(d, y, bnds), fit_log_quad(d, y),
              fit_stretched_exp(d, y), fit_linear_inv(d, y)]
    for m in models:
        mono, bok = monotone_ok(m), bounds_ok(m, bnds)
        t_end = 1.0 / float(m["f"](np.array([D_MAX]))[0]) if mono else float("nan")
        print(f"  {m['kind']:8s} rms={m['rms']:.5f}  "
              f"恒正且单调递减：{'✓' if mono else '✗'}  "
              f"满足下界：{'✓' if bok else '✗'}  "
              f"T60(d={D_MAX:.1f})={t_end:8.1f} s   {m['desc']}")

    legal = [m for m in models if monotone_ok(m) and bounds_ok(m, bnds)]
    if not legal:
        print("\n✗ 没有模型同时满足单调性与下界约束 —— 停止，不写回。")
        return
    best = min(legal, key=lambda m: m["rms"])
    print(f"\n→ 采用 {best['kind']}：{best['desc']}")
    print(f"   log 残差 RMS {best['rms']:.5f}"
          f"（≈ {100*(np.exp(best['rms'])-1):.1f}% 的 T60 相对误差）")

    print(f"\n逐档对照（实测 vs 采用模型 vs 现行线性模型）")
    lin = [m for m in models if m["kind"] == "linear"][0]
    cur_k, cur_b = -0.117657, 0.907644
    print(f"{'d(s)':>7} {'实测T60':>9} {'新模型':>9} {'相对%':>7} "
          f"{'现行T60':>10} {'相对%':>8}  备注")
    kept_d = set(np.round(d, 6))
    for dd, iv in zip(d_all, inv_all):
        t_meas = 1.0 / iv
        t_new = 1.0 / best["f"](dd)
        cur_inv = cur_k * dd + cur_b
        t_cur = 1.0 / cur_inv if cur_inv > 1e-3 else 1.0e3
        tag = "" if round(dd, 6) in kept_d else "  下界（未参与拟合）"
        print(f"{dd:7.3f} {t_meas:9.3f} {t_new:9.3f} "
              f"{100*(t_new/t_meas-1):+7.1f} {t_cur:10.2f} "
              f"{100*(t_cur/t_meas-1):+8.1f}{tag}")

    if a.write:
        if best["kind"] != "power":
            raise SystemExit(
                f"自动写回只实现了 power 形式，当前最优是 {best['kind']}；"
                "请手工把 t60FromDecaySec 改成对应形式。")
        s = open(TUNING).read()
        for name, val, fmt in (("kMeasInvT60Bound", best["D"], "{:.6f}"),
                               ("kMeasInvT60Scale", best["a"], "{:.8f}"),
                               ("kMeasInvT60Exponent", best["p"], "{:.6f}")):
            pat = re.compile(rf"({name}\s*=\s*)([-\d.eE+]+)")
            if not pat.search(s):
                raise SystemExit(f"ReverbTuning.h 里没有 {name}，请先加占位常数")
            s = pat.sub(lambda m: m.group(1) + fmt.format(val), s, count=1)
        open(TUNING, "w").write(s)
        print("\n已写回 ReverbTuning.h（kMeasInvT60Bound/Scale/Exponent）")


if __name__ == "__main__":
    main()
