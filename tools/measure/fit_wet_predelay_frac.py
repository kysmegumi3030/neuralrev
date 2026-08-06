"""湿抽头预延迟的**分数部分**：把候选 echo1 逐步延迟，找残差最小的 τ。

## 为什么要这一步（而不是直接填 4.5）

`ref_delay_echo1_deconv.py` 已经判定缺的是**纯延迟**（幅度全带 ±0.1 dB 平、
群延迟随频率基本不变、反卷积核是 +4/+5 两个 0.48/0.50 的相邻抽头 = 半样点
线性插值的指纹）。但它给出的量在两个档上是 4.50 与 4.57，两个口径（群延迟、
核重心）也各差几个百分点。**填进 DSP 的常数必须是一个拟合值，不是读数的平均。**

## 测法

在 Python 里给候选 echo1 施加分数延迟 τ（与 DSP 同族的 Lagrange 插值，
不引入额外形状），扫 τ 使 nrmse(ref, delayed_cand) 最小。这不需要重编译，
所以可以扫得很细，并在**多个档位**上验证 τ 是常数（若它随 D 变化，
就不是「预延迟」而是别的东西，那这条修法本身就错了）。

先做**增益配平**再算残差：候选 echo1 峰值本就高 3…6%，不配平的话增益差会
污染 τ 的最优点（同一个坑在 ref_delay_rounds_ab.py 里踩过：不对齐就配平，
增益被错位压低，读出假的 gain=0.5009）。这里反过来：不配平就对齐，
τ 会被增益差拉偏。两个都要做，顺序是 **先扫 τ、每个 τ 内部解析配平**。
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
AT = 19200
AMP = 1e-2
LFO_PHASE = 0.238423
WIN = 1024
ORDER = 15          # 与 kArchFracInterpOrder 同族（中心 Lagrange）


def frac_delay(x: np.ndarray, tau: float, order: int = ORDER) -> np.ndarray:
    """中心 Lagrange 分数延迟。整数部分用移位，分数部分用插值核。"""
    n0 = int(np.floor(tau))
    t = tau - n0
    half = order // 2
    idx = np.arange(order + 1) - half
    c = np.ones(order + 1)
    for i in range(order + 1):
        for j in range(order + 1):
            if i != j:
                c[i] *= (t - idx[j]) / (idx[i] - idx[j])
    y = np.zeros(len(x) + n0 + order + 2)
    for i in range(order + 1):
        s = n0 + idx[i]
        if s >= 0:
            y[s:s + len(x)] += c[i] * x
        else:
            y[:len(x) + s] += c[i] * x[-s:]
    return y[:len(x)]


def nrmse_at(ref: np.ndarray, cand: np.ndarray, tau: float) -> tuple[float, float]:
    """给候选加 τ 样点延迟，解析配平增益后返回 (nrmse%, gain)。"""
    d = frac_delay(cand, tau)
    g = float(np.dot(ref, d) / max(np.dot(d, d), 1e-300))
    resid = ref - g * d
    nr = 100.0 * float(np.sqrt(np.mean(resid ** 2))
                       / max(np.sqrt(np.mean(ref ** 2)), 1e-300))
    return nr, g


def measure(norm: float) -> tuple[float, float, float]:
    """返回该档的 (最优 τ, 该 τ 下的 nrmse%, 增益)。"""
    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    rp = {"delay_drywet": 1.0, "delay_time_l": norm, "delay_time_r": norm,
          "delay_feedback": 0.0, "delay_lowpass": 1.0, "delay_highpass": 0.0,
          "delay_mode": 1.0}
    cp = {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
          "d_timel": norm, "d_timer": norm, "d_feedback": 0.0,
          "d_lowpass": 1.0, "d_highpass": 0.0, "d_stereo": 1.0,
          "d_lfophase": LFO_PHASE}

    n = AT + 200000
    x = np.zeros(n, dtype=np.float64)
    x[AT] = AMP
    yr = np.asarray(ref.render(x, rp)[0], float)
    yc = np.asarray(cand.render(x, cp)[0], float)

    # 同一段绝对样点（对齐会把要测的量吃掉）
    pr = int(np.argmax(np.abs(yr[AT + 64:]))) + AT + 64
    a0 = pr - 40
    sr_seg, sc_seg = yr[a0:a0 + WIN], yc[a0:a0 + WIN]

    # 粗扫 → 细扫（0.001 样点分辨率足够：DSP 侧的插值本身就是这个量级）
    taus = np.arange(2.0, 8.0, 0.05)
    vals = [nrmse_at(sr_seg, sc_seg, t)[0] for t in taus]
    t0 = float(taus[int(np.argmin(vals))])
    fine = np.arange(t0 - 0.06, t0 + 0.06, 0.001)
    fv = [nrmse_at(sr_seg, sc_seg, t)[0] for t in fine]
    tb = float(fine[int(np.argmin(fv))])
    nr, g = nrmse_at(sr_seg, sc_seg, tb)
    return tb, nr, g


def main() -> None:
    print(f"\n{'=' * 78}")
    print("湿抽头预延迟的分数部分：逐档拟合 τ（τ 必须是常数，否则修法就错了）")
    print(f"{'=' * 78}")
    print("   档位 norm     最优 τ(样点)   nrmse%    增益")

    rows = []
    for norm in (0.0, 0.25, 0.5, 0.65, 0.85, 1.0):
        try:
            tb, nr, g = measure(norm)
        except Exception as exc:
            print(f"   {norm:8.2f}   ← 失败：{exc}")
            continue
        rows.append((norm, tb, nr, g))
        print(f"   {norm:8.2f}   {tb:11.3f}   {nr:7.2f}  {g:7.4f}")

    if rows:
        t = np.array([r[1] for r in rows])
        print(f"\n  τ：均值 {t.mean():.3f}  标准差 {t.std():.3f}  "
              f"极差 {t.max() - t.min():.3f}")
        print("  判读：标准差 ≪ 0.1 ⇒ 是常数预延迟，可直接填进 DSP；")
        print("        若随档位系统性漂移 ⇒ 不是预延迟，别改这个常数。")
        print(f"\n  与既有常数合账：16 + {t.mean():.3f} = {16 + t.mean():.3f}"
              f"  ⇔ §14.1 长期未解释的「恒定 +21 样点」")


if __name__ == "__main__":
    main()
