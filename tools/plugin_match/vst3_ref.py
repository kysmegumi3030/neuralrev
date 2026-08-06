"""参考插件（Tone King Imperial MKII，VST3）渲染器 —— 走原生 vst3_render 宿主。

为什么不用 pedalboard：参考插件被 PACE/iLok（__Pace_Eden）包裹，在 Python 进程里
加载会被 SIGKILL（exit 137，无异常、无 crash log）。同一 bundle 在原生可执行文件里
dlopen 完全正常，故参考侧统一走 tools/vst3_host/vst3_render（f32 stdin/stdout 协议，
与 plugin_match.OfflineRenderer 同构）。

本模块覆盖**混响段**与**延迟段**两套参数：

- REVERB_PARAMS / ISOLATE_REVERB —— 关掉其余所有段（pedal/amp/EQ/cab/chorus/delay），
  使插件退化成「纯混响 + 干信号」；
- DELAY_PARAMS / ISOLATE_DELAY —— 同理，只留 FX 段里的 Delay，**混响与 chorus 关掉**。

两套隔离用的是同一张 SECTION_PARAMS 开关表，只差 delay_active / reverb_active 谁开。
"""
from __future__ import annotations

import os
import subprocess

import numpy as np

from .render import Renderer, _as_stereo

REF_VST3 = "/Library/Audio/Plug-Ins/VST3/Tone King Imperial MKII.vst3"

HOST_EXE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vst3_host", "vst3_render"
)

# ---- 混响段参数（VST3 ParamID，probe 得到）----
REVERB_PARAMS = {
    "reverb_active":   992817016,   # step, <=0.5 Inactive / >0.5 Active
    "reverb_drywet":   389880618,   # 0..1        显示 0.00..1.00
    "reverb_decay":    1004671240,  # 0..1        显示 0.50..8.00（线性，s）
    "reverb_predelay": 950313106,   # 0..1        显示 1.00..200.00 ms（非线性）
    "reverb_lowcut":   389874988,   # 0..1        显示 50..700 Hz（线性）
    "reverb_highcut":  389878832,   # 0..1        显示 1000..10000 Hz（线性）
}

# ---- 延迟段参数（VST3 ParamID，probe 得到；index 74..87）----
#
# 14 个参数，其中 5 个是开关/枚举、7 个连续、2 个是音符档位（stepCount=20）。
# 显示串的映射律见本文件下方的 delay_* 函数与 docs/REFERENCE.md §14。
DELAY_PARAMS = {
    "delay_active":     1759950441,  # step,   Inactive / Active
    "delay_note_ms":    2143206555,  # step,   Note / ms —— **它选择 DSP 路径，不只是显示**
    "delay_tempo":      490014552,   # 0..1    显示 40.0..240.0 BPM（线性）
    "delay_drywet":     816144857,   # 0..1    显示 0.00..1.00（线性）
    "delay_sync":       1678364350,  # step,   Off / On（默认 On）
    "delay_syncnote_l": 144643708,   # step 20, 1/64T .. 1/1D（21 档）
    "delay_syncnote_r": 144643714,   # step 20, 同上
    "delay_time_l":     719224438,   # 0..1    显示 100.00..1100.00 ms（幂律 5/3）
    "delay_time_r":     719224444,   # 0..1    同上
    "delay_feedback":   559732264,   # 0..1    显示 0.00..0.50（线性）
    "delay_mode":       1068390302,  # step,   Mono / Stereo（默认 Stereo）
    "delay_lowpass":    816143071,   # 0..1    显示 1.00..16.0 kHz（幂律 ~2.174）
    "delay_highpass":   816139227,   # 0..1    显示 20.00..800 Hz（幂律 5/3）
    "delay_tap":        234471387,   # step,   Tap 1 / Tap 2（tap tempo，离线渲染无意义）
}

