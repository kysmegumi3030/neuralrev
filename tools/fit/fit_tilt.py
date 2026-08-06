"""拟合湿声总线上的**低频倾斜补偿**（低架）：kFitTiltShelfDb / kFitTiltShelfHz。

修的是什么（来自 diag_lowmodes.py 的误差分解）：本网络的低频比参考整体偏热，
逐带「候选−参考」均值 20–40 Hz +5.81 / 40–80 +2.51 / 80–300 +2.91 /
300–2k −1.21 dB。低频三带同号为正、300 Hz 以上转负 ⇒ 是一条**整体倾斜**，
一个低架就能吃掉其中的**均值项**。

它修不了什么：20–40 Hz 的去均值 max 约 10.6 dB，那部分是模式错位，
只能靠动线长/加路数（见 fit_lines_direct.py 与 PROGRESS 待办 2）。
所以本脚本的成功判据不是「≤3 dB」，而是「把**均值项**压到接近 0，
使各带 max 落到各自去均值 max 附近」。

实际落点（全 6 档复核）：fc=235 Hz / −4.25 dB，全档全带 max 17.37 → 13.53 dB。
注意 **40–80 Hz 是被换出去的**（12.04 → 13.53）：一个 2 阶低架的平坦区同时
覆盖 20–40 与 40–80，两带需要的补偿量却方向相反，min–max 解停在两带交叉处，
收益已榨干。完整推导见 ReverbTuning.h 的 kFitTiltShelf* 注释。

为什么两个自由量一起扫、而不是只扫 dB：低架的拐点决定它「够不够只碰低频」。
fc 太高会连 300–2k 一起压（那带均值本来是负的，压了会更负）；太低则 20–40 Hz
吃不到。所以 fc 与 dB 必须联立 —— 一维扫 dB 的解会被 fc 的初值绑死。

口径与所有 fit 脚本一致：每个试探点都**真写常数、真重编译、真渲染、真测**，
不用任何解析代理（代理在 fit_modes.py 上已经翻过车，见该文件开头）。
参考 IR 全程缓存（它与候选侧常数无关），所以每次试探只重算候选。

目标函数 = **各带「max 超出可达下界的部分」的最大值**（min–max），
和式只作同分时的次序。下界来自 tools/measure/ref_band_floor.py 的参考自比实测。
这是本脚本与 fit_lines_direct.py 的唯一口径差别：那边用和式，而验收口径看的是
逐带最差，和式会拿一带的退化去换另一带的改善。选型的完整经过记在
`kFitTiltShelfDb` 扫描段之前的注释里（v1/v2/v3 三版守卫的兴废）。

**为什么只有一级低架**：曾加过第二级「次低档」（拐点在 20–40 与 40–80 之间），
想把 20–40 剩下的均值也吃掉。25 个网格点无一合格，全部退化 40–80 Hz ——
两带中心只差一个八度，2 阶低架的过渡带比这更宽，没有可行窗口。
该 stage 与对应常数已删除，完整推导留在 ReverbTuning.h 的
kFitTiltShelf* 之后（「试过并否证的方案」那段）。

用法：
    python3 tools/fit/fit_tilt.py               # 只报告，跑完回滚
    python3 tools/fit/fit_tilt.py --apply       # 写入 ReverbTuning.h
    python3 tools/fit/fit_tilt.py --coarse      # 只跑粗网格（快速看趋势）
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
NFFT = 65536
F = np.fft.rfftfreq(NFFT, 1.0 / SR)
TUNING = os.path.join(ROOT, "src", "dsp", "ReverbTuning.h")
BUILD = os.path.join(ROOT, "tools", "nrev_render", "build.sh")

BANDS = [(20, 40), (40, 80), (80, 300), (300, 2000), (2000, 20000)]
FLOOR = np.array([0.35, 1.15, 1.38, 1.72, 1.05])

# 拟合档位：默认档 + 一个 LOW CUT 中位档。
# 为什么要带 lowcut-mid：低架与 LOW CUT 串在同一条总线上，两者的作用区重叠。
# 只用 lowcut=0 档拟合出的低架，在用户把 LOW CUT 推上去时可能过度补偿。
#
# decay-hi 是**后加的，被一次真实退化逼出来的**：最初只用前两档拟合，落点在
# band_report.py 的全 6 档复核里让 40–80 Hz 从 11.22 涨到 11.69 dB。
# 退化守卫本身没问题，是它的**覆盖面**不够 —— 40–80 的最差档正是 decay-hi
# （11.69），而它不在拟合集里，守卫根本没看到。
# 教训：守卫必须覆盖「该带最差的那一档」，否则它只是在没有风险的地方守。
FIT_POINTS = [
    ("default",    dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)),
    ("lowcut-mid", dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.5, highcut=1.0)),
    ("decay-hi",   dict(drywet=1.0, predelay=0.5, decay=0.8, lowcut=0.0, highcut=1.0)),
    # predelay-hi 是**同一个洞第二次**咬出来的：加了 decay-hi 后我以为 40–80 Hz
    # 的最差档已经覆盖，但全 6 档复核给出 predelay-hi 13.76 dB > decay-hi 13.14
    # —— 最差档根本不是 decay-hi。
    # 教训升级版：不要**猜**哪一档最差。先用 band_report.py 跑全档、看每个带的
    # max 落在哪一档，再把那些档放进拟合集。
    ("predelay-hi", dict(drywet=1.0, predelay=0.9, decay=0.5, lowcut=0.0, highcut=1.0)),
]

_ref: dict[str, np.ndarray] = {}


def write_const(name, value, fmt="{:.4f}"):
    s = open(TUNING).read()
    pat = re.compile(rf"({name}\s*=\s*)([-\d.eE+]+)")
    if not pat.search(s):
        raise KeyError(f"ReverbTuning.h 里找不到 {name}")
    open(TUNING, "w").write(pat.sub(lambda m: m.group(1) + fmt.format(value),
                                    s, count=1))


def read_const(name):
    s = open(TUNING).read()
    m = re.search(rf"{name}\s*=\s*([-\d.eE+]+)", s)
    if not m:
        raise KeyError(f"ReverbTuning.h 里找不到 {name}")
    return float(m.group(1))


def rebuild():
    r = subprocess.run(["/bin/zsh", BUILD], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("重编译失败:\n" + r.stderr.decode()[:800])


def smooth(y, of=1 / 12):
    a = np.zeros(NFFT)
    a[:min(len(y), NFFT)] = np.asarray(y, float)[:NFFT]
    S = np.abs(np.fft.rfft(a))
    cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
    lo = np.searchsorted(F, F * 2 ** -of, "left")
    hi = np.maximum(np.searchsorted(F, F * 2 ** of, "right"), lo + 1)
    return np.sqrt((cs[hi] - cs[lo]) / np.maximum(hi - lo, 1))


def ref_curve(r, name, p):
    if name not in _ref:
        n = BASE_AT + int(4.0 * SR)
        x = np.zeros(n, dtype=np.float32)
        x[BASE_AT] = 1.0
        y = r.render(x, params={f"reverb_{k}": v for k, v in p.items()})
        _ref[name] = smooth(y.astype(np.float64)[0][BASE_AT + REF_LATENCY:])
    return _ref[name]


def cand_curve(p):
    c = NrevRenderer(sr=SR, block=512)
    n = BASE_AT + int(4.0 * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    return smooth(c.render(x, params=p).astype(np.float64)[0][BASE_AT:])


def measure(r):
    """返回 (目标值, 逐带超额, 逐带均值, 逐带去均值max)。

    后两项是诊断量：均值项是低架**能**修的，去均值 max 是它**修不了**的
    模式错位。把两者分开打印，才看得出「低架已经吃干了均值」还是「还有余量」。
    """
    tot = 0.0
    nb = len(BANDS)
    # per 取各档的**最大**（不是平均）：验收口径是逐带最差 max，所以目标里的
    # 每一带也必须是它在所有档上的最差值。用平均会把「一档退化 1.5 dB、
    # 另两档各改善 0.7」算成净改善 —— 正是这种稀释让第一版的 40–80 退化溜过去。
    per = np.zeros(nb)
    mean = np.zeros(nb)
    dmax = np.zeros(nb)
    for name, p in FIT_POINTS:
        A, B = ref_curve(r, name, p), cand_curve(p)
        d = 20 * np.log10(np.maximum(B, 1e-30) / np.maximum(A, 1e-30))
        err = np.abs(d)
        for i, (lo, hi) in enumerate(BANDS):
            m = (F >= lo) & (F <= hi)
            ex = max(0.0, float(err[m].max()) - FLOOR[i])
            tot += ex
            per[i] = max(per[i], ex)
            dm = d[m]
            mean[i] += float(dm.mean()) / len(FIT_POINTS)
            dmax[i] += float(np.abs(dm - dm.mean()).max()) / len(FIT_POINTS)
    # 主目标是 **min–max**（全带最差超额），和式只作同分时的次序。
    # 为什么改口径（原来抄的是 fit_lines_direct.py 的和式）：验收是逐 bin ≤3 dB，
    # 所以真正卡住的永远是**最差的那一带**，和式会为了几个宽带的小改善
    # 去牺牲最差带。而这里两带的需求是**互斥**的（低架压 20–40 必然压 40–80），
    # 和式会一路压到 40–80 爆掉；min–max 会自动停在两带**交叉**的位置。
    return float(per.max()), tot, per, mean, dmax


def trial(r, hz, db, prefix="kFitTiltShelf"):
    write_const(prefix + "Hz", hz)
    write_const(prefix + "Db", db)
    rebuild()
    return measure(r)


# 关于「逐带不退化守卫」——**试过两版，最后删掉了**，过程记在这里。
#
# v1：一律不许退化（容差 0.10 dB）。理由是验收口径逐带独立，一带退化就是
#     实打实的损失。但它有两个洞，都被实测抓出来了：
#       a) 拟合集只有 default + lowcut-mid，而 40–80 Hz 的**最差档是
#          decay-hi**，守卫根本没看到它 —— 落点在全 6 档复核里让 40–80
#          从 11.22 涨到 11.69。守卫必须覆盖「该带最差的那一档」。
#       b) 基线取的是常数文件里的当前值，重跑时基线已含上次落点，
#          于是上次的退化被当成「本来就这样」而放过。
#     两个洞都补了（decay-hi 入集、基线强制 dB=0、per 取各档 max）。
#
# v2：补洞后再跑，**全部 30 个网格点都退化 40–80** —— 该带在高 decay 档
#     均值本来就是负的（−1.13），低架的任何衰减都让它更负。硬守卫下无解，
#     脚本只能退回 dB=0，等于放弃另外三带各约 3 dB 的真实改善。
#     于是改成「允许退化但设上限 3.2 dB」。
#
# v3（现状）：连上限也删了。因为它会挡掉**真正的 min–max 最优解** ——
#     fc=2000/−4.5 dB 的最差带是 12.04，比上限选中的 13.48 更好，
#     却因 40–80 涨了 4.5 dB 被拒。
#     而 min–max 本身就是自限的：它永远不会接受「某带涨到超过当前最差值」
#     的解，因为那会直接抬高目标本身。所以额外的守卫是冗余的，
#     在这个互斥两带的问题上还有害。
#
# 留下的教训（比落点本身重要）：**目标函数要和验收口径同形**。
# 验收看逐带最差 ⇒ 目标就该是 min–max。一开始抄 fit_lines_direct.py 的
# 和式，才需要一层层打补丁去堵它「用一带换另一带」的行为。


def report(tag, worst, tot, per, mean=None, dmax=None):
    print(f"{tag}  最差带超额 {worst:.3f}（和式 {tot:.3f}）")
    print("    逐带超额：" + "  ".join(
        f"{lo}-{hi}:{v:.2f}" for (lo, hi), v in zip(BANDS, per)))
    if mean is not None:
        print("    逐带均值：" + "  ".join(
            f"{lo}-{hi}:{v:+.2f}" for (lo, hi), v in zip(BANDS, mean)))
        print("    去均值max：" + "  ".join(
            f"{lo}-{hi}:{v:.2f}" for (lo, hi), v in zip(BANDS, dmax)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--coarse", action="store_true", help="只跑粗网格")
    a = ap.parse_args()

    PREFIX = "kFitTiltShelf"

    r = V.Vst3RefRenderer(sr=SR, block=512)
    orig_hz = read_const(PREFIX + "Hz")
    orig_db = read_const(PREFIX + "Db")

    # 基线必须是**低架关闭**（dB=0）的状态，而不是常数文件里的当前值。
    # 否则重跑时基线里已经含着上一次的落点，退化守卫会把上次的退化当成
    # 「本来就这样」而放过 —— 这正是第一次 40–80 退化能留下来的另一半原因。
    w0, t0, p0, m0, d0 = trial(r, orig_hz, 0.0, PREFIX)
    print(f"起点（低架关闭：Hz={orig_hz:.1f} dB=+0.00）")
    report("  ", w0, t0, p0, m0, d0)
    print()

    # dB 只取负值：实测低频**偏热**，补偿必然是衰减。
    # fc 网格上探到 2 kHz：落点实际就在 2120 Hz —— 这条「低架」的拐点比直觉高，
    # 因为它要同时覆盖 80–300 与 300–2k 两带的正均值。
    grid_hz = [200.0, 320.0, 500.0, 800.0, 1200.0, 2000.0]
    grid_db = [-1.0, -2.0, -3.0, -4.5, -6.0]
    HZ_LO, HZ_HI = 100.0, 4000.0

    def rising(per):
        """哪些带比基线上升（只用于打印，不再作为准入条件，见上方 v3 说明）。"""
        return [f"{lo}-{hi}" for (lo, hi), v, v0 in zip(BANDS, per, p0)
                if v > v0 + 0.10]

    # 排序键：主 = 最差带超额，次 = 和式（同分时偏向整体更好的解）
    def key(w, t):
        return (round(w, 4), round(t, 3))

    best = (w0, t0, orig_hz, orig_db)
    seen = {}
    print("--- 粗网格 ---")
    for hz in grid_hz:
        for db in grid_db:
            w, t, per, mean, dmax = trial(r, hz, db, PREFIX)
            seen[(hz, db)] = w
            up = rising(per)
            flag = ""
            if key(w, t) < key(best[0], best[1]):
                best = (w, t, hz, db)
                flag = "  ← 最优"
            if up:
                flag += "  ↑" + ",".join(up)
            print(f"  Hz={hz:6.1f} dB={db:+5.2f}  最差 {w:6.2f}"
                  f"   20-40:{per[0]:6.2f}  40-80:{per[1]:5.2f}"
                  f"  80-300:{per[2]:5.2f}  300-2k:{per[3]:5.2f}{flag}")

    print(f"\n粗网格最优：Hz={best[2]:.1f} dB={best[3]:+.2f}  最差 {best[0]:.3f}")

    if not a.coarse:
        # 细化：在最优点邻域做一次坐标下降（先 dB 后 Hz，各一轮）
        print("\n--- 细化 ---")
        cur_w, cur_t, cur_hz, cur_db = best
        # dB 步长细到 0.25：min–max 的最优点在两带**交叉处**，
        # 交叉附近目标是 V 形（一侧升一侧降），粗步长会直接跨过去。
        for step_db in (1.0, 0.5, 0.25):
            improved = True
            while improved:
                improved = False
                for s in (+1, -1):
                    db = round(cur_db + s * step_db, 4)
                    if db > 0.0 or db < -14.0 or (cur_hz, db) in seen:
                        continue
                    w, t, _, _, _ = trial(r, cur_hz, db, PREFIX)
                    seen[(cur_hz, db)] = w
                    if key(w, t) < key(cur_w, cur_t):
                        cur_w, cur_t, cur_db = w, t, db
                        print(f"  dB → {db:+.2f}   最差 {w:.3f}")
                        improved = True
                        break
        for step_hz in (60.0, 25.0):
            improved = True
            while improved:
                improved = False
                for s in (+1, -1):
                    hz = round(cur_hz + s * step_hz, 4)
                    if hz < HZ_LO or hz > HZ_HI or (hz, cur_db) in seen:
                        continue
                    w, t, _, _, _ = trial(r, hz, cur_db, PREFIX)
                    seen[(hz, cur_db)] = w
                    if key(w, t) < key(cur_w, cur_t):
                        cur_w, cur_t, cur_hz = w, t, hz
                        print(f"  Hz → {hz:.1f}   最差 {w:.3f}")
                        improved = True
                        break
        best = (cur_w, cur_t, cur_hz, cur_db)

    w1, t1, p1, m1, d1 = trial(r, best[2], best[3], PREFIX)
    print(f"\n落点：Hz={best[2]:.1f} dB={best[3]:+.2f}")
    report("  ", w1, t1, p1, m1, d1)
    print(f"\n最差带超额 {w0:.3f} → {w1:.3f}（{len(seen) + 1} 次试探）")
    print("    逐带变化：" + "  ".join(
        f"{lo}-{hi}:{v0:.2f}→{v1:.2f}({v1 - v0:+.2f})"
        for (lo, hi), v0, v1 in zip(BANDS, p0, p1)))
    up = [f"{lo}-{hi}(+{v1 - v0:.2f})" for (lo, hi), v0, v1
          in zip(BANDS, p0, p1) if v1 > v0 + 0.10]
    print(f"    注：以下带为换取最差值下降而**上升**：{', '.join(up)}"
          if up else "    ✓ 无带上升")
    print("注：20–40 Hz 的「去均值max」是低架碰不到的模式错位项，"
          "它决定该带的数学下限。")

    if not a.apply:
        write_const(PREFIX + "Hz", orig_hz)
        write_const(PREFIX + "Db", orig_db)
        rebuild()
        print("\n（未加 --apply，已回滚）")
    else:
        print("\n已写入 ReverbTuning.h")


if __name__ == "__main__":
    main()
