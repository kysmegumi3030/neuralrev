"""参考 vs 候选的对拍主脚本（用户验收口径）。

口径：
  * 波形 diff < 1e-3
  * 65536 点 FFT 下每 bin 误差 ≤ 3 dB（只统计参考自身 > −80 dBpeak 的 bin）

激励统一放在 2.0 s（参考插件前 ~0.1–0.2 s 有起步淡入，见 REFERENCE §3），
并按参考的固有延迟 51 样点对齐。

用法：
    python3 tools/abtest_reverb.py                 # 默认档位 + 关键档位网格
    python3 tools/abtest_reverb.py --quick         # 只测默认档位
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugin_match import vst3_ref as V          # noqa: E402
from plugin_match.nrev_cand import NrevRenderer, report  # noqa: E402

SR = 48000
REF_LATENCY = 51
IMPULSE_AT = int(2.0 * SR)
TAIL_SEC = 4.0

# 对拍档位：默认 + 每个参数的两端与中点
POINTS = [
    ("default",        dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)),
    ("decay-min",      dict(drywet=1.0, predelay=0.5, decay=0.0, lowcut=0.0, highcut=1.0)),
    ("decay-max",      dict(drywet=1.0, predelay=0.5, decay=0.8, lowcut=0.0, highcut=1.0)),
    ("predelay-min",   dict(drywet=1.0, predelay=0.0, decay=0.5, lowcut=0.0, highcut=1.0)),
    ("predelay-max",   dict(drywet=1.0, predelay=1.0, decay=0.5, lowcut=0.0, highcut=1.0)),
    ("lowcut-max",     dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=1.0, highcut=1.0)),
    ("highcut-min",    dict(drywet=1.0, predelay=0.5, decay=0.5, lowcut=0.0, highcut=0.0)),
    ("mix-half",       dict(drywet=0.5, predelay=0.5, decay=0.5, lowcut=0.0, highcut=1.0)),
]


def impulse(n_total):
    x = np.zeros(n_total, dtype=np.float32)
    x[IMPULSE_AT] = 1.0
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    ref = V.Vst3RefRenderer(sr=SR, block=512)
    cand = NrevRenderer(sr=SR, block=512)

    n = IMPULSE_AT + int(TAIL_SEC * SR)
    x = impulse(n)

    points = POINTS[:1] if args.quick else POINTS
    passed = 0
    for name, p in points:
        # 参考：混响隔离配置 + 该档位
        yr = ref.render(x, params={f"reverb_{k}": v for k, v in p.items()})
        yr = yr.astype(np.float64)[0][IMPULSE_AT + REF_LATENCY:]
        # 候选
        yc = cand.render(x, params=p).astype(np.float64)[0][IMPULSE_AT:]

        ok, _ = report(yr, yc, label=name)
        passed += int(ok)

    print(f"\n通过 {passed}/{len(points)} 档位")


if __name__ == "__main__":
    main()
