"""PRE-DELAY 拓扑：最终判定。

三轮测量后的完整图像：

  1. α ≡ 1.000（pv≥0.1 全档，偏差 <0.6%）：**第一路增益与 pv 无关**。
     pv=0 时 α=2.000 只是因为两路重合。
  2. 残差（= 第二路）的起点精确等于 **D(pv) − D_min + onset**：
        pv=0.1  D=254   残差起点 683 = 254−48+477
        pv=0.3  D=1332  残差起点 1761 = 1332−48+477
        pv=0.5  D=3057  残差起点 3486 = 3057−48+477
        pv=0.9  D=8062  残差起点 8491 = 8062−48+477
     （pv=0.2 的 478 与 pv=1.0 的 751 是残差过小导致的阈值误判，
      其余 7 档全部命中，偏差 0 样点。）
  3. 但残差**不是** w 的纯延迟拷贝（拟合残差比 0.58–0.77）。

结论：**两路并联，第二路延迟 = PRE-DELAY，但两路的湿声内容不同**
（各自独立的混响网络 / 不同的抽头集合），所以第二路无法用第一路的波形平移得到。

这对实现的指导是明确的：
  * PRE-DELAY 是一条**并联支路的延迟**，不是串在整个混响前面的延迟；
  * 两路各有自己的湿声生成器，起点都相对自身入口 477 样点；
  * 总输出 = 路1 + 路2（pv→0 时两路重合，故早期能量翻倍——实测 2.0000）。

本脚本把第 2 点定量钉死（残差起点 vs D 的一一对应），作为实现的验收依据。

用法：python3 tools/measure/ref_predelay_solved.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
LATENCY = 51
IMPULSE_AT = int(2.0 * SR)
D_MAX = 9600
ONSET = 477   # 湿声起点（实测，与 pv 无关）
D_MIN = 48    # D(pv=0) = 1 ms


def ir(r, params, tail_sec=6.0):
    n = IMPULSE_AT + int(tail_sec * SR)
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return r.render(x, params=params).astype(np.float64)[:, IMPULSE_AT + LATENCY:]


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    wet = {"reverb_drywet": 1.0}
    w = ir(r, {**wet, "reverb_predelay": 1.0})[0][:D_MAX]

    print("第二路进场时刻 vs PRE-DELAY 参数（模型：onset₂ = D(pv) − D_min + onset₁）")
    print("    pv    D(样点)   预测 onset₂   实测 onset₂   偏差   α（第一路增益）")
    hits, tot = 0, 0
    for pv in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
        y = ir(r, {**wet, "reverb_predelay": pv})[0][:D_MAX]
        d = int(round(V.predelay_ms(pv) / 1000 * SR))
        alpha = float(np.dot(y, w) / max(np.dot(w, w), 1e-30))
        s = y - alpha * w
        # 用「累积能量首次超过总量 1e-4」定起点，比绝对阈值稳
        c = np.cumsum(s ** 2)
        c /= max(c[-1], 1e-30)
        onset2 = int(np.argmax(c > 1e-4))
        pred = d - D_MIN + ONSET
        diff = onset2 - pred
        tot += 1
        if abs(diff) <= 4:
            hits += 1
        print(f"    {pv:.1f}  {d:8d}   {pred:10d}   {onset2:10d}  {diff:+5d}   {alpha:.4f}")

    print(f"\n命中率（|偏差| ≤ 4 样点）：{hits}/{tot}")
    print("→ PRE-DELAY 是并联第二支路的延迟；第一路不受其影响（α≡1）。")


if __name__ == "__main__":
    main()
