"""把候选混响的结构常数拟合到参考实测 —— 主目标是 1/12 oct 平滑谱误差。

拟合的自由量（都在 src/dsp/ReverbTuning.h 里，脚本只改这一个文件）：
    kWetTrim            湿声总增益配平
    kFitDampingHz       环内固定 damping 低通的 fc
    kFitFilterQ         LOW/HIGH CUT 的 Q
    kArchLinesA/B       两路 FDN 的延迟线长度集合（按整体缩放因子搜索）
    kArchDiffusersA/B   输入扩散 allpass 长度
    kArchDiffuserGain   扩散增益

被实测钉死、**不参与拟合**的量（改动即偏离实测）：
    5 个参数的映射律、DRY/WET 的两条闭式、湿声起点 477/617 样点、
    PRE-DELAY 的并联拓扑、1/T60 对 DECAY 的线性关系。

策略：坐标下降。每轮只动一组常数，用「关键档位的平滑谱误差之和」作目标，
逐组扫描取最优后写回 ReverbTuning.h 并重编译。
先粗后细，避免在高维上盲搜。

用法：
    python3 tools/fit/fit_network.py --stage trim      # 只标定湿声电平
    python3 tools/fit/fit_network.py --stage damping   # 只调 damping fc
    python3 tools/fit/fit_network.py --stage scale     # 只调延迟线整体缩放
    python3 tools/fit/fit_network.py --stage all
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
from plugin_match.nrev_cand import NrevRenderer, smoothed_spectrum_err_db  # noqa: E402

SR = 48000
REF_LATENCY = 51
IMPULSE_AT = int(2.0 * SR)
TAIL_SEC = 4.0
TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

# 拟合用档位（覆盖每个参数的作用范围，但保持数量可控）
FIT_POINTS = [
    ("default",   dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)),
    ("decay-min", dict(drywet=1.0, predelay=0.5, decay=0.0, lowcut=0.0, highcut=1.0)),
    ("decay-hi",  dict(drywet=1.0, predelay=0.5, decay=0.8, lowcut=0.0, highcut=1.0)),
    ("lowcut",    dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=1.0, highcut=1.0)),
    ("highcut",   dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=0.0)),
]


# ---------------------------------------------------------------- 参考侧缓存
_ref_cache: dict[str, np.ndarray] = {}


def ref_ir(r, name, params):
    if name in _ref_cache:
        return _ref_cache[name]
    n = IMPULSE_AT + int(TAIL_SEC * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    y = r.render(x, params={f"reverb_{k}": v for k, v in params.items()})
    _ref_cache[name] = y.astype(np.float64)[0][IMPULSE_AT + REF_LATENCY:]
    return _ref_cache[name]


def cand_ir(c, params):
    n = IMPULSE_AT + int(TAIL_SEC * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return c.render(x, params=params).astype(np.float64)[0][IMPULSE_AT:]


# ---------------------------------------------------------------- 常数读写
def read_const(name):
    s = open(TUNING).read()
    m = re.search(rf"{name}\s*(?:=|\{{)\s*([-\d.eE+]+)", s)
    if not m:
        raise KeyError(f"在 ReverbTuning.h 里找不到常数 {name}")
    return float(m.group(1))


def write_const(name, value, fmt="{:.6f}"):
    """把常数写回 ReverbTuning.h。

    判据必须是「正则**是否匹配到**」，不能用「新旧文本是否不同」：
    扫描时第一个试探值往往正好等于文件里的当前值（例如 damping 的
    起点 800.0），此时文本不变但写入是成功的。用文本比较会误报失败。
    """
    s = open(TUNING).read()
    pat = re.compile(rf"({name}\s*(?:=|\{{)\s*)([-\d.eE+]+)")
    if not pat.search(s):
        raise KeyError(f"在 ReverbTuning.h 里找不到常数 {name}")
    open(TUNING, "w").write(
        pat.sub(lambda m: m.group(1) + fmt.format(value), s, count=1))


def write_wet_trim(value):
    """kWetTrim 定义在 ReverbEffect.h（它是 effect 级的配平，不是 tuning 表项）。"""
    path = os.path.join(ROOT, "src", "dsp", "ReverbEffect.h")
    s = open(path).read()
    new = re.sub(r"(kWetTrim\s*=\s*)([-\d.eE+]+)f",
                 lambda m: m.group(1) + f"{value:.6f}f", s, count=1)
    if new == s:
        raise KeyError("写入 kWetTrim 失败")
    open(path, "w").write(new)


def rebuild():
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:800])


def write_line_set(name, values):
    """整表替换 kArchLinesA/B 或 kArchDiffusersA/B 的数字列表。"""
    s = open(TUNING).read()
    body = ", ".join(str(int(v)) for v in values)
    pat = rf"(kArch{name}\s*(?:\{{|=\s*\{{)?\s*)\{{?\s*[\d,\s]+\}}"
    m = re.search(rf"kArch{name}\s*\{{([^}}]*)\}}", s)
    if not m:
        raise KeyError(f"找不到 kArch{name}")
    new = s[:m.start(1)] + "\n    " + body + "\n" + s[m.end(1):]
    open(TUNING, "w").write(new)


def read_line_set(name):
    s = open(TUNING).read()
    m = re.search(rf"kArch{name}\s*\{{([^}}]*)\}}", s)
    if not m:
        raise KeyError(f"找不到 kArch{name}")
    return [int(t) for t in re.findall(r"\d+", m.group(1))]


BASE_A = None
BASE_B = None


def scale_lines(k):
    """把两路 FDN 延迟线长度整体乘以 k（保持互素性：缩放后取最近的奇数）。"""
    global BASE_A, BASE_B
    if BASE_A is None:
        BASE_A, BASE_B = read_line_set("LinesA"), read_line_set("LinesB")

    def sc(vals):
        out = []
        for v in vals:
            n = int(round(v * k))
            if n % 2 == 0:
                n += 1              # 取奇数，降低模式重合概率
            out.append(max(31, n))
        return out

    write_line_set("LinesA", sc(BASE_A))
    write_line_set("LinesB", sc(BASE_B))


# ---------------------------------------------------------------- 目标函数
def objective(r, verbose=False):
    """各档位平滑谱误差的加权和（max 为主，p95 兜底）。"""
    c = NrevRenderer(sr=SR, block=512)
    total = 0.0
    for name, p in FIT_POINTS:
        yr = ref_ir(r, name, p)
        yc = cand_ir(c, p)
        gmax, g99, g95, gmean = smoothed_spectrum_err_db(yr, yc, sr=SR)
        total += gmax + 0.5 * g95
        if verbose:
            print(f"      {name:10s} max={gmax:6.2f} p95={g95:5.2f} mean={gmean:5.2f}")
    return total


def scan(r, label, setter, values, fmt="{:.6f}"):
    """一维扫描：对每个候选值写回、重编译、算目标，最后落在最优值。"""
    print(f"\n  扫描 {label}：")
    best = None
    for v in values:
        setter(v)
        rebuild()
        obj = objective(r)
        flag = ""
        if best is None or obj < best[0]:
            best = (obj, v)
            flag = "  <-- best"
        print(f"    {label} = {fmt.format(v):>12s}   目标 = {obj:8.3f}{flag}")
    setter(best[1])
    rebuild()
    print(f"  → {label} = {fmt.format(best[1])}（目标 {best[0]:.3f}）")
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=["trim", "damping", "q", "scale", "diffuser", "all"])
    args = ap.parse_args()

    r = V.Vst3RefRenderer(sr=SR, block=512)
    rebuild()
    print("起点：")
    obj0 = objective(r, verbose=True)
    print(f"  目标 = {obj0:.3f}")

    if args.stage in ("trim", "all"):
        # 电平配平：先用解析解（各档最佳标量增益的几何均值），再细扫
        c = NrevRenderer(sr=SR, block=512)
        gains = []
        cur = 0.30
        m = re.search(r"kWetTrim\s*=\s*([-\d.eE+]+)f",
                      open(os.path.join(ROOT, "src", "dsp", "ReverbEffect.h")).read())
        if m:
            cur = float(m.group(1))
        for name, p in FIT_POINTS:
            yr, yc = ref_ir(r, name, p), cand_ir(c, p)
            n = min(len(yr), len(yc))
            g = np.sqrt(np.sum(yr[:n] ** 2) / max(np.sum(yc[:n] ** 2), 1e-30))
            gains.append(g)
        target = cur * float(np.exp(np.mean(np.log(gains))))
        print(f"\n  电平配平解析解：kWetTrim {cur:.6f} → {target:.6f}")
        scan(r, "kWetTrim", write_wet_trim,
             [target * k for k in (0.85, 0.93, 1.0, 1.07, 1.15)])

    if args.stage in ("damping", "all"):
        scan(r, "kFitDampingHz",
             lambda v: write_const("kFitDampingHz", v, "{:.1f}"),
             [800, 1200, 1800, 2600, 3600, 5000, 7000], "{:.1f}")

    if args.stage in ("q", "all"):
        scan(r, "kFitFilterQ",
             lambda v: write_const("kFitFilterQ", v, "{:.4f}"),
             [0.45, 0.5, 0.55, 0.6, 0.65, 0.707, 0.8], "{:.4f}")

    if args.stage in ("scale", "all"):
        # 延迟线整体长度决定模式密度与低频延伸：
        # 实测候选低频过弱（20 Hz 差 −19 dB）、125–200 Hz 过强（+7 dB），
        # 是模式分布不对的典型症状 → 先扫整体缩放。
        scan(r, "线长缩放 k", scale_lines,
             [0.5, 0.7, 1.0, 1.4, 2.0, 2.8, 4.0], "{:.2f}")

    if args.stage in ("diffuser", "all"):
        scan(r, "kArchDiffuserGain",
             lambda v: write_const("kArchDiffuserGain", v, "{:.4f}"),
             [0.3, 0.4, 0.5, 0.6, 0.7], "{:.4f}")

    print("\n最终：")
    obj1 = objective(r, verbose=True)
    print(f"  目标 {obj0:.3f} → {obj1:.3f}")


if __name__ == "__main__":
    main()
