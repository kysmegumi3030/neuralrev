"""延迟段的隔离验证与基本可测性 —— 对应混响那轮的 `ref_probe.py`。

四件事，顺序不能换（每一步都是下一步的前提）：

1. **隔离成立吗**：`delay_active=0` 时干路是否 bit-transparent、固有延迟是否
   还是 51 样点。若不成立，后面测到的一切都混着别的段。
2. **起步淡入**：混响那轮实测插件前 ~0.1–0.2 s 有淡入（冲激放 0.02 s 输出为 0），
   所以所有激励统一放在 2.0 s。这里复验一次 —— 不能假设两段共用同一个淡入。
3. **湿声起点与 Delay Time 的关系**：显示 500 ms 时第一个回声落在哪？
   这是「显示值是不是真实延迟」的第一手判据（混响 §6.1 的教训：显示 fc 不是
   −3 dB 点，所以显示 ms 也不能想当然）。
4. **线性性 / 时不变性**：混响是线性**时变**的（内部 LFO），验收口径因此被迫
   放宽到平滑谱。延迟**可能是 LTI**，那样 1e-3 的原始波形口径就是可达的。
   这一条决定整个延迟工作的验收标准，必须先测清楚。

用法：
    python3 tools/measure/ref_delay_probe.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from plugin_match import vst3_ref as V  # noqa: E402

SR = 48000
IMP_AT = 2.0        # 激励位置（秒）—— 绕开起步淡入
DUR = 8.0           # 渲染总长（秒）


def impulse(n, at, amp=1.0):
    x = np.zeros(n, dtype=np.float32)
    x[int(round(at))] = amp
    return x


def hdr(t):
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


def main() -> None:
    n = int(DUR * SR)
    at = int(IMP_AT * SR)
    r = V.Vst3RefRenderer(sr=SR, block=512, section="delay")

    # ---------------------------------------------------------------- 1. 隔离
    hdr("1. 隔离验证：delay_active=0 时干路应当 bit-transparent")
    x = np.random.default_rng(0).standard_normal(n).astype(np.float32) * 0.25
    y = r.render(x, {"delay_active": 0.0})
    yl = y[0]
    # 找固有延迟：与输入的最大相关位置
    best, bd = -1.0, 0
    for d in range(0, 200):
        seg = yl[d:d + 20000]
        c = float(np.dot(seg, x[:len(seg)]) / (np.linalg.norm(seg) * np.linalg.norm(x[:len(seg)]) + 1e-30))
        if c > best:
            best, bd = c, d
    print(f"  固有延迟   = {bd} 样点   (混响段实测 51)")
    print(f"  corr       = {best:.9f}")
    # **必须跳过起步淡入再比**（见下面第 2 节）：淡入区里输出被压到近 0，
    # 把它算进 max|y−x| 会读出 9.7e-01 并把「透明」误判成「不透明」——
    # 那是淡入的指纹，不是泄漏。混响段当年没踩到只是因为它的对齐段恰好在淡入之后。
    for skip in (0, 1000, SR // 2, SR):
        seg = yl[bd + skip:n]
        ref = x[skip:n - bd]
        m = min(len(seg), len(ref))
        dev = float(np.max(np.abs(seg[:m] - ref[:m])))
        tag = "✓ bit-transparent" if dev < 1e-6 else ("（淡入区，见第 2 节）" if skip < SR // 2 else "✗ 不透明")
        print(f"  跳过 {skip:6d} 样点  max|y-x| = {dev:.3e}   {tag}")

    # ------------------------------------------------------------ 2. 起步淡入
    hdr("2. 起步淡入：冲激放在不同时刻，看湿声峰值是否被压")
    for t in (0.02, 0.05, 0.10, 0.20, 0.50, 1.00, 2.00):
        xi = impulse(n, t * SR)
        yi = r.render(xi, {"delay_drywet": 1.0, "delay_feedback": 0.0})
        print(f"  冲激@{t:5.2f}s  湿声峰值 = {float(np.max(np.abs(yi))):.6f}")

    # ------------------------------------- 3. 湿声起点 vs 显示的 Delay Time
    hdr("3. 第一个回声的位置 vs 显示的 Delay Time（显示值是真实延迟吗）")
    print(f"  {'norm':>8} {'显示 ms':>10} {'预测样点':>10} {'实测样点':>10} {'偏差':>8} {'实测 ms':>10}")
    for nv in (0.0, 0.2, 0.4, 0.577079952, 0.8, 1.0):
        ms = V.delay_time_ms(nv)
        xi = impulse(n, at)
        yi = r.render(xi, {"delay_drywet": 1.0, "delay_feedback": 0.0,
                           "delay_time_l": nv, "delay_time_r": nv})
        w = yi[0]
        # 湿声起点：峰值 1% 以上的第一个样点（从激励之后开始找）
        thr = float(np.max(np.abs(w))) * 0.01
        idx = np.where(np.abs(w[at:]) > thr)[0]
        got = int(idx[0]) if len(idx) else -1
        pred = int(round(ms * SR / 1000.0))
        print(f"  {nv:8.4f} {ms:10.2f} {pred:10d} {got:10d} {got - pred:8d} "
              f"{got * 1000.0 / SR:10.3f}")

    # -------------------------------------------------- 4. 线性性 / 时不变性
    hdr("4. 线性性与时不变性（决定验收口径能否用原始波形 1e-3）")
    p = {"delay_drywet": 1.0, "delay_feedback": 0.5, "delay_time_l": 0.4,
         "delay_time_r": 0.6}

    # 重复渲染一致性
    a = r.render(impulse(n, at), p)
    b = r.render(impulse(n, at), p)
    print(f"  重复渲染 max|Δ|        = {float(np.max(np.abs(a - b))):.3e}"
          f"   {'✓ 确定性' if np.array_equal(a, b) else '✗'}")

    # 齐次性
    h1 = r.render(impulse(n, at, 1.0), p)
    h2 = r.render(impulse(n, at, 0.5), p) * 2.0
    d = float(np.max(np.abs(h1 - h2))) / (float(np.max(np.abs(h1))) + 1e-30)
    print(f"  齐次性 相对误差        = {d:.3e}   {'✓ 线性' if d < 1e-3 else '✗ 非线性'}")

    # 时不变性：把冲激挪 1 ms，对齐后比较
    s = int(0.001 * SR)
    t1 = r.render(impulse(n, at), p)[0]
    t2 = r.render(impulse(n, at + s), p)[0]
    m = min(len(t1) - at, len(t2) - at - s)
    u, v = t1[at:at + m], t2[at + s:at + s + m]
    nrmse = float(np.linalg.norm(u - v) / (np.linalg.norm(u) + 1e-30))
    print(f"  时不变性 nrmse(1 ms)   = {nrmse * 100:.4f}%"
          f"   {'✓ 时不变（LTI）' if nrmse < 1e-3 else '✗ 时变'}"
          f"   [混响段是 9.2% ⇒ 时变]")

    for sh in (1, 16, 480, 4800):
        t2 = r.render(impulse(n, at + sh), p)[0]
        m = min(len(t1) - at, len(t2) - at - sh)
        u, v = t1[at:at + m], t2[at + sh:at + sh + m]
        e = float(np.linalg.norm(u - v) / (np.linalg.norm(u) + 1e-30))
        print(f"    位移 {sh:5d} 样点  nrmse = {e * 100:8.4f}%")

    # 立体声相关性
    print(f"\n  corr(L,R) @ time_l≠time_r = "
          f"{float(np.corrcoef(a[0], a[1])[0, 1]):+.6f}")


if __name__ == "__main__":
    main()
