"""候选 DSP 的 Renderer 包装：与 vst2_ref.Vst2Renderer 接口一致。

参考与候选共用同一个接口后，拟合与验收脚本对两者的驱动方式完全相同，
不会引入「测法不同造成的伪差异」。

候选进程 tools/pwa1_render/pwa1_render 直接编译自 src/dsp/PWA1Dsp.hpp，
因此这里拟合出的常量对插件构建一定成立（不存在 Python 复刻版漂移问题）。
"""
import json
import os
import subprocess

import numpy as np

from .render import Renderer, _as_stereo

DEFAULT_EXE = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "pwa1_render", "pwa1_render"))

#: 面板刻度的默认工作点（与 PluginParameters.cpp 的 default 一致，
#: 也与参考实现出厂默认对应：feedback 打满、resonance 为 0、sagging 2.5）
CAND_DEFAULTS = {
    "input_level": 1.0,
    "volume": 5.0,
    "depth": 5.0,
    "presence": 5.0,
    "bias": 5.0,
    "tubes": 1,
    "feedback": 10.0,
    "resonance": 0.0,
    "sagging": 2.5,
    "output_level": 1.0,
    "oversampling": 1,
    "mode": 0,
    "bypass": 0,
}


class CandidateRenderer(Renderer):
    """把候选 DSP 当 Renderer 用。``consts`` 覆盖 PWA1Constants.h 的字段。"""

    def __init__(self, sr=48000, defaults=None, exe=DEFAULT_EXE, consts=None):
        super().__init__(sr, defaults if defaults is not None else CAND_DEFAULTS)
        self.exe = exe
        self.consts = dict(consts or {})

    def param_names(self):
        return list(CAND_DEFAULTS)

    def const_names(self):
        out = subprocess.run([self.exe, "--dump-consts"], stdout=subprocess.PIPE, check=True)
        return json.loads(out.stdout.decode())

    def render(self, x, params=None, consts=None):
        p = self._merge(params)
        unknown = [key for key in p if key not in CAND_DEFAULTS]
        if unknown:
            raise KeyError(f"unknown candidate params: {unknown}")
        xs = _as_stereo(x)
        nch = xs.shape[0]
        args = [self.exe, "--sr", str(self.sr), "--nch", str(nch)]
        for key, v in p.items():
            args += ["--param", f"{key}={float(v):.9g}"]
        allc = {**self.consts, **(consts or {})}
        for key, v in allc.items():
            args += ["--const", f"{key}={float(v):.17g}"]
        inter = xs.T.reshape(-1).astype("<f4")
        r = subprocess.run(args, input=inter.tobytes(), stdout=subprocess.PIPE, check=True)
        y = np.frombuffer(r.stdout, dtype="<f4").reshape(-1, nch).T
        return np.ascontiguousarray(y, dtype=np.float32)