# ---- 其余段的开关（用于隔离混响 / 延迟）----
SECTION_PARAMS = {
    "pedal_section":   1929779953,
    "wah_active":      977147780,
    "comp_active":     373379829,
    "od1_active":      1768110541,
    "od2_active":      508130574,
    "amp_section":     1534839573,
    "eq_section":      1408664831,
    "eq_active":       1242253010,
    "cab_section":     1811077351,
    "fx_section":      507051134,
    "chorus_active":   450336844,
    "delay_active":    1759950441,
    "gate_amount":     1425402848,
    "input_gain":      1706566249,
    "output_gain":     873230368,
    "global_bypass":   773352680,
}

# 只留混响：其余段全关；input/output gain 居中（=0 dB）；gate 拉到最低（不动信号）
ISOLATE_REVERB = {
    SECTION_PARAMS["pedal_section"]: 0.0,
    SECTION_PARAMS["wah_active"]:    0.0,
    SECTION_PARAMS["comp_active"]:   0.0,
    SECTION_PARAMS["od1_active"]:    0.0,
    SECTION_PARAMS["od2_active"]:    0.0,
    SECTION_PARAMS["amp_section"]:   0.0,
    SECTION_PARAMS["eq_section"]:    0.0,
    SECTION_PARAMS["eq_active"]:     0.0,
    SECTION_PARAMS["cab_section"]:   0.0,
    SECTION_PARAMS["fx_section"]:    1.0,   # FX 段必须开，混响在其中
    SECTION_PARAMS["chorus_active"]: 0.0,
    SECTION_PARAMS["delay_active"]:  0.0,
    SECTION_PARAMS["gate_amount"]:   0.0,   # -80 dB 阈值 = 实际不门限
    SECTION_PARAMS["input_gain"]:    0.5,   # 0 dB
    SECTION_PARAMS["output_gain"]:   0.5,   # 0 dB
    SECTION_PARAMS["global_bypass"]: 0.0,
    REVERB_PARAMS["reverb_active"]:  1.0,
}

# 混响参数默认（归一）——与插件出厂默认一致
REVERB_DEFAULTS = {
    "reverb_drywet":   0.5,
    "reverb_decay":    0.5,
    "reverb_predelay": 0.5,
    "reverb_lowcut":   0.0,
    "reverb_highcut":  1.0,
}

# 只留延迟：与 ISOLATE_REVERB 唯一的差别是 delay/reverb 两个开关互换。
#
# **必须设 delay_note_ms = 1.0（"ms"）** —— 这是实测出来的、与参数名字直觉相反的一条：
# 该参数的标题是 "Delay Note/ms Display"，probe 的 units 为空，看名字像是纯 UI 开关。
# 实测它**选择 DSP 路径**：note_ms=0（"Note"）时 Delay Time L/R **完全无效**
# （六个 norm 全部落在 24021 样点），延迟时长由 Sync Note 档位 × tempo 决定；
# 只有 note_ms=1 时 Delay Time L/R 才生效。2×2×2 开关网格（sync / note_ms / tap）
# 里「ms 路径通」的四格全部满足 note_ms=1，与 sync 和 tap 都无关。
#
# 与之配套的一条修正：**Delay Sync 的极性与显示串相反**。显示 0.00→"Off"、
# 1.00→"On"，但实测 sync=0.0 时 tempo 影响延迟时长（52821→12020 样点），
# sync=1.0 时 tempo 无效 —— 即 0.0 才是「跟随 tempo」。这是 §6.1「显示值不等于
# DSP 行为」在开关参数上的又一次实例。note_ms=1 之后两者都不再影响 ms 路径，
# 所以本表把 sync 留在 0.0 只是为了可复现，不再承担「关掉 sync」的作用。
ISOLATE_DELAY = {
    SECTION_PARAMS["pedal_section"]: 0.0,
    SECTION_PARAMS["wah_active"]:    0.0,
    SECTION_PARAMS["comp_active"]:   0.0,
    SECTION_PARAMS["od1_active"]:    0.0,
    SECTION_PARAMS["od2_active"]:    0.0,
    SECTION_PARAMS["amp_section"]:   0.0,
    SECTION_PARAMS["eq_section"]:    0.0,
    SECTION_PARAMS["eq_active"]:     0.0,
    SECTION_PARAMS["cab_section"]:   0.0,
    SECTION_PARAMS["fx_section"]:    1.0,   # FX 段必须开，延迟在其中
    SECTION_PARAMS["chorus_active"]: 0.0,
    REVERB_PARAMS["reverb_active"]:  0.0,   # 混响关掉
    SECTION_PARAMS["gate_amount"]:   0.0,
    SECTION_PARAMS["input_gain"]:    0.5,
    SECTION_PARAMS["output_gain"]:   0.5,
    SECTION_PARAMS["global_bypass"]: 0.0,
    DELAY_PARAMS["delay_active"]:    1.0,
    DELAY_PARAMS["delay_note_ms"]:   1.0,   # 见上：ms 路径的**唯一**开关，必须 1.0
    DELAY_PARAMS["delay_sync"]:      0.0,   # 见上：留 0.0 只为可复现
}

