"""插件 bundle 与离线渲染器的逐样点一致性核对。

两者编译的是同一份 src/dsp 头文件，所以在同一参数、同一块大小下应当
**逐位相同**。这一步核对的不是算法精度，而是「测的就是发布的」——
常数改完只重编渲染器、忘了重编插件，就会在这里暴露。
"""
import os, subprocess, sys
import numpy as np

ROOT = "/Users/realmac/Documents/trae_projects/neuralrev"
sys.path.insert(0, os.path.join(ROOT, "tools"))
from plugin_match.nrev_cand import NrevRenderer

HOST = os.path.join(ROOT, "tools", "vst3_host", "vst3_render")
BUNDLE = os.path.expanduser("~/Library/Audio/Plug-Ins/VST3/NeuralRev.vst3")

# 我们插件的 ParamID（--probe 读出）
PID = {"drywet": 824435163, "predelay": 856962240, "decay": 95459258,
       "lowcut": 1050619502, "highcut": 915482976, "bypass": 773352680}

SR, BLOCK = 48000, 512
N = SR * 3

def render_bundle(x, params):
    args = [HOST, "--plugin", BUNDLE, "--sr", str(SR), "--block", str(BLOCK),
            "--nch", "2"]
    for k, v in params.items():
        args += ["--param", f"{PID[k]}={v:.9g}"]
    inter = np.ascontiguousarray(np.vstack([x, x]).T.reshape(-1), dtype="<f4")
    r = subprocess.run(args, input=inter.tobytes(),
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[:600])
    return np.frombuffer(r.stdout, dtype="<f4").reshape(-1, 2).T

x = np.zeros(N, dtype=np.float32)
x[SR // 2] = 1.0

cases = [
    ("default",  dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)),
    ("decay-hi", dict(drywet=1.0, predelay=0.2, decay=0.94, lowcut=0.3, highcut=0.6)),
]

cand = NrevRenderer(sr=SR, block=BLOCK)
ok = True
for name, p in cases:
    a = render_bundle(x, p)
    b = cand.render(np.vstack([x, x]), p)
    n = min(a.shape[1], b.shape[1])
    d = float(np.max(np.abs(a[:, :n] - b[:, :n])))
    ident = np.array_equal(a[:, :n], b[:, :n])
    print(f"{name:10s} max|Δ| = {d:.3e}   逐位相同: {ident}   "
          f"peak = {float(np.max(np.abs(b))):.6f}")
    ok &= (d == 0.0)

print("\n" + ("✓ bundle 与离线渲染器逐位一致" if ok else "✗ 不一致 —— 检查是否漏编"))
