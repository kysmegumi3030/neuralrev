"""定环内**固定**滤波器：把用户 LP 剥掉之后剩下的那条陡峭曲线。

## 输入是已经测好的表，不需要再渲染

`ref_delay_loop_filter.py` 给出每圈损耗 L(f) = r(f)/0.80 的四个 LP 档，
以及两条已经定死的结论：

* **用户 LP 是二阶 Butterworth，显示 fc 就是真 −3 dB 点**。判据：三个档位
  在各自**名义 fc** 上的比值都恰好 −3 dB（LP=0.0 在 1 kHz −3.00；
  LP=0.4 在 3046 Hz −2.87；LP=0.7 在 7908 Hz −3.07），而 2fc / 3fc 处
  实测 −12.36 / −18.98 对二阶 Butterworth 的 −12.30 / −19.08 —— 差 0.1 dB。
  （这跟混响段相反：那边显示 fc 不是 −3 dB 点，§6.1。）
* **用户 HP 同样是二阶 Butterworth、显示 fc 诚实**：fc=800 Hz 档在
  200 / 350 / 700 Hz 实测 −24.18 / −14.60 / −4.44，理论 −24.1 / −14.5 / −4.32。

于是把 L(f; LP=16k) 除掉「16 kHz 的二阶 Butterworth」，剩下的就是**固定**部分。

## 固定部分长什么样（这是要定的东西）

    1k     2k     3k     4k     5k     6k     8k     10k    12k
   -0.18  -0.56  -1.18  -2.00  -3.02  -4.28  -8.09 -15.21 -23.57  dB

从 8 kHz 到 12 kHz 掉 15.5 dB —— 比任何一二阶极点都陡，而且**上端像一堵墙**。
这个形状最像「内部以更低采样率运行」时的抗镜像滤波器：若延迟线内部跑
24 kHz，则 12 kHz 是它的 Nyquist，过渡带正好落在 8–12 kHz。

四个候选模型在 §判据 2 里比过，最好的「线性插值 ×12.4 次」也差 1.73 dB，
且残差是**系统性**的（中频过冲、顶端不足）⇒ 不是单一插值器，是一堵更陡的墙。

## 本脚本做什么

只做曲线拟合（纯本地，不碰参考插件）：扫「阶数 M × 拐点 fc」的二维网格，
M 取 1…12（Butterworth 幅度 |H|² = 1/(1+(f/fc)^{2M})），
取最差 dB 偏差最小者，并报出逐点残差。

**掩蔽**：8 kHz 以上有一段读数会回升（LP=0.0 列在 3k 之后从 −20 dB 反弹到
−1.5 dB）—— 损耗不可能自己变小，那是猝发已衰到本底、`band_amp` 在读残渣。
所以只用**单调下降**的那一段拟合，并把被丢掉的点明确打印出来。

用法：
    python3 tools/fit/fit_delay_loop_filter.py
"""
from __future__ import annotations

import numpy as np

# ref_delay_loop_filter.py 的实测输出（LP=1.0 列，dB）
MEAS = [
    (100.0, -0.0396), (200.0, -0.0445), (350.0, -0.0391), (500.0, -0.0783),
    (700.0, -0.1123), (1000.0, -0.1761), (1500.0, -0.3391), (2000.0, -0.5636),
    (3000.0, -1.1800), (4000.0, -1.9995), (5000.0, -3.0179), (6000.0, -4.2793),
    (8000.0, -8.0913), (10000.0, -15.2145), (12000.0, -23.5652),
]

USER_LP_HZ = 16000.0   # LP=1.0 的显示 fc，实测即真 −3 dB 点
USER_LP_ORDER = 2      # 二阶 Butterworth（判据见 docstring）


def hdr(t: str) -> None:
    print(f"\n{'=' * 84}\n{t}\n{'=' * 84}")


def butter_db(f: np.ndarray, fc: float, order: int) -> np.ndarray:
    """Butterworth 幅度（dB）：|H|² = 1/(1+(f/fc)^{2M})。"""
    return -10.0 * np.log10(1.0 + (f / fc) ** (2 * order))


