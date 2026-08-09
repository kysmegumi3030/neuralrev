"""搁架之后剩下的 12–14 kHz 残余：**单次通过**还是**逐圈累积**？

## 为什么这是下一个该问的问题

§14.14.4j 落地的湿抽头搁架把长档的**谱型**修好了（16 kHz 需求
+13.02 → −0.46 dB），但验收的逐 bin 只从 40.91 降到 35.28 dB，
最差 bin 落在 **13013 Hz**、该带（12–14 kHz）**中位就 3.74 dB**。
搁架治的是谱型，所以剩下的是另一件事。

两种身份，修法完全相反，不能猜：

  * **单次通过**（湿路径的谱型仍有残余）⇒ 各圈的误差是**同一个常数**，
    在湿抽头上再修一级即可；
  * **逐圈累积**（环内 FIR 的 >12 kHz 滚降）⇒ 误差随圈数**线性增长**，
    必须动 `kMeasLoopFirTaps` 所在的反馈环，而那张表是**实测**的
    （§14.14.6 两次抽头重拟合都因此被否决）。

已有的一条线索指向后者：§14.14.4h 在定搁架位置时顺带量到，对照档
（588 ms，谱型本来就对）的 15–17 kHz 逐圈是 +0.41 → −3.35 → −9.77 dB，
**与档位无关**。若同一个斜率也出现在 12–14 kHz 上、且长档与对照档一致，
那 35.28 dB 的成因就与延迟长度无关，是环损。

## 测法：不含拟合的两侧直接读数

每一圈各开一个窗（回声互不重叠：突发 4800 样点，最短档间距 28212），
算功率谱，报**各自相对自己 1 kHz** 的带电平，再取候选 − 参考。
没有回归，因此没有 §14.14.4h 那个「在无能量频带索要 +28 dB」的失效模式。

量的是**每侧自己的圈间比**（= 该侧该带的环路增益），再各自扣掉该侧
1 kHz 的圈间比，得「该带相对 1 kHz 的每圈多余损耗」；候选 − 参考即失配。
这样连跨侧归一都不需要，读数只依赖单侧内部的比。

## 三条自查（每一条都是踩过的坑）

1. **不能用「与激励前静音段比」当底噪门**。激励前那段严格是 0，比值恒过，
   于是第一版给 12–14k「每圈 −24 dB」和 14–16k「每圈 **+10** dB」都盖了 ✓ ——
   衰减环里 HF 逐圈变强，物理上不可能。
2. **矩形窗会用泄漏冒充信号**。强 LF 每圈只掉 3 dB，真 HF 每圈掉 28 dB，
   几圈后 HF 桶里装的是 LF 的旁瓣。故主口径用 4 项 Blackman–Harris
   （旁瓣 −92 dB），并用 Hann（−31 dB）复算同一个量交叉核对；两窗差
   >1 dB 的格作废。注意这与验收谱**必须不加窗**不冲突：验收量逐 bin 幅度，
   加窗会抹平梳状零点；这里量带能量的圈间比，不加窗才是错的。
3. **限带对照给出自造物地板**。同一激励、清零 >12 kHz 再跑一遍：那之上
   出现的一切都是插件自己造的（环内舍入、调制边带、互调），不是回声。
   带内功率不足该地板 10 dB 的格作废。做这个对照时**不要**补回 std ——
   切掉高频后 std 掉到 0.79，补回去等于把通带整体抬 2 dB，会让每一条带
   （连 4–8k）都读出同一个假失配（本工具第三版就是这么全表作废的）。

另加两条沿用的纪律：**对照档** norm=0.65（LFO 零点，净调制 0.005 样点）
必须在 echo1 上给出 ≈0；**线性区** std 取 1e−3，远低于饱和起弯的 0.03
（§14.4），否则探针电平本身就会造出假的 HF 误差。

用法：
    python3 tools/measure/ref_delay_hf_rounds.py            # 0.65 与 1.00 两档
    python3 tools/measure/ref_delay_hf_rounds.py --fb 1.0   # 逐圈信号更强
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V          # noqa: E402
from plugin_match import nrev_cand as C         # noqa: E402

SR = 48000
AT = 19200          # 过参考的起始渐变（§14.3/§14.10）
DUR = 4800          # 突发长度；必须短于最短档的回声间距
STD = 1e-3          # 线性区（饱和在 0.03 起弯，§14.4）
# 限带对照的截止：取 12 kHz，正好让 12–14k / 14–16k 两条**整条**落在无输入
# 能量的一侧（可判自造物），而 4–8k / 8–12k 完全不受影响（仍是有效读数）。
CUTOFF = 12000.0
SEED = 12345
WIN = 6000          # 每圈的分析窗（突发 + 环内滤波器拖尾余量）
LFO_PHASE = 0.238423

T_MIN_MS, T_MAX_MS, T_EXP = 100.0, 1100.0, 5.0 / 3.0

# 参考带（相对 1 kHz）。12–14k 是当前最差带；两侧各留一带看形状走向。
BANDS = ((1000.0, 1000.0), (4000.0, 8000.0), (8000.0, 12000.0),
         (12000.0, 14000.0), (14000.0, 16000.0))
REF_BAND = (900.0, 1100.0)


def time_ms(norm: float) -> float:
    return T_MIN_MS + (T_MAX_MS - T_MIN_MS) * norm ** T_EXP


def burst(n: int, std: float = STD, cutoff: float | None = None) -> np.ndarray:
    """突发激励。cutoff 非空时把突发限带到该频率以下。

    限带版是**底噪对照**：>cutoff 完全没有输入能量，所以那之上出现的任何
    东西都是插件自己造的（环内 float 舍入、调制边带、互调），不是回声。
    有了它，才能判断某一圈的 HF 读数是信号还是自造物 —— 光看窗形或看空隙
    都不行，那些量里装的是同一份自造物。
    """
    rng = np.random.default_rng(SEED)
    x = np.zeros(n)
    b = rng.standard_normal(DUR) * std
    if cutoff is not None:
        S = np.fft.rfft(b)
        S[np.fft.rfftfreq(DUR, 1.0 / SR) > cutoff] = 0.0
        b = np.fft.irfft(S, n=DUR)
        # **不要**在这里补回 std。限带对照要的是「通带内谱密度与主跑完全相同」，
        # 不是「总电平相同」：切掉 >12 kHz 会让 std 掉到 0.79，补回去等于把
        # 通带整体抬 2 dB，于是每一条带（连 4–8k）都读出同一个假失配。
    x[AT:AT + DUR] = b
    return x


def ref_params(norm: float, fb: float) -> dict:
    return {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
            "delay_feedback": fb, "delay_lowpass": 1.0, "delay_highpass": 0.0,
            "delay_mode": 1.0}


def cand_params(norm: float, fb: float) -> dict:
    return {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
            "d_timel": norm, "d_timer": norm, "d_feedback": fb,
            "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
            "d_lfophase": LFO_PHASE}


def band_power(seg: np.ndarray, lo: float, hi: float, w: np.ndarray) -> float:
    P = np.abs(np.fft.rfft(seg * w)) ** 2
    f = np.fft.rfftfreq(len(seg), 1.0 / SR)
    m = (f >= lo) & (f <= hi)
    return float(P[m].sum())


# 为什么这里**必须**加窗，而验收谱**必须不**加窗：两者量的不是同一件事。
# 验收谱量的是逐 bin 幅度，加窗会抹平梳状零点、等于换口径（见
# ref_delay_worstbins.py 的那条纪律）。本工具量的是**带能量的圈间比**，
# 而矩形窗的边沿泄漏会把强 LF（每圈只掉 3 dB）漏进 HF 桶里；真 HF 每圈掉
# 28 dB，几圈之后读数就跟着泄漏走，表现为衰减环里 HF「每圈涨 10 dB」。
#
# 两窗交叉核对代替底噪门：Blackman–Harris 旁瓣约 −92 dB，Hann 约 −31 dB。
# 若某带两窗读数一致，说明该读数不受旁瓣支配；若差很多，那一格作废。
# 这比「与空隙比 100 倍」可靠 —— 空隙里装的是同一份泄漏，拿它当门槛
# 等于用污染物给污染物做体检（上一版就是这么放过了 +10 dB/圈那几格）。
def windows(n: int):
    k = np.arange(n) / (n - 1)
    # 4 项 Blackman–Harris（numpy 没有现成的），旁瓣 ≈ −92 dB
    bh = (0.35875 - 0.48829 * np.cos(2 * np.pi * k)
          + 0.14128 * np.cos(4 * np.pi * k)
          - 0.01168 * np.cos(6 * np.pi * k))
    return bh, np.hanning(n)


def read_echo(y: np.ndarray, start: int):
    """一圈的读数：各带**绝对**功率（不做任何归一）。

    只返回绝对量。跨侧归一（相对 1 kHz）留给调用方 —— 因为本工具真正要的
    量是**每侧自己的圈间比**，那是该侧在该带的环路增益，不需要跨侧口径。
    """
    seg = y[start:start + WIN]
    w1, w2 = windows(len(seg))
    out, alt = {}, {}
    for b in list(BANDS) + [REF_BAND]:
        if b[0] == b[1]:
            continue
        out[b] = band_power(seg, b[0], b[1], w1)
        alt[b] = band_power(seg, b[0], b[1], w2)
    return out, alt


def db(num: float, den: float) -> float:
    return 10.0 * np.log10(max(num, 1e-300) / max(den, 1e-300))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fb", type=float, default=0.5,
                    help="归一反馈（0.5 = 验收档；1.0 让逐圈信号更强）")
    ap.add_argument("--rounds", type=int, default=4)
    ap.add_argument("--norms", type=float, nargs="*",
                    default=[0.65, 0.90, 1.00])
    args = ap.parse_args()

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    print("逐圈 12–14 kHz 残余：单次通过 vs 逐圈累积（fb 归一 = %.2f，突发 std=%.0e）"
          % (args.fb, STD))
    print("口径：每侧自己的**圈间比**（该侧该带的环路增益），再扣掉该侧 1 kHz 的")
    print("      圈间比 ⇒「该带相对 1 kHz 的每圈多余损耗」。候选 − 参考即失配。")
    print("有效性：① 主口径 Blackman–Harris，用 Hann 复算交叉核对（防泄漏冒充信号）；")
    print("        ② 清零 >%.0f kHz 的限带对照给出自造物地板，不足 10 dB 的格作废。\n"
          % (CUTOFF / 1000.0))

    for nt in args.norms:
        ms = time_ms(nt)
        d = int(round(ms * 1e-3 * SR))
        n = AT + (args.rounds + 1) * d + WIN + 8192
        x = burst(n)
        a = np.asarray(ref.render(x, ref_params(nt, args.fb))[0], float)
        c = np.asarray(cand.render(x, cand_params(nt, args.fb))[0], float)

        # 限带对照：通带谱密度与主跑逐 bin 相同，只把 >CUTOFF 清零
        xb = burst(n, cutoff=CUTOFF)
        ab = np.asarray(ref.render(xb, ref_params(nt, args.fb))[0], float)
        cb = np.asarray(cand.render(xb, cand_params(nt, args.fb))[0], float)

        print("=== norm=%.4f  (%.1f ms, D=%d) ===" % (nt, ms, d))
        names = [b for b in BANDS if b[0] != b[1]]
        hdr = "  ".join("%20s" % ("%g-%gk" % (lo / 1000, hi / 1000))
                        for lo, hi in names)
        print("  圈→圈            " + hdr)
        print("  (每圈多余损耗 dB：ref / cand / 失配；两窗不一致的格作废)")

        prev = None
        for m in range(1, args.rounds + 1):
            s = AT + m * d - 200
            if s + WIN > min(len(a), len(c)):
                break
            ra, xa = read_echo(a, s)
            rc, xc = read_echo(c, s)
            # 自造物地板：同圈、同窗，限带激励下的同一读数
            fa, _ = read_echo(ab, s)
            fc, _ = read_echo(cb, s)

            if m == 1:
                one = [db(rc[b], rc[REF_BAND]) - db(ra[b], ra[REF_BAND])
                       for b in names]
                print("  echo1 单次通过：  "
                      + "  ".join("%+20.2f" % v for v in one))

            if prev is not None:
                pa, pc_, pxa, pxc, pfa, pfc = prev
                cells = []
                for b in names:
                    la = db(ra[b], pa[b]) - db(ra[REF_BAND], pa[REF_BAND])
                    lc = db(rc[b], pc_[b]) - db(rc[REF_BAND], pc_[REF_BAND])
                    # 交叉核对：同一个量用 Hann 再算一次
                    la2 = db(xa[b], pxa[b]) - db(xa[REF_BAND], pxa[REF_BAND])
                    lc2 = db(xc[b], pxc[b]) - db(xc[REF_BAND], pxc[REF_BAND])
                    # 自造物门：只对**整条落在 CUTOFF 之上**的带有意义。
                    # 通带内的带在对照里也是满信号，比出来必然 ≈0 dB，
                    # 拿去当门槛会把有效读数全判死。
                    if b[0] >= CUTOFF:
                        snr = min(db(ra[b], fa[b]), db(rc[b], fc[b]),
                                  db(pa[b], pfa[b]), db(pc_[b], pfc[b]))
                        if snr < 10.0:
                            cells.append("%20s" % ("—作废(自造物 %.0fdB)" % snr))
                            continue
                    if abs(la - la2) > 1.0 or abs(lc - lc2) > 1.0:
                        cells.append("%20s" % ("—作废(泄漏 %.0f/%.0f)"
                                               % (la - la2, lc - lc2)))
                        continue
                    cells.append("%8.2f/%6.2f/%+5.2f" % (la, lc, lc - la))
                print("  %d→%d              " % (m - 1, m) + "  ".join(cells))

            prev = (ra, rc, xa, xc, fa, fc)
        print()

    print("判读：失配列若 ≈0 ⇒ 单次通过（湿抽头再修一级）；")
    print("      若稳定为负且各档一致 ⇒ 环内 FIR 的 >12 kHz 滚降，属实测抽头表，")
    print("      改它必须先过 §14.14.6 那两条否决理由。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
