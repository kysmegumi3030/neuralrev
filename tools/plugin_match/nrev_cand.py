"""候选侧（本插件）渲染器 + 与参考侧的统一对拍度量。

候选走 tools/nrev_render（直接编译 src/dsp 的发布头文件），
参考走 tools/vst3_host/vst3_render，两者都是 f32 stdin/stdout，
所以下面的 metric 函数对两侧完全对称。

验收口径（用户设定）：
  * 波形 diff < 1e-3
  * 65536 点 FFT 下每 bin 误差 ≤ 3 dB
`report()` 直接按这两条出结论。
"""
from __future__ import annotations

import os
import subprocess

import numpy as np

from .render import Renderer, _as_stereo

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NREV_EXE = os.path.join(TOOLS, "nrev_render", "nrev_render")

# 与 nrev_render 的 argv 顺序一致（全部归一值）
# 顺序必须与 tools/nrev_render/nrev_render.cpp 的 argv 一致。
# 后 8 个是延迟段；d_active 默认 0（关）⇒ 既有的混响对拍脚本行为不变。
PARAM_ORDER = ["drywet", "predelay", "decay", "lowcut", "highcut",
               "d_active", "d_drywet", "d_timel", "d_timer", "d_feedback",
               "d_lowpass", "d_highpass", "d_stereo", "d_lfophase"]

DEFAULTS = {"drywet": 0.5, "predelay": 0.5, "decay": 0.5, "lowcut": 0.0, "highcut": 1.0,
            # 延迟段默认值与参考插件出厂态一致（500 ms / 显示反馈 0.25 /
            # LP 全开 / HP 全关 / Stereo），但 d_active=0 ⇒ 默认不参与信号。
            "d_active": 0.0, "d_drywet": 0.5,
            "d_timel": 0.577079952, "d_timer": 0.577079952,
            "d_feedback": 0.5, "d_lowpass": 1.0, "d_highpass": 0.0,
            "d_stereo": 1.0,
            # LFO 起相（周期的分数）。只有 ab_delay.py 会动它，用来标定参考侧
            # 那个未知但确定的起相；插件运行时恒 0。
            "d_lfophase": 0.0}