# 延迟参数默认（归一）——与插件出厂默认一致，但 sync 改成 Off（理由见 ISOLATE_DELAY）
DELAY_DEFAULTS = {
    "delay_drywet":   0.5,
    "delay_time_l":   0.577079952,   # 出厂默认，显示 500.00 ms
    "delay_time_r":   0.577079952,
    "delay_feedback": 0.5,           # 显示 0.25
    "delay_lowpass":  1.0,           # 显示 16.0 kHz
    "delay_highpass": 0.0,           # 显示 20.00 Hz
    "delay_mode":     1.0,           # Stereo
}


# =============================================================================
# 参数真实值 ↔ 归一值（由 --sweep 实测的显示串拟合，见 docs/REFERENCE.md）
# =============================================================================
def decay_seconds(norm: float) -> float:
    """DECAY：0.50 s .. 8.00 s，线性。"""
    return 0.5 + 7.5 * float(norm)


def decay_norm(seconds: float) -> float:
    return (float(seconds) - 0.5) / 7.5


def lowcut_hz(norm: float) -> float:
    """LOW CUT：50 Hz .. 700 Hz，线性。"""
    return 50.0 + 650.0 * float(norm)


def highcut_hz(norm: float) -> float:
    """HIGH CUT：1000 Hz .. 10000 Hz，线性。"""
    return 1000.0 + 9000.0 * float(norm)


PREDELAY_EXPONENT = 5.0 / 3.0
"""PRE-DELAY 的幂律指数，**精确 5/3**（不是拟合近似）。

由 21 点显示串反解：对每个采样点求 log((ms-1)/199)/log(norm)，
21 点全部给出 1.66666±2e-4（例：norm=0.1 → 1.66574，norm=0.5 → 1.66666，
norm=0.9 → 1.66669）。反代回 1+199·norm^(5/3) 与插件显示串在全部 21 点上
吻合到 ±0.01 ms（显示串本身的两位小数精度），故判定为精确 5/3。
"""


def predelay_ms(norm: float) -> float:
    """PRE-DELAY：1 ms .. 200 ms，幂律指数 5/3。"""
    return 1.0 + 199.0 * float(norm) ** PREDELAY_EXPONENT


def predelay_norm(ms: float) -> float:
    return ((float(ms) - 1.0) / 199.0) ** (1.0 / PREDELAY_EXPONENT)


# -----------------------------------------------------------------------------
# 延迟段的映射律
#
# 全部由 --sweep 的 **101 点**显示串反解（混响那轮只用了 21 点；这里加密到 101，
# 因为 LOW PASS 的指数不是好看的分数，21 点撑不住三位有效数字的判定）。
#
# 判据不是「最小二乘残差小」，而是**每一点都落在显示串自己的量化格内**：
# 对 101 点逐点检查 `|模型 − 显示| < 显示量化步长`。三条律全部 0 违反
# （详见 docs/REFERENCE.md §14.1）。这比报一个 RMS 更强 —— 它排除了
# 「平均很准但某档偏一格」这种会在听感上暴露的情形。
#
# 注意显示串的舍入方式**不是**四舍五入也不是纯截断（两种假设各有 14…48 点
# 违反，但取并集后 0 违反），所以不要用「显示串能否逐字复现」当判据 ——
# 那会把一条正确的律判成错的。
# -----------------------------------------------------------------------------
DELAY_TIME_EXPONENT = 5.0 / 3.0
"""Delay Time L/R 的幂律指数，**精确 5/3** —— 与混响 PRE-DELAY 同一个 skew。

101 点逐点反解 log((ms−100)/1000)/log(n) 全部落在 1.6663…1.6686；
固定端点 100/1100 后取 5/3，最差偏差 0.957 个显示量化格（< 1）⇒ 判定精确。
两个通道（L=719224438 / R=719224444）共用这条律。
"""

