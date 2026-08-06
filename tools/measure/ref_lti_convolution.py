"""LTI 性的终极检验：用实测 IR 做卷积，能否复现任意输入下的参考输出。

这个检验的意义：它给出**任何实现路线的精度上界**。
若卷积能到 float32 精度，说明混响确实是纯 LTI，那么「让 IR 对上」
就等价于「让任意信号的输出对上」——拟合目标可以只盯 IR。
若卷积做不到，说明存在时变/非线性成分（调制、噪声注入等），
那么逐样点 1e-3 在原理上就不可达，必须与用户重新对齐验收口径。

已知（REFERENCE §3）：
  * 重复渲染完全确定性（max|Δ| = 0）
  * 齐次性：0.5 倍激励 ×2 与 1.0 倍，相对误差 1.3e-4
  * 叠加性：IR(a+b) vs IR(a)+IR(b)，相对误差 1.7e-4
  * 幅度扫描：peak/amp 在 1e-3…1.0 上恒为 0.064490（5 位有效）

本脚本把这些点连成线：直接对**长噪声**做卷积对拍。

用法：python3 tools/measure/ref_lti_convolution.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from plugin_match import vst3_ref as V              # noqa: E402
from plugin_match.nrev_cand import report           # noqa: E402

SR = 48000
LATENCY = 51
IMPULSE_AT = int(2.0 * SR)
TAIL_SEC = 6.0

PARAMS = {"reverb_drywet": 1.0, "reverb_predelay": 0.5, "reverb_decay": 0.5}


def main():
    r = V.Vst3RefRenderer(sr=SR, block=512)
    n = IMPULSE_AT + int(TAIL_SEC * SR)

    def rend(x):
        return r.render(x, params=PARAMS).astype(np.float64)[0][IMPULSE_AT + LATENCY:]

    # ---- 采 IR ----
    imp = np.zeros(n, dtype=np.float32)
    imp[IMPULSE_AT] = 1.0
    ir = rend(imp)
    print(f"IR 长度 {len(ir)} 样点，峰值 {np.abs(ir).max():.6e}")

    cases = []

    # ---- 用例 1：双冲激（叠加性的直接推论，应当最好）----
    d = 4800
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    x[IMPULSE_AT + d] = 1.0
    cases.append(("2 冲激 (间隔 100 ms)", x))

    # ---- 用例 2：短噪声突发 ----
    rng = np.random.default_rng(3)
    for ms, amp in [(10, 0.05), (50, 0.05), (500, 0.2)]:
        ln = int(ms / 1000 * SR)
        x = np.zeros(n, dtype=np.float32)
        x[IMPULSE_AT:IMPULSE_AT + ln] = (amp * rng.standard_normal(ln)).astype(np.float32)
        cases.append((f"噪声突发 {ms} ms (amp {amp})", x))

    # ---- 用例 3：正弦突发（周期信号，检验梳状响应）----
    ln = int(0.2 * SR)
    t = np.arange(ln) / SR
    x = np.zeros(n, dtype=np.float32)
    x[IMPULSE_AT:IMPULSE_AT + ln] = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    cases.append(("440 Hz 正弦突发 200 ms", x))

    print()
    for name, x in cases:
        y_ref = rend(x)
        y_cnv = np.convolve(x[IMPULSE_AT:].astype(np.float64), ir)[:len(y_ref)]
        report(y_ref, y_cnv, label=name)

    print("\n若上述用例的波形 max|Δ| 都在 1e-4 量级，则混响为纯 LTI，")
    print("「让 IR 对上」= 「让任意信号对上」，拟合目标可只盯 IR。")


if __name__ == "__main__":
    main()
