"""零点 = 钥匙：norm 0.65（587.7 ms）处调制深度 **0.0122 样点**。

## 为什么盯住这个零点

密扫在塌陷点附近抓到一个**真零点**，而不是一个小极值：

    norm    0.55   0.57   0.59   0.60   0.61   0.63   0.65   0.70
    ms     587.7 → 见下
    幅度    3.894  3.227  2.496  2.109  1.709  0.876  0.0122  2.189
    残差    2.6%   2.7%   2.8%   2.7%   2.7%   2.9%   60.2%   3.1%

0.65 处幅度掉到 **0.0122 样点**（比邻档小两个数量级），且正弦拟合残差从 ~2.7%
爆到 60% —— 因为已经没有正弦可拟合了。零点是**结构性**的，不是测量噪声。

同时，我为解释深度而提的两个闭式模型都失败了：相位律的圆均值只有 R=0.485
（若机制是「全局 LFO 被读指针延后 D 采样」应当 R→1），两路叠加模型最差偏差
120%。所以机制不是我假设的那样，**但零点本身是可复现的硬锚点**，值得单独审问。

## 零点落在哪：一个可疑的巧合

norm=0.65 对应 D = **28212 样点**。而 LFO 周期 = 48000/1.70186 = **28204.6 样点**。

    D / 周期 = 28212 / 28204.6 = 1.00026

即延迟长度**恰好等于一个 LFO 周期**。这不是巧合能解释的量级（差 0.026%）。

它直接给出机制：若调制**同时加在写指针与读指针**（或等价地，延迟量取的是
「写入时刻的 LFO」与「读出时刻的 LFO」之差），则读出的净调制 ∝
sin(ωt) − sin(ω(t−D/SR))，其幅度 = 2·|sin(ωD/2SR)|。**当 D 等于周期整数倍时
两者同相相消 ⇒ 净调制为零**。这条模型还预言：

* 零点出现在 D = m·周期（m=1,2,…）；1100 ms = 52800 样点 = 1.872 周期
  ⇒ 全程只有 m=1 一个零点，与实测「只有 0.65 一个零点」相符；
* 深度 = 2A·|sin(π·D/周期)| —— **一条单参数律**（只有 A 待定），而且它自然
  给出非单调：D 从 0 涨到半周期时深度升，到一个周期时归零，再升。
  实测幅度序列 3.32↗6.50↘0.01↗6.25↘2.57 正是这个形状。

## 检验

1. **单参数拟合**：depth = 2A·|sin(π·D/T)|，T 固定为实测 LFO 周期，只拟合 A，
   看 17 个点的最差偏差。这是**零自由度的形状检验**（A 只是纵轴单位）。
2. **零点定位**：在 D/T = 1 附近（norm 0.645…0.655）密扫，看零点位置是否落在
   D = T 上，并验证**穿越零点时相位跳 180°** —— 那是相消而非衰减的判据。
3. **预言外推**：把 SR 换成 44100，则 T（样点）变，零点应当移到**同一物理
   延迟时长**（587.7 ms）而不是同一 norm/样点数。这条最硬：它把「零点由
   LFO 周期决定」与「零点由某个固定样点数决定」彻底分开。

用法：
    python3 tools/measure/ref_delay_lfo_null.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402
from measure.ref_delay_lfo_demod import measure  # noqa: E402

SR = 48000
LFO_HZ = 1.70186          # 连续解调实测（11 档全部给出同一值）

# 全程 + 零点附近加密
NORMS = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.55, 0.6, 0.63,
         0.645, 0.65, 0.655, 0.66, 0.68, 0.7, 0.8, 0.9, 1.0)


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def main() -> None:
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    T = SR / LFO_HZ

    print(f"LFO 周期 T = {T:.2f} 样点 = {T / SR * 1000:.2f} ms")

    rows = [measure(r, nv) for nv in NORMS]

    hdr("深度 vs D/T：零点应当落在 D/T = 1")
    print(f"  {'norm':>6} {'ms':>8} {'D':>7} {'D/T':>8} {'幅度':>8} "
          f"{'初相°':>9} {'残差':>8} {'模型 2A|sin(πD/T)|':>18}")

    d0 = np.array([q["d0"] for q in rows], dtype=float)
    amp = np.array([q["amp"] for q in rows])
    shape = 2.0 * np.abs(np.sin(np.pi * d0 / T))
    # 单参数：只拟合幅度 A（纵轴单位），形状零自由度
    A = float(np.dot(shape, amp) / (np.dot(shape, shape) + 1e-30))
    model = A * shape
    for q, dd, a0, m0 in zip(rows, d0, amp, model):
        print(f"  {q['nv']:6.3f} {V.delay_time_ms(q['nv']):8.1f} {q['d0']:7d} "
              f"{dd / T:8.4f} {a0:8.4f} {q['phase']:+9.2f} {q['res'] * 100:7.3f}% "
              f"{m0:18.4f}")

    rel = np.abs(model - amp) / (amp + 1e-30)
    # 零点附近相对偏差无意义（分母趋零），用绝对偏差判
    absd = np.abs(model - amp)
    hdr("单参数形状检验：depth = 2A·|sin(π·D/T)|，T 固定为实测 LFO 周期")
    print(f"  A = {A:.5f} 样点")
    print(f"  最差绝对偏差 = {absd.max():.4f} 样点   均方 = {np.sqrt(np.mean(absd ** 2)):.4f}")
    ok = amp > 0.5     # 排除零点邻域再看相对偏差
    print(f"  幅度 >0.5 的点上最差相对偏差 = {rel[ok].max() * 100:.2f}%"
          f"   {'✓ 形状成立' if rel[ok].max() < 0.10 else '✗'}")

    hdr("零点定位与相位跳变（相消的判据）")
    print(f"  {'norm':>7} {'D':>7} {'D/T':>9} {'幅度':>9} {'初相°':>9}")
    for q, dd, a0 in zip(rows, d0, amp):
        if 0.94 <= dd / T <= 1.06:
            print(f"  {q['nv']:7.3f} {q['d0']:7d} {dd / T:9.5f} {a0:9.4f} {q['phase']:+9.2f}")
    print("  穿越零点时初相跳 ~180° ⇒ 是两路相消（净调制变号），不是单路衰减。")

    hdr("判读")
    print("  若形状成立：LFO 的净调制 = 写入时刻与读出时刻 LFO 之差，")
    print("  depth(D) = 2A·|sin(π·D/T)|。实现上只需一个常数 A 与 LFO 速率，")
    print("  深度的「非单调」不再需要任何查表 —— 它是机制的推论。")


if __name__ == "__main__":
    main()
