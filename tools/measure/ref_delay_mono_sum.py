"""Mono 模式：参考把两路输入**求和**还是**取平均**？

## 为什么这是个独立的问题

`ab_delay.py` 的 Mono 档报 `gain=0.4974` —— 配平标量差了整整一倍，而其它
11 档都在 0.95…1.00。一倍不可能来自滤波器/延迟的细节，只能来自一个结构性的
2（或 1/2）。候选当前是 `sum = inL + inR`，注释里引的实测依据是
「两输出在同一延迟处给出同一峰值」—— 那条只证明**两个输出相等**，
对「求和还是平均」完全是盲的（两种做法都给出两路相等的输出）。
这与 §14.11 同型：判据对要判的那个自由度不敏感。

## 判法：改变两路输入的**相对内容**，而不是看输出是否相等

对比两种激励下参考的输出幅度：

  * 只有 L 有信号（R 静音）：求和 ⇒ 1·x，平均 ⇒ 0.5·x
  * L = R = 同一信号：       求和 ⇒ 2·x，平均 ⇒ 1·x

于是**比值 r = |L=R 档| / |仅 L 档|** 在两种做法下都是 2 —— 这个比值判不了。
真正能判的是**绝对标度**：拿 Stereo 档（已知无求和、且电平已闭合）当尺子，
比较 Mono 仅-L 档与 Stereo 仅-L 档的输出幅度：

  * 求和   ⇒ Mono(仅L) / Stereo(仅L) = 1.0
  * 平均   ⇒ 0.5

Stereo 档的电平已由 11 个档位的 gain≈1 独立确认，所以它是可信的尺子。
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
AMP = 1e-3          # 线性区（§14.4）
NORM = 0.0          # D = 4800 精确整数，echo1 位置无歧义
LFO_PHASE = 0.238423


def echo1_peak(y: np.ndarray) -> float:
    """echo1 的峰值绝对幅度（fb=0 ⇒ AT 之后只有一次回声）。"""
    a = np.abs(np.asarray(y, float))
    return float(a[AT + 64:].max())


def run(renderer, params, mode_key: str, mono: bool, only_l: bool) -> float:
    n = AT + 40000
    xl = np.zeros(n, dtype=np.float64)
    xr = np.zeros(n, dtype=np.float64)
    xl[AT] = AMP
    if not only_l:
        xr[AT] = AMP
    p = dict(params)
    p[mode_key] = 0.0 if mono else 1.0
    out = renderer.render(np.stack([xl, xr]), p)
    return echo1_peak(out[0])


def main() -> None:
    rp = {"delay_drywet": 1.0, "delay_time_l": NORM, "delay_time_r": NORM,
          "delay_feedback": 0.0, "delay_lowpass": 1.0, "delay_highpass": 0.0}
    cp = {"drywet": 0.0, "d_active": 1.0, "d_drywet": 1.0,
          "d_timel": NORM, "d_timer": NORM, "d_feedback": 0.0,
          "d_lowpass": 1.0, "d_highpass": 0.0, "d_lfophase": LFO_PHASE}

    ref = V.Vst3RefRenderer(sr=SR, block=512, section="delay")
    cand = C.NrevRenderer(sr=SR, block=512)

    print(f"\n{'=' * 78}")
    print("Mono 求和 vs 平均（尺子 = Stereo 档，其电平已由 11 档 gain≈1 确认）")
    print(f"{'=' * 78}")
    print("           仅L/Stereo   仅L/Mono    比值(M/S)   L=R/Mono   判读")

    for name, r, params, key in (("参考", ref, rp, "delay_mode"),
                                 ("候选", cand, cp, "d_stereo")):
        try:
            s_l = run(r, params, key, mono=False, only_l=True)
            m_l = run(r, params, key, mono=True, only_l=True)
            m_lr = run(r, params, key, mono=True, only_l=False)
        except Exception as exc:
            print(f"  {name}  ← 渲染失败：{exc}")
            continue
        ratio = m_l / s_l if s_l > 0 else float("nan")
        verdict = ("求和" if ratio > 0.75 else
                   "平均" if ratio < 0.6 else "?? 既非 1 也非 0.5")
        print(f"  {name}  {s_l:11.4e}  {m_l:10.4e}  {ratio:9.4f}"
              f"  {m_lr:10.4e}   {verdict}")

    print("\n  判读：比值 ≈1.0 ⇒ 求和（inL+inR）；≈0.5 ⇒ 平均（(inL+inR)/2）。")
    print("        两侧判读必须一致；不一致就是 Mono 档 gain=0.4974 的成因。")


if __name__ == "__main__":
    main()
