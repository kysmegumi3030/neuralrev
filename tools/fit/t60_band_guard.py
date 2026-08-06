"""逐带 T60 的**尾长稳定**测量口径。所有涉及 T60 的新脚本都从这里 import。

## 为什么单独抽一个模块

`fit_damping_t60.band_t60` 的口径是对的，但它有一个**隐式前提**：渲染尾长
必须足够长。同一个函数、同一个信号，尾长 6 s 与 8 s 在 125 Hz 上分别读出
3.39 s 与 35.6 s（差 950%），而 4 kHz / 8 kHz 两带在两种尾长下**逐位相同**。

成因：−5…−35 dB 的回归窗要求包络在窗内真的跌够 30 dB。长 T60 的低频带在
短尾里只跌了十几 dB，回归段就滑到尾部那段近乎水平的截断平台上，斜率趋 0、
T60 趋无穷。这不是 band_t60 的 bug，是**用法**的前提没被检查。

教训 6 说「新脚本的度量必须与既有口径逐字一致」；这里补上第二条：
**口径一致还不够，还要证明读数与渲染窗长无关。**

## 判据

同一档位渲染两个尾长（长的比短的多 ~25%），只有两次读数相对差 <`TOL`
的带才算有效。漂移的带一律丢弃，不参与任何拟合或结论 —— 宁可 n 变小，
也不让一个滑到平台上的读数进目标函数。

用法：
    from t60_band_guard import measure_guarded, ref_guarded, BANDS
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "fit"))

from plugin_match import vst3_ref as V                              # noqa: E402
from plugin_match.nrev_cand import NrevRenderer                     # noqa: E402
# 逐带口径一律复用既有实现，不自造（见 t60-measurement-caliber 的教训）
import fit_damping_t60 as F                                          # noqa: E402

SR = 48000
REF_LATENCY = 51
BASE_AT = int(2.0 * SR)

# 与 fit_damping_t60.BANDS 逐字相同：125 Hz–8 kHz。
# 不扩到 31/63 Hz —— 固定 20 ms RMS 窗在 31 Hz 不足一个周期，那两带的
# T60 读数本身无意义（低频结论只能走能量占比，见 diag_t60_caliber.py）。
BANDS = F.BANDS

TOL = 0.05          # 两个尾长的读数相对差上限
GROW = 1.28         # 长尾 / 短尾

# 各档 T60 先验（REFERENCE §7.1），只用来定尾长。
T60_PRIOR = {0.20: 1.371, 0.50: 2.469, 0.70: 4.476, 0.86: 10.258, 0.94: 24.817}

# 短尾 = 先验 T60 的这个倍数。−35 dB 需要 0.583·T60；取 2.4 倍留足余量，
# 让低频带（T60 可达先验的 1.4 倍）也能跌够 30 dB。
TAIL_MULT = 2.4


def tails_for(norm: float) -> tuple[float, float]:
    """(短尾, 长尾) 秒数。上限 48 s：再长渲染时间不可接受，且 0.94 档已够。"""
    base = float(np.clip(TAIL_MULT * T60_PRIOR[norm], 6.0, 40.0))
    return base, min(base * GROW, 52.0)


def _params(norm: float) -> dict:
    return dict(drywet=1.0, predelay=0.5, decay=norm, lowcut=0.0, highcut=1.0)


def render_ref(r, norm: float, tail: float) -> np.ndarray:
    n = BASE_AT + int(tail * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    p = _params(norm)
    y = r.render(x, params={f"reverb_{k}": v for k, v in p.items()})
    return y.astype(np.float64)[0][BASE_AT + REF_LATENCY:]


def render_cand(norm: float, tail: float) -> np.ndarray:
    n = BASE_AT + int(tail * SR)
    x = np.zeros(n, dtype=np.float32)
    x[BASE_AT] = 1.0
    c = NrevRenderer(sr=SR, block=512)
    return c.render(x, params=_params(norm)).astype(np.float64)[0][BASE_AT:]


def _guard(y_short: np.ndarray, y_long: np.ndarray) -> dict:
    """逐带测两次，返回 {带名: T60}，只含尾长稳定的带。"""
    out: dict[str, float] = {}
    for nm, lo, hi in BANDS:
        a = F.band_t60(F.bandpass(y_short, lo, hi))
        b = F.band_t60(F.bandpass(y_long, lo, hi))
        if not (a == a and b == b) or a <= 0 or b <= 0:
            continue
        if abs(b / a - 1.0) > TOL:
            continue                      # 漂移 ⇒ 弃
        out[nm] = float(0.5 * (a + b))
    return out


def ref_guarded(r, norm: float) -> dict:
    ts, tl = tails_for(norm)
    return _guard(render_ref(r, norm, ts), render_ref(r, norm, tl))


def measure_guarded(norm: float) -> dict:
    ts, tl = tails_for(norm)
    return _guard(render_cand(norm, ts), render_cand(norm, tl))


def rel_errors(ref: dict, cand: dict) -> dict:
    """{带名: 相对误差%}，只含两侧都有效的带。"""
    return {nm: 100.0 * (cand[nm] / ref[nm] - 1.0)
            for nm in ref if nm in cand and ref[nm] > 0}
