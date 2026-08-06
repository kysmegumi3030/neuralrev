#!/bin/zsh
# =============================================================================
# 构建 vst3_render（参考插件原生宿主）
# -----------------------------------------------------------------------------
# 用 JUCE 自带的 VST3 SDK 头文件 + 必需的少数 SDK 源文件（funknown / coreiids /
# conststringtable）。arm64 原生编译：参考插件是 universal (x86_64 + arm64)，
# 原生 arm64 加载最快且 PACE 正常放行。
# =============================================================================
set -e
here=${0:a:h}

# VST3 SDK：优先用本仓库 build 树里的 JUCE，其次找同级项目已拉取的 JUCE
for cand in \
    "$here/../../build/_deps/juce-src" \
    "$here/../../../coding/JucyPWA1/build/_deps/juce-src" \
    "$HOME/Documents/coding/JucyPWA1/build/_deps/juce-src"
do
    if [[ -d "$cand/modules/juce_audio_processors/format_types/VST3_SDK" ]]; then
        SDK="$cand/modules/juce_audio_processors/format_types/VST3_SDK"
        break
    fi
done

if [[ -z "$SDK" ]]; then
    echo "[build] 找不到 VST3 SDK（先构建一次主工程以拉取 JUCE，或调整本脚本的搜索路径）" >&2
    exit 1
fi

echo "[build] VST3 SDK: $SDK"

clang++ -std=c++17 -O2 -arch arm64 \
    -I"$SDK" \
    -DRELEASE=1 \
    -Wno-deprecated-declarations -Wno-unused-parameter \
    "$here/vst3_render.cpp" \
    "$SDK/pluginterfaces/base/funknown.cpp" \
    "$SDK/pluginterfaces/base/coreiids.cpp" \
    "$SDK/pluginterfaces/base/conststringtable.cpp" \
    -framework CoreFoundation \
    -o "$here/vst3_render"

echo "[build] -> $here/vst3_render"
