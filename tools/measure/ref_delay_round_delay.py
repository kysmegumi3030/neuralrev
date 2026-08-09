"""每圈的**累积延迟**：参考 vs 候选，逐圈直读。

## 要判定的事

候选在 fb=1.0 上的逐圈对齐滞后是 −4, 11, 27, 43, … 即 **+15.6 样点/圈**
（`align` 的正 lag = 候选偏晚）。环内每圈的额外延迟有两个来源：

  * `kMeasLoopPreDelaySamples = 16`（固定预延迟）
  * `kMeasLoopFirTaps` 的群延迟（峰在第 7 抽头附近）

两种拓扑给出不同的预测：

  A. 预延迟**在环内**（当前候选：LoopFir 串在反馈支路上）
     echo n 超出 n·D 的量 = (16 + gd)·n        ⇒ 增量 16+gd
  B. 预延迟只在**湿抽头**上过一次（不进反馈）
     echo n 超出 n·D 的量 = gd·n + 16          ⇒ 增量 gd

相对增量 = 16 ⇒ 实测 15.6 支持「参考是 B、候选是 A」。

但这条推断依赖 gd 的取值，而 gd 是从抽头形状估的。**更硬的判据是直接量
参考自己的逐圈增量**：若参考是 B，它的增量就等于 gd（约 7）；若参考也是 A，
增量应为 16+gd（约 23），那 15.6 的相对差就得由别的机制解释。

档案里有一条旧读数「参考逐圈 +16/+2/+15/+3」，那是**峰位**（整数 argmax）读的，
在 LFO 调制 + 双峰核下会在相邻样点间跳，所以呈现出 16/2/16/2 的锯齿。
本脚本改用**能量重心**（对 LFO 抖动与核形状都稳），并同时报峰位以便对照。

## 测法

单冲激（amp=1e-3，线性区，见 §14.4）、`at = 19200`（过起始渐变，§14.10）、
`fb=1.0`、`LP=1.0`、`HP=0.0`、Stereo。取 D = 4800（norm=0.0，**精确整数**，
没有分数延迟搅混起点）。逐圈取窗 [n·D − 64, n·D + 1200)，在窗内算重心，
减去 n·D 得到该圈的累积超出量。
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V          # noqa: E402
from plugin_match import nrev_cand as C         # noqa: E402

SR = 48000
AT = 19200          # 过起始渐变（§14.10）
AMP = 1e-3          # 线性区（§14.4：amp>0.03 进饱和）
# 16 圈而不是 9：斜率的标准误 ∝ 1/√Σ(k−k̄)² ，圈数从 9 加到 16
# 让杠杆增大约 2.4 倍。fb=1.0 的环路增益 0.8，第 16 圈仍在 0.8^16 = 2.8%，
# 重心口径够用（窗内 rms 会一并打印，可核对是否已沉到噪声）。
NROUND = 16
PRE = 64            # 窗左侧余量（容纳负向偏移）
WIN = 1200


def centroid(seg: np.ndarray) -> float:
    """能量重心（相对窗起点，样点）。用 |x|² 加权。"""
    w = np.abs(np.asarray(seg, float)) ** 2
    s = w.sum()
    if s <= 0.0:
        return float("nan")
    return float(np.dot(np.arange(len(w), dtype=float), w) / s)


def rounds(y: np.ndarray, d: int) -> list[tuple[float, int, float]]:
    """逐圈返回 (重心超出量, 峰位超出量, 窗内 rms)。"""
    out = []
    for n in range(1, NROUND + 1):
        a = AT + n * d - PRE
        seg = np.asarray(y, float)[a:a + WIN]
        if len(seg) < WIN:
            break
        c = centroid(seg) - PRE
        p = int(np.argmax(np.abs(seg))) - PRE
        out.append((c, p, float(np.sqrt(np.mean(seg ** 2)))))
    return out


def drift_regression(ca: np.ndarray, cb: np.ndarray) -> None:
    """漂移斜率：对**累积重心之差**做回归，而不是取相邻差的均值。

    为什么换估计量：相邻差的均值只用到首末两点
    （Σdiff/(N−1) ≡ (x_N − x_1)/(N−1)），中间 N−2 个点全部作废，
    于是它的不确定度就是单点噪声除以 N−1 —— 而单点噪声（LFO 残余 + 重心
    对窗内电平分布的敏感）的量级与要测的 0.4 样点/圈相当。回归用上所有点。

    对**差**序列回归而不是各自回归再相减：LFO 摆动与那 ~4.4 样点未解释偏置
    在两侧同相同量（已由 HP 扫描的差值列稳定性证实），在差里直接约掉。
    残余的散布才是真正的随机误差，可以用来判斜率是否显著。
    """
    n = min(len(ca), len(cb))
    k = np.arange(1, n + 1, dtype=float)
    dif = np.asarray(cb[:n], float) - np.asarray(ca[:n], float)

    # 一次多项式 + 残差 ⇒ 斜率的标准误（普通最小二乘）
    slope, intercept = np.polyfit(k, dif, 1)
    resid = dif - (slope * k + intercept)
    dof = max(n - 2, 1)
    s2 = float(np.dot(resid, resid)) / dof
    sxx = float(np.dot(k - k.mean(), k - k.mean()))
    se = float(np.sqrt(s2 / sxx)) if sxx > 0 else float("nan")

    naive = (dif[-1] - dif[0]) / (n - 1) if n > 1 else float("nan")

    print(f"\n{'=' * 78}")
    print("漂移斜率（对累积重心之差回归，圈数为自变量）")
    print(f"{'=' * 78}")
    print(f"  差序列：{np.round(dif, 3)}")
    tstat = abs(slope) / se if se > 0 else float("nan")
    print(f"  斜率 = {slope:+.4f} ± {se:.4f} 样点/圈（|t| = {tstat:.2f}）")
    print(f"  截距 = {intercept:+.4f} 样点（第 0 圈的常数偏移）")
    print(f"  残差 std = {resid.std(ddof=1) if n > 2 else float('nan'):.4f} 样点")
    print(f"  对照：相邻差均值口径给 {naive:+.4f}（只用到首末两点）")
    print(f"\n  判读：|t| < 2 ⇒ 斜率与 0 不可区分，**不要**据此加分数延迟补偿；")
    print(f"        |t| ≥ 2 ⇒ 漂移真实，环内补 {-slope:+.4f} 样点/圈。")


def main() -> None:
    d = 4800    # norm=0.0 ⇒ 100 ms 恰好 4800 样点（整数）

    n = AT + (NROUND + 2) * d
    x = np.zeros(n, dtype=np.float64)
    x[AT] = AMP

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    rp = {"delay_drywet": 1.0, "delay_time_l": 0.0, "delay_time_r": 0.0,
          "delay_feedback": 1.0, "delay_lowpass": 1.0, "delay_highpass": 0.0,
          "delay_mode": 1.0}
    # ⚠️ 必须标定 LFO 起相。不设它 = 随机取一个相位，于是逐圈重心里混入
    # ±3.4 样点的 LFO 摆动（§14.5 的深度律在 D=4800 上给 3.40）。那正是
    # 下面两列 std≈2.4…2.6 的来源 —— **均值仍然可用**（摆动零均值），
    # 但逐圈的抖动模式在两侧是各摆各的，不能逐圈比。
    cp = {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
          "d_timel": 0.0, "d_timer": 0.0, "d_feedback": 1.0,
          "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
          "d_lfophase": 0.238423}

    yr = ref.render(x, rp)[0]
    yc = cand.render(x, cp)[0]

    rr = rounds(yr, d)
    rc = rounds(yc, d)

    print(f"\n{'=' * 78}")
    print(f"逐圈累积延迟（相对 n·D，D={d}）—— 重心口径")
    print(f"{'=' * 78}")
    print("  圈    参考重心   候选重心   差(cand-ref)  参考峰位  候选峰位")
    for i, (a, b) in enumerate(zip(rr, rc), start=1):
        print(f"  {i:2d}  {a[0]:9.2f}  {b[0]:9.2f}  {b[0] - a[0]:11.2f}"
              f"  {a[1]:8d}  {b[1]:8d}")

    ca = np.array([t[0] for t in rr])
    cb = np.array([t[0] for t in rc])
    da, db = np.diff(ca), np.diff(cb)

    print(f"\n  参考逐圈增量：{np.round(da, 2)}")
    print(f"    均值 {da.mean():.3f}  标准差 {da.std():.3f}")
    print(f"  候选逐圈增量：{np.round(db, 2)}")
    print(f"    均值 {db.mean():.3f}  标准差 {db.std():.3f}")
    print(f"\n  相对增量（候选 − 参考）= {db.mean() - da.mean():.3f} 样点/圈")

    drift_regression(ca, cb)

    print(f"\n{'=' * 78}")
    print("判读")
    print(f"{'=' * 78}")
    print("  设 FIR 群延迟 gd、固定预延迟 16：")
    print("    拓扑 A（预延迟在环内）  ⇒ 逐圈增量 = 16 + gd")
    print("    拓扑 B（只在湿抽头一次）⇒ 逐圈增量 = gd，且第 1 圈多 16")
    print(f"  参考实测增量 {da.mean():.2f} ⇒ 若为 B 则 gd ≈ {da.mean():.2f}；"
          f"若为 A 则 gd ≈ {da.mean() - 16:.2f}")
    print(f"  候选实测增量 {db.mean():.2f}（候选当前是 A，gd ≈ {db.mean() - 16:.2f}）")
    print("  gd 可由抽头独立算出，见下 —— 三个数必须自洽，否则拓扑判错。")

    taps = np.array([
        9.62263668e-06, 1.60290989e-04, 1.58561456e-03, 9.58770734e-03,
        4.09797339e-02, 1.19218596e-01, 2.29726013e-01, 2.90206586e-01,
        2.32081954e-01, 9.87947094e-02, -3.96733547e-03, -2.79706871e-02,
        -6.66839289e-03, 1.04657766e-02, 8.66904199e-03, 3.55806552e-04,
        -2.98078448e-03, -1.36125211e-03, 5.59422371e-04, 7.39425299e-04,
        9.38805754e-05, -2.37934054e-04, -1.31222246e-04, 3.49083115e-05,
        6.42143005e-05, 1.35561315e-05, -1.85154631e-05, -1.07360456e-05,
    ])
    # DC 群延迟 = Σ n·h[n] / Σ h[n]（对低频而言就是重心）
    gd_dc = float(np.dot(np.arange(len(taps), dtype=float), taps) / taps.sum())
    gd_e = centroid(taps)
    print(f"\n  抽头独立算出的群延迟：DC 口径 {gd_dc:.3f}，能量重心口径 {gd_e:.3f}")

    hp_sweep(ref, cand, x, rp, cp, d, gd_dc, gd_e)


def hp_sweep(ref, cand, x, rp, cp, d, gd_dc, gd_e) -> None:
    """判定那 ~3.4–4.4 样点的未解释偏置是**口径效应**还是真实环路长度差。

    两侧都读到 ~10–11.4 样点/圈，而抽头群延迟只有 6.73（DC）/ 6.96（重心）。
    候选解释：环内用户 HP 是 20 Hz 二阶，48 kHz 上时间常数约 380 样点，
    这么长的尾巴对**重心**有显著后拖偏置，而对 DC 群延迟几乎无贡献。

      * 若增量随 HP 抬高而下降到接近 gd ⇒ 是 HP 尾巴的重心偏置（口径效应）
      * 若增量不动                      ⇒ 是真实环路长度差，必须继续追

    HP 归一化 0.0/0.2/0.4 对应 20 / 20+780·0.2^(5/3) / … Hz（§14.1 的 5/3 律）。
    """
    print(f"\n{'=' * 78}")
    print("HP 扫描：那 ~4 样点是重心口径的偏置，还是真实环路长度？")
    print(f"{'=' * 78}")
    print(f"  基准：gd = {gd_dc:.3f}(DC) / {gd_e:.3f}(重心)")
    print("   HP归一   HP(Hz)    参考增量   候选增量   差    参考−gd(重心)")

    for hp in (0.0, 0.1, 0.2, 0.4):
        hz = 20.0 + 780.0 * hp ** (5.0 / 3.0)
        rp2 = dict(rp, delay_highpass=hp)
        cp2 = dict(cp, d_highpass=hp)
        try:
            da = np.diff([t[0] for t in rounds(ref.render(x, rp2)[0], d)])
            db = np.diff([t[0] for t in rounds(cand.render(x, cp2)[0], d)])
        except Exception as exc:                     # 渲染失败不该中断整表
            print(f"  {hp:6.2f}  {hz:7.1f}   ← 渲染失败：{exc}")
            continue
        if len(da) == 0 or len(db) == 0:
            print(f"  {hp:6.2f}  {hz:7.1f}   ← 窗内无能量，跳过")
            continue
        print(f"  {hp:6.2f}  {hz:7.1f}  {da.mean():9.3f}  {db.mean():9.3f}"
              f"  {db.mean() - da.mean():6.3f}  {da.mean() - gd_e:12.3f}")

    print("\n  判读：末列若随 HP 抬高而趋近 0 ⇒ 口径效应（换 DC 口径即消失）；"
          "\n        若在各档基本不变 ⇒ 真实环路长度差，不能记作已解释。")


if __name__ == "__main__":
    main()