DELAY_HIGHPASS_EXPONENT = 5.0 / 3.0
"""Delay High Pass 的幂律指数，同样是 **5/3**（20 → 800 Hz）。

最差 1.993 个「四舍五入格」但 0 个「截断格」，取两种舍入的并集后 0 违反。
自由三参拟合给 lo=19.998 / hi=799.27 / p=1.66623 —— 端点与整数值差 <0.1%，
指数与 5/3 差 3e-4 ⇒ 判定为精确 5/3 而非巧合。
"""

DELAY_LOWPASS_EXPONENT = 2.174040
"""Delay Low Pass 的幂律指数（1 → 16 kHz）。**这一条不是好看的分数。**

固定端点 1000/16000 后在 [2.10, 2.25] 上以 1e-5 步长扫描，最优 2.17404，
101 点最差 0.959 个显示格。自由三参拟合独立给出 lo=999.17 / hi=15988.8 /
p=2.17263（端点偏差 <0.1%），两条路径一致。

它等价于 JUCE `NormalisableRange` 的 **skew = 0.46**（1/0.46 = 2.173913，
101 点最差 0.992 格，同样通过）—— 即厂商很可能就是在 UI 上填了 0.46 这个
整数化的 skew。两者在 20 kHz 尺度上最大差 3 Hz，实现上取哪个都在显示精度内；
本项目取实测的 2.174040（不假设厂商用的是 JUCE）。
"""


def delay_time_ms(norm: float) -> float:
    """Delay Time L/R：100 ms .. 1100 ms，幂律指数 5/3。"""
    return 100.0 + 1000.0 * float(norm) ** DELAY_TIME_EXPONENT


def delay_time_norm(ms: float) -> float:
    return ((float(ms) - 100.0) / 1000.0) ** (1.0 / DELAY_TIME_EXPONENT)


def delay_lowpass_hz(norm: float) -> float:
    """Delay Low Pass：1 kHz .. 16 kHz，幂律指数 2.17404。"""
    return 1000.0 + 15000.0 * float(norm) ** DELAY_LOWPASS_EXPONENT


def delay_lowpass_norm(hz: float) -> float:
    return ((float(hz) - 1000.0) / 15000.0) ** (1.0 / DELAY_LOWPASS_EXPONENT)


def delay_highpass_hz(norm: float) -> float:
    """Delay High Pass：20 Hz .. 800 Hz，幂律指数 5/3。"""
    return 20.0 + 780.0 * float(norm) ** DELAY_HIGHPASS_EXPONENT


def delay_highpass_norm(hz: float) -> float:
    return ((float(hz) - 20.0) / 780.0) ** (1.0 / DELAY_HIGHPASS_EXPONENT)


def delay_feedback(norm: float) -> float:
    """Delay Feedback：0.00 .. 0.50，线性。

    **上限是 0.5，不是 1.0** —— 这是厂商刻意留的稳定性余量。显示串两位小数
    在 21 点上最大偏差 0.005（= 半个显示格）⇒ 线性成立。
    注意这个 0.5 是**显示值**，环内实际反馈系数是否等于它，要由 IR 实测判定，
    不能照搬（混响 §6.1 的前车之鉴：显示的 fc 不是 −3 dB 点）。
    """
    return 0.5 * float(norm)


def delay_tempo_bpm(norm: float) -> float:
    """Delay Tempo：40 .. 240 BPM，线性（sync=Off 时不参与 DSP）。"""
    return 40.0 + 200.0 * float(norm)


