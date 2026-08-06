cmake_minimum_required(VERSION 3.22)

# ============================================================
# NeuralRev —— JUCE 8 混响插件（Flutter UI）
# ------------------------------------------------------------
# 混响算法与 UI 对标 Tone King Imperial MKII 的 Reverb 段
# （参数范围/映射律/时频响应逐条实测对齐，见 docs/REFERENCE.md）。
#
# 骨架复用自 JucyFlutter 模板：cmake/ 与 src/ui/ 直接取自模板，
# 由 cmake/JucyFlutter.cmake 提供 juce_flutter_add_plugin() 一行建目标。
# ============================================================

project(NeuralRev VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/lib)

# 一站式 bootstrap + 高层函数（JUCE / Flutter Engine / 编译标志 / juce_flutter_add_plugin）
include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/JucyFlutter.cmake)

juce_flutter_add_plugin(${PROJECT_NAME}
    PLUGIN_CODE              "Nrv1"
    MANUFACTURER_CODE        "Nrev"
    # 厂商身份**沿用** cmake/JucyFlutter.cmake 的缺省值 AshunSoundMachines
    # （含其官网/邮箱）：用户已确认有权使用该企业签名，且本插件不对外分发。
    # 于是这里不再覆盖 COMPANY_NAME / COMPANY_WEBSITE / COMPANY_EMAIL。
    # bundle id 随之回到 com.AshunSoundMachines.NeuralRev。
    # 若将来要改回自有身份：三项都得给**非空**值（cmake 用 if(NOT ...) 判空，
    # 空字符串会照样回落到这里的缺省），且名字**不能带空格** —— JUCE 用
    # COMPANY_NAME 拼 bundle id，而 juce_flutter_add_plugin() 不转发
    # BUNDLE_ID，"NeuralRev Audio" 会得到含空格的非法 id 并触发 JUCEUtils 告警。
    PRODUCT_NAME             "NeuralRev"
    DESCRIPTION              "Spring/plate reverb with Flutter UI, matched to Tone King Imperial MKII"
    VERSION                  "1.0.0"
    # 只出 VST3：模板缺省是 VST3 Standalone AU AUv3，这里显式收窄。
    # AU/AUv3 已按要求舍弃 —— cmake/JucyFlutter.cmake 的安装规则由
    # if(TARGET ${TGT}_AU) 把门，目标不存在时 .component 的安装步骤自动消失。
    # Standalone 也一并去掉（它属同一缺省集，且要额外分发 AOT framework
    # 与 flutter_assets）；若要用它免 DAW 跑 Flutter UI，把 Standalone 加回来。
    FORMATS                  VST3
    # 设计盒是 plugin_ui.dart 的 _kDesignW/_kDesignH = 1500×470（两块踏板并排）。
    # 这里的所有尺寸都必须落在 1500:470 这条比例线上，否则 FittedBox 会加黑边。
    # 缺省取设计盒的 0.8 倍，免得在 1440p 笔记本上一开就顶满屏。
    EDITOR_WIDTH             1200
    EDITOR_HEIGHT            376
    EDITOR_MIN_WIDTH         750
    EDITOR_MIN_HEIGHT        235
    EDITOR_MAX_WIDTH         1800
    EDITOR_MAX_HEIGHT        564
    EDITOR_FIXED_ASPECT      "1500.0/470.0"
    DSP_SOURCES
        src/dsp/PluginParameters.cpp
        src/dsp/PluginParameters.h
        src/dsp/ReverbEffect.h
        src/dsp/ReverbCore.hpp
        src/dsp/ReverbTuning.h
        src/dsp/DelayEffect.h
        src/dsp/DelayCore.hpp
        src/dsp/DelayTuning.h
        src/dsp/PluginProcessor.cpp
        src/dsp/PluginProcessor.h
    EXTRA_INCLUDE_DIRS
        ${CMAKE_CURRENT_SOURCE_DIR}/src/dsp
        ${CMAKE_CURRENT_SOURCE_DIR}/src/dsp/common
)

message(STATUS "========================================")
message(STATUS "  ${PROJECT_NAME} 配置完成")
message(STATUS "  JUCE 版本    : ${JUCE_VERSION}")
message(STATUS "  Flutter SDK  : ${FLUTTER_SDK_DIR}")
message(STATUS "========================================")