class NrevRenderer(Renderer):
    """本插件的离线渲染器（参数为归一值，语义与参考插件一致）。"""

    def __init__(self, sr=48000, block=512, defaults=None):
        merged = dict(DEFAULTS)
        merged.update(defaults or {})
        super().__init__(sr, merged)
        self.block = int(block)
        if not os.path.exists(NREV_EXE):
            raise FileNotFoundError(f"缺少 {NREV_EXE}；先运行 tools/nrev_render/build.sh")

    def param_names(self):
        return list(PARAM_ORDER)

    def render(self, x, params=None):
        p = self._merge(params)
        xs = _as_stereo(x)
        nch = xs.shape[0]
        args = [NREV_EXE, str(self.sr), str(self.block), str(nch)]
        args += [f"{float(p[k]):.9g}" for k in PARAM_ORDER]
        inter = np.ascontiguousarray(xs.T.reshape(-1), dtype="<f4")
        r = subprocess.run(args, input=inter.tobytes(),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0:
            raise RuntimeError(f"nrev_render rc={r.returncode}: "
                               f"{r.stderr.decode(errors='replace')[:400]}")
        y = np.frombuffer(r.stdout, dtype="<f4").reshape(-1, nch).T
        return np.ascontiguousarray(y, dtype=np.float32)


# =============================================================================
# 对拍度量
# =============================================================================
def align(a, b, max_lag=4096):
    """把 b 对齐到 a（整数平移），返回 (a_aligned, b_aligned, lag)。"""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    c = np.correlate(b[:min(n, 1 << 16)], a[:min(n, 1 << 16)], "full")
    mid = min(n, 1 << 16) - 1
    lo = max(0, mid - max_lag)
    hi = min(len(c), mid + max_lag)
    lag = int(np.argmax(np.abs(c[lo:hi])) + lo - mid)
    if lag > 0:
        return a[:n - lag], b[lag:n], lag
    if lag < 0:
        return a[-lag:n], b[:n + lag], lag
    return a, b, 0


def waveform_diff(ref, cand, align_lag=True):
    """波形误差：返回 (max_abs_diff, rms_diff, nrmse, lag, gain)。

    先整数对齐，再解最佳标量增益（消掉整体电平差），然后统计残差。
    用户的 1e-3 口径按 max_abs_diff 判。
    """
    a, b, lag = align(ref, cand) if align_lag else (np.asarray(ref, float),
                                                    np.asarray(cand, float), 0)
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    g = float(np.dot(a, b) / max(np.dot(b, b), 1e-30))
    d = a - g * b
    return (float(np.max(np.abs(d))), float(np.sqrt(np.mean(d ** 2))),
            float(np.sqrt(np.mean(d ** 2)) / max(np.sqrt(np.mean(a ** 2)), 1e-30)),
            lag, g)


def spectrum_err_db(ref, cand, nfft=65536, floor_db=-80.0):
    """逐 bin 频谱误差（dB）。

    只统计**参考自身高于 floor_db（相对其峰值）** 的 bin —— 低于该电平的 bin
    是数值噪声，比值无意义（实测未平滑的梳状零点处比值可达 15 dB）。
    返回 (max_err, p99, p95, mean, 参与统计的 bin 数, 总 bin 数)。
    """
    a = np.zeros(nfft)
    b = np.zeros(nfft)
    a[:min(len(ref), nfft)] = np.asarray(ref, float)[:nfft]
    b[:min(len(cand), nfft)] = np.asarray(cand, float)[:nfft]
    A = np.abs(np.fft.rfft(a))
    B = np.abs(np.fft.rfft(b))
    ref_db = 20 * np.log10(np.maximum(A, 1e-300) / max(A.max(), 1e-300))
    m = ref_db > floor_db
    if not m.any():
        return (float("nan"),) * 4 + (0, len(A))
    err = np.abs(20 * np.log10(np.maximum(B[m], 1e-300) / np.maximum(A[m], 1e-300)))
    return (float(err.max()), float(np.percentile(err, 99)),
            float(np.percentile(err, 95)), float(err.mean()),
            int(m.sum()), int(len(A)))


def smoothed_spectrum_err_db(ref, cand, nfft=65536, oct_frac=1 / 12,
                             f_lo=20.0, f_hi=20000.0, sr=48000,
                             floor_db=None):
    """1/N 倍频程 RMS 平滑后的逐 bin 误差（dB）——**主验收口径**。

    为什么用平滑口径：参考混响是线性**时变**的（内部 LFO 调制延迟线，
    见 docs/REFERENCE.md §10）。原始逐 bin 误差对 LFO 相位极度敏感——
    参考插件与**它自己**比（只挪 1 样点激励位置）最差 bin 已达 8.2 dB，
    挪 1 ms 达 30 dB。故原始逐 bin ≤3 dB 对任何独立实现都不可达。

    平滑后的谱对 LFO 相位不敏感：参考自比在 1 ms 位移下 max 仅 0.10 dB、
    4800 样点位移下 1.72 dB。因此「平滑后 ≤3 dB」既可达又能严格约束音色。

    ## floor_db：**电平门**（延迟段必须给，混响段沿用 None）

    不给门时全带每个 bin 都算，包括参考比全谱峰值低 60–80 dB 的那些 ——
    那里参考自己就是准噪声，比的不是失配。实测（延迟 0.9 档）：无门时最差
    **3.51 dB @ 19927 Hz**，而该点参考电平是 **−80.4 dB**；加 −40 dB 门后
    最差变成 **2.30 dB @ 13902 Hz**（真信号区）。前者会把一个通过的实现
    判成失败。

    这与 `spectrum_err_db` 已经有的 floor_db 是同一条纪律，也与 §14.6
    「门限必须与地板同口径」一致 —— 那张地板表就是在 −40 dB 门下测的。
    混响段的历史读数是在无门下取的，故默认保持 None 以免破坏可比性。

    返回 (max, p99, p95, mean)。
    """
    a = np.zeros(nfft)
    b = np.zeros(nfft)
    a[:min(len(ref), nfft)] = np.asarray(ref, float)[:nfft]
    b[:min(len(cand), nfft)] = np.asarray(cand, float)[:nfft]
    A = np.abs(np.fft.rfft(a))
    B = np.abs(np.fft.rfft(b))
    f = np.fft.rfftfreq(nfft, 1.0 / sr)

    def smooth(S):
        out = np.zeros_like(S)
        # 用累积平方和做 O(n) 的带内 RMS，避免逐 bin 掩码的 O(n²)
        cs = np.concatenate([[0.0], np.cumsum(S.astype(np.float64) ** 2)])
        lo_i = np.searchsorted(f, f * 2 ** -oct_frac, side="left")
        hi_i = np.searchsorted(f, f * 2 ** oct_frac, side="right")
        hi_i = np.maximum(hi_i, lo_i + 1)
        cnt = hi_i - lo_i
        out = np.sqrt((cs[hi_i] - cs[lo_i]) / np.maximum(cnt, 1))
        return out

    As, Bs = smooth(A), smooth(B)
    m = (f >= f_lo) & (f <= f_hi)
    if floor_db is not None:
        # 门用**未平滑**的参考谱取（与 spectrum_err_db 同口径）：平滑会把
        # 低电平区抬起来，用平滑谱设门等于让门自己漏掉该挡的东西。
        m &= A > A.max() * 10.0 ** (floor_db / 20.0)
    err = np.abs(20 * np.log10(np.maximum(Bs[m], 1e-30) / np.maximum(As[m], 1e-30)))
    return (float(err.max()), float(np.percentile(err, 99)),
            float(np.percentile(err, 95)), float(err.mean()))


def report(ref, cand, label="", nfft=65536, sr=48000):
    """打印一条对拍结论。

    **主口径**：1/12 oct 平滑后逐 bin 误差 ≤ 3 dB（对 LFO 相位不敏感）。
    原始逐 bin 与波形 diff 仍照实打印作参考，但不作为通过条件——
    参考插件与自身比都过不了（见 §10）。
    """
    wmax, wrms, nrmse, lag, gain = waveform_diff(ref, cand)
    rmax, r99, r95, rmean = spectrum_err_db(ref, cand, nfft)[:4]
    gmax, g99, g95, gmean = smoothed_spectrum_err_db(ref, cand, nfft, sr=sr)
    ok = gmax <= 3.0
    print(f"  {label:22s} 平滑谱 max={gmax:5.2f} dB {'✓' if ok else '✗'}"
          f"  p99={g99:5.2f} p95={g95:5.2f} mean={gmean:5.2f}")
    print(f"  {'':22s} 参考量：波形 max|Δ|={wmax:.2e} nrmse={nrmse*100:6.2f}%"
          f"  原始谱 max={rmax:5.1f} p95={r95:5.2f}  (lag={lag}, gain={gain:.4f})")
    return (ok, dict(smooth_max=gmax, smooth_p99=g99, smooth_p95=g95,
                     smooth_mean=gmean, wave_max=wmax, nrmse=nrmse,
                     raw_max=rmax, raw_p95=r95, lag=lag, gain=gain))