DELAY_SYNC_NOTES = [
    "1/64T", "1/64", "1/32T", "1/64D", "1/32", "1/16T", "1/32D", "1/16",
    "1/8T", "1/16D", "1/8", "1/4T", "1/8D", "1/4", "1/2T", "1/4D", "1/2",
    "1/1T", "1/2D", "1/1", "1/1D",
]
"""Delay Sync Note L/R 的 21 档（stepCount=20），按归一值 0, 0.05, …, 1.0 排列。

顺序是按**实际时长**单调递增的（T=三连音 ×2/3、D=附点 ×3/2），
所以档位之间不是等比：1/64T → 1/64 是 ×1.5，1/64 → 1/32T 是 ×1.333。
出厂默认 0.65 = "1/4"。
"""


class Vst3RefRenderer(Renderer):
    """通过原生宿主渲染参考插件。

    params 用**名字**（REVERB_PARAMS 的键）或**裸 ParamID**（int）都可以；
    名字会被翻成 id。每次 render 都是一个全新进程 → 天然干净的状态，
    不需要 reset()。
    """

    def __init__(self, sr=48000, block=512, defaults=None, plugin=REF_VST3, isolate=True,
                 section="reverb"):
        if section not in ("reverb", "delay"):
            raise ValueError(f"section 只能是 'reverb' 或 'delay'，收到 {section!r}")
        self.section = section
        merged = dict(REVERB_DEFAULTS if section == "reverb" else DELAY_DEFAULTS)
        merged.update(defaults or {})
        super().__init__(sr, merged)
        self.plugin = plugin
        self.block = int(block)
        self.isolate = bool(isolate)
        if not os.path.exists(HOST_EXE):
            raise FileNotFoundError(
                f"缺少原生宿主 {HOST_EXE}；先运行 tools/vst3_host/build.sh"
            )

    def param_names(self):
        return list(REVERB_PARAMS if self.section == "reverb" else DELAY_PARAMS)

    def _resolve(self, params):
        """名字/裸 id 混合的参数表 → {ParamID: norm}，并叠加隔离设置。"""
        if self.isolate:
            out = dict(ISOLATE_REVERB if self.section == "reverb" else ISOLATE_DELAY)
        else:
            out = {}
        for k, v in self._merge(params).items():
            if isinstance(k, str):
                pid = REVERB_PARAMS.get(k, DELAY_PARAMS.get(k, SECTION_PARAMS.get(k, k)))
            else:
                pid = k
            if not isinstance(pid, int):
                raise KeyError(f"未知参数名: {k!r}")
            out[pid] = float(v)
        return out

    def render(self, x, params=None, tail=0):
        xs = _as_stereo(x)
        nch = xs.shape[0]
        args = [HOST_EXE, "--plugin", self.plugin,
                "--sr", str(self.sr), "--block", str(self.block), "--nch", str(nch)]
        if tail:
            args += ["--tail", str(int(tail))]
        for pid, v in sorted(self._resolve(params).items()):
            args += ["--param", f"{pid}={v:.9g}"]

        inter = np.ascontiguousarray(xs.T.reshape(-1), dtype="<f4")
        r = subprocess.run(args, input=inter.tobytes(), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE)
        if r.returncode != 0:
            raise RuntimeError(
                f"vst3_render rc={r.returncode}: {r.stderr.decode(errors='replace')[:500]}"
            )
        y = np.frombuffer(r.stdout, dtype="<f4").reshape(-1, nch).T
        return np.ascontiguousarray(y, dtype=np.float32)


def probe_json(plugin=REF_VST3):
    """参考插件全部参数元数据（dict）。"""
    import json
    r = subprocess.run([HOST_EXE, "--plugin", plugin, "--probe"],
                       stdout=subprocess.PIPE, check=True)
    return json.loads(r.stdout.decode())


def sweep(pid, steps=21, plugin=REF_VST3):
    """扫描某参数的显示串，返回 [(norm, plain, display), ...]。"""
    import json
    r = subprocess.run([HOST_EXE, "--plugin", plugin, "--sweep", str(int(pid)),
                        "--steps", str(int(steps))], stdout=subprocess.PIPE, check=True)
    d = json.loads(r.stdout.decode())
    return [(p["norm"], p["plain"], p["display"]) for p in d["points"]]
