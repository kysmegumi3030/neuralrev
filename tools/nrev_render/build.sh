#!/bin/zsh
# =============================================================================
# 构建 nrev_render（本插件混响的离线渲染器）
# -----------------------------------------------------------------------------
# 编译标志与插件 Release 构建保持一致（见 cmake/JucyFlutter.cmake）：
#   -O3 -DNDEBUG -ffp-contract=fast
# 这很重要 —— 浮点收缩会影响与竞品对拍的波形/频谱，两边必须一致。
# =============================================================================
set -e
here=${0:a:h}

clang++ -std=c++17 -O3 -DNDEBUG -ffp-contract=fast -DNREV_NO_JUCE=1 \
    -Wall -Wextra -Wno-unused-parameter \
    "$here/nrev_render.cpp" \
    -o "$here/nrev_render"

echo "[build] -> $here/nrev_render"