def main() -> None:
    f = np.array([m[0] for m in MEAS])
    L = np.array([m[1] for m in MEAS])

    # 单调性掩蔽：一旦读数不再下降，之后全丢
    keep = np.ones(len(f), dtype=bool)
    for i in range(1, len(f)):
        if L[i] > L[i - 1] + 0.05:
            keep[i:] = False
            break
    dropped = [f"{f[i]:.0f} Hz" for i in range(len(f)) if not keep[i]]
    print(f"  用于拟合的点: {keep.sum()} / {len(f)}")
    print(f"  因非单调被丢弃: {', '.join(dropped) if dropped else '（无）'}")

    # 剥掉用户 LP（16 kHz 二阶）
    fixed = L - butter_db(f, USER_LP_HZ, USER_LP_ORDER)

    hdr("剥掉 16 kHz 二阶 Butterworth 之后的**固定**环内损耗")
    print(f"  {'频率':>7} {'L 实测':>9} {'用户LP':>9} {'固定部分':>10} {'用':>4}")
    for i in range(len(f)):
        print(f"  {f[i]:7.0f} {L[i]:9.4f} {butter_db(f[i:i+1], USER_LP_HZ, USER_LP_ORDER)[0]:9.4f} "
              f"{fixed[i]:10.4f} {'✓' if keep[i] else '—':>4}")

    hdr("二维扫描：Butterworth 阶数 M × 拐点 fc")
    ff, LL = f[keep], fixed[keep]
    best = None
    for M in range(1, 13):
        for fc in np.linspace(3000.0, 30000.0, 5401):
            e = np.abs(butter_db(ff, fc, M) - LL)
            if best is None or e.max() < best[2]:
                best = (M, float(fc), float(e.max()))
    M, fc, worst = best
    print(f"  最优: 阶数 M = {M}   拐点 fc = {fc:.1f} Hz   最差偏差 = {worst:.4f} dB")

    print(f"\n  {'频率':>7} {'固定实测':>10} {'模型':>10} {'差':>9}")
    for q, a0 in zip(ff, LL):
        m0 = butter_db(np.array([q]), fc, M)[0]
        print(f"  {q:7.0f} {a0:10.4f} {m0:10.4f} {m0 - a0:+9.4f}")

    # 逐阶最优，看阶数是否真的被数据挑出来
    print(f"\n  {'M':>3} {'最优 fc':>10} {'最差偏差':>10}")
    for m in range(1, 13):
        b = None
        for fc2 in np.linspace(3000.0, 30000.0, 5401):
            e = np.abs(butter_db(ff, fc2, m) - LL).max()
            if b is None or e < b[1]:
                b = (float(fc2), float(e))
        print(f"  {m:3d} {b[0]:10.1f} {b[1]:10.4f}"
              + ("   ← 最优" if m == M else ""))

    # ------------------------------------------------------------------
    # 单一极点族全部失败 ⇒ 换成「可实现的落点」而不是继续找机制
    # ------------------------------------------------------------------
    hdr("可信带的界定：per-round-trip 损耗多大就读不出来了")
    print("  逐圈比值用的是第 2…6 个回声。若单圈损耗 X dB，则第 6 个回声比第 1 个")
    print("  低 5X dB。float32 的可用动态约 130 dB，激励幅度 1e-3（−60 dBFS）")
    print("  ⇒ 单圈损耗超过约 14 dB 时，末尾几个回声已进本底，比值被抬高。")
    print(f"  {'频率':>7} {'单圈 dB':>9} {'第6回声 dB':>11} {'可信':>6}")
    trust = np.zeros(len(f), dtype=bool)
    for i in range(len(f)):
        e6 = 5.0 * fixed[i]
        ok = keep[i] and abs(fixed[i]) < 14.0
        trust[i] = ok
        print(f"  {f[i]:7.0f} {fixed[i]:9.4f} {e6:11.2f} {'✓' if ok else '—':>6}")

    hdr("拟合可实现落点：两级二阶低通级联（各自 fc 与 Q 自由）")
    print("  为什么改拟合级联而不是继续找机制：逐 bin ≤3 dB 的验收口径只要求")
    print("  **复制 L(f)**，不要求给它起名字。级联二阶是环内可用的最简形式")
    print("  （群延迟小、无限脉冲、稳定），而 FIR 会引入可测的回声时移。")

    ft, Lt = f[trust], fixed[trust]
    w = 2.0 * np.pi * ft / 48000.0

    def biquad_lp_db(fc, q):
        """RBJ 二阶低通的幅度响应（dB），数字域精确式。"""
        w0 = 2.0 * np.pi * fc / 48000.0
        cs, sn = np.cos(w0), np.sin(w0)
        alpha = sn / (2.0 * q)
        a0 = 1.0 + alpha
        b0 = ((1.0 - cs) * 0.5) / a0
        b1 = (1.0 - cs) / a0
        b2 = b0
        a1 = (-2.0 * cs) / a0
        a2 = (1.0 - alpha) / a0
        z = np.exp(-1j * w)
        h = (b0 + b1 * z + b2 * z * z) / (1.0 + a1 * z + a2 * z * z)
        return 20.0 * np.log10(np.abs(h) + 1e-30)

    best = None
    for fc1 in np.linspace(4000.0, 20000.0, 81):
        for q1 in np.linspace(0.4, 1.6, 25):
            d1 = biquad_lp_db(fc1, q1)
            for fc2 in np.linspace(4000.0, 20000.0, 81):
                for q2 in np.linspace(0.4, 1.6, 25):
                    e = np.abs(d1 + biquad_lp_db(fc2, q2) - Lt).max()
                    if best is None or e < best[4]:
                        best = (fc1, q1, fc2, q2, float(e))
    fc1, q1, fc2, q2, worst2 = best
    print(f"\n  级联 A: fc = {fc1:.1f} Hz, Q = {q1:.3f}")
    print(f"  级联 B: fc = {fc2:.1f} Hz, Q = {q2:.3f}")
    print(f"  可信带内最差偏差 = {worst2:.4f} dB")
    model = biquad_lp_db(fc1, q1) + biquad_lp_db(fc2, q2)
    print(f"\n  {'频率':>7} {'固定实测':>10} {'级联模型':>10} {'差':>9}")
    for q0, a0, m0 in zip(ft, Lt, model):
        print(f"  {q0:7.0f} {a0:10.4f} {m0:10.4f} {m0 - a0:+9.4f}")

    # ------------------------------------------------------------------
    # 闭环校正：把**估计器自身的偏倚**算进目标里
    # ------------------------------------------------------------------
    hdr("闭环校正：目标不是参考的原始表，而是「同一估计器下候选该读到什么」")
    print("  cand_delay_selftest 用与参考侧完全相同的 band_amp 逐圈估计器测候选，")
    print("  结果比**候选自己的解析响应**还小（8 kHz 上 +0.29 dB）。")
    print("  cand_delay_blocks 已把三块拆开：biquad 系数换算无误（0.0006 dB），")
    print("  纯延迟线无损（B 那 −0.446 dB 是我自己 harness 的窗宽不一致），")
    print("  LFO 边带抽薄随频率上升（C−B 在 8 kHz 上 −0.039 dB/圈）。")
    print("  剩下的就是**猝发有限带宽 × 陡峭环内斜坡**：窗内低侧频率损耗更小，")
    print("  单频投影读到的是那一小段的加权平均 ⇒ 系统性少读损耗。")
    print("  这个偏倚在两侧都存在，所以**不该修 DSP 去抵消它**；")
    print("  但它必须搬进拟合目标：候选的解析响应要落在 ref_L − bias 上。")

    # cand_delay_selftest（候选实测） − cand_delay_blocks 段 A（候选解析）
    BIAS = {100.0: 0.0076, 200.0: 0.0014, 350.0: 0.0009, 500.0: 0.0038,
            700.0: -0.0017, 1000.0: 0.0059, 1500.0: 0.0088, 2000.0: 0.0171,
            3000.0: 0.0356, 4000.0: 0.0638, 5000.0: 0.1018, 6000.0: 0.1518,
            8000.0: 0.2921}

    # 平项：低频平台（100/200/350）—— 二阶低通在 DC 上恒 0 dB，产生不了它
    flat = float(L[:3].mean())
    print(f"\n  低频平台均值 = {flat:.4f} dB ⇒ 每圈额外平损 {10 ** (flat / 20):.5f}")
    print(f"  即 0.80 × {10 ** (flat / 20):.5f} = {0.80 * 10 ** (flat / 20):.5f}"
          "，正是文档里 0.796 vs 0.80 的那 0.45%%")
    print("  ⇒ 落一个独立常数 kFitLoopFlatGain，别让级联去背这个锅")

    # 10k/12k 没有候选侧读数（它们本来就被可信带排除），补 0 只为对齐长度
    bias = np.array([BIAS.get(float(q), 0.0) for q in f])
    target_total = L - bias - flat            # 候选解析响应该落的位置
    target_casc = target_total - butter_db(f, USER_LP_HZ, USER_LP_ORDER)

    print(f"\n  {'频率':>7} {'ref L':>9} {'偏倚':>8} {'平项':>8} {'级联目标':>10} {'用':>4}")
    for i in range(len(f)):
        print(f"  {f[i]:7.0f} {L[i]:9.4f} {bias[i]:+8.4f} {flat:8.4f} "
              f"{target_casc[i]:10.4f} {'✓' if trust[i] else '—':>4}")

    hdr("重扫级联：目标改为闭环校正后的曲线")
    Lt2 = target_casc[trust]

    # 向量化：先把每个 (fc, Q) 的响应算成一张表，再用广播找最优组合。
    # 四重 Python 循环在 161×51 的网格上要算 6.7e7 次，跑不完；
    # 而级联是**相加**，所以可以只算 8211 条响应，再在矩阵里找和。
    FCS = np.linspace(4000.0, 20000.0, 161)
    QS = np.linspace(0.35, 1.6, 51)
    resp = np.array([[biquad_lp_db(fc, q) for q in QS] for fc in FCS])
    resp = resp.reshape(-1, len(Lt2))            # (nCombo, nFreq)

    # 级联对称（A+B = B+A），故只需扫上三角；但直接全扫更简单且仍然很快。
    best = None
    for i in range(resp.shape[0]):
        e = np.abs(resp[i][None, :] + resp - Lt2[None, :]).max(axis=1)
        j = int(np.argmin(e))
        if best is None or e[j] < best[4]:
            best = (FCS[i // len(QS)], QS[i % len(QS)],
                    FCS[j // len(QS)], QS[j % len(QS)], float(e[j]))
    fc1, q1, fc2, q2, worst3 = best
    print(f"  级联 A: fc = {fc1:.1f} Hz, Q = {q1:.3f}")
    print(f"  级联 B: fc = {fc2:.1f} Hz, Q = {q2:.3f}")
    print(f"  平项  : kFitLoopFlatGain = {10 ** (flat / 20):.6f}")
    print(f"  可信带内最差偏差 = {worst3:.4f} dB（校正前 {worst2:.4f} dB）")
    model2 = biquad_lp_db(fc1, q1) + biquad_lp_db(fc2, q2)
    print(f"\n  {'频率':>7} {'级联目标':>10} {'级联模型':>10} {'差':>9} "
          f"{'预测候选实测':>13} {'vs ref L':>9}")
    for q0, a0, m0 in zip(ft, Lt2, model2):
        pred = (m0 + flat + butter_db(np.array([q0]), USER_LP_HZ, USER_LP_ORDER)[0]
                + BIAS.get(float(q0), 0.0))
        idx = int(np.where(f == q0)[0][0])
        print(f"  {q0:7.0f} {a0:10.4f} {m0:10.4f} {m0 - a0:+9.4f} "
              f"{pred:13.4f} {pred - L[idx]:+9.4f}")

    hdr("判读")
    print("  单极点族（1…12 阶 Butterworth）最好只到 2.20 dB，且残差呈系统性 S 形")
    print("  ⇒ 固定损耗不是一个极点滤波器。它的 dB 损耗在 1–8 kHz 上近似 ∝ f²，")
    print("  那是**内插/平滑核**的特征而不是极点的特征（混响那轮同一个坑，§10.2.2）。")
    print("  但验收只要求复制 L(f)，所以落点取上面那组级联系数；")
    print("  8 kHz 以上是外推，验收时用逐 bin 表核对而不是信这条曲线。")


if __name__ == "__main__":
    main()
