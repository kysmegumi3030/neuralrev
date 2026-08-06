/**
 * @file  PluginParameters.h
 * @brief 插件参数系统的「单一事实来源」
 *
 * 整个项目中所有参数的定义、ID 字符串、元数据以及 APVTS 工厂
 * 均集中在此文件（声明）和对应的 PluginParameters.cpp（实现）中。
 *
 * 使用规则
 * --------
 *  - 新增参数：在 PluginParameters.cpp 的 getAllParameterDefs() 中追加一行，
 *    并在 ParamID 命名空间中添加对应的字符串常量。
 *  - 其他 .cpp/.h 文件只需 #include "PluginParameters.h" 即可访问所有 ID 和元数据。
 *  - DSP 处理器通过 apvts.getRawParameterValue(ParamID::*) 读取参数，
 *    无需关心 ID 字符串的具体内容。
 *  - Flutter 端通过 audio_bridge/param_schema 通道接收 JSON 化后的元数据，
 *    自动渲染旋钮/开关及其范围、标签、单位。
 *
 * 数据流
 * ------
 *    PluginParameters
 *        │
 *        ├─ getAllParameterDefs()  → createParameterLayout() → APVTS
 *        │                        → buildSchemaJson()        → Flutter UI
 *        │
 *        └─ ParamID::*            → PluginProcessor (DSP 路由)
 *
 * 本文件是模板工程（JuceFlutterPlugin）的示例参数表，
 * 仅演示一个最简单的「音量增益」效果器（GAIN + BYPASS）。
 * 新建自己的插件时，直接在此追加/替换参数即可。
 */

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include <vector>

// ============================================================
// ParameterDef — 参数完整元数据
//
// 字段说明：
//   id          —— JUCE APVTS 键，同时作为 Flutter 消息通道中的参数 key
//   label       —— UI 显示标签（建议全大写）
//   unit        —— UI 显示单位字符串（"dB" / "Hz" / "%" / ""）
//   min / max   —— 参数取值范围
//   defaultVal  —— 默认值（必须在 [min, max] 内）
//   skewFactor  —— NormalisableRange skew；1.0 = 线性，<1 = 对数感知
//   step        —— 0.0f = 连续，>0 = 离散步长
//   isBoolean   —— true → AudioParameterBool；false → AudioParameterFloat
//   uiHint      —— Flutter UI 控件类型提示："knob" | "toggle" | "hidden"
// ============================================================
struct ParameterDef
{
    juce::String id;
    juce::String label;
    juce::String unit;
    float        min;
    float        max;
    float        defaultVal;
    float        skewFactor;
    float        step;
    bool         isBoolean;
    juce::String uiHint;
};

// ============================================================
// ParamID — 所有参数 ID 字符串常量（模板示例：GAIN 效果器）
//
// 用法：
//   if (parameterID == ParamID::GAIN)  { ... }
//   apvts.getRawParameterValue(ParamID::GAIN)->load();
//
// 新增参数时：
//   1. 在此处添加 extern 声明
//   2. 在 PluginParameters.cpp 的 namespace ParamID 块中添加定义
//   3. 在 getAllParameterDefs() 的表格中追加一行
// ============================================================
namespace ParamID
{
    // ---- 混响参数（范围/映射律逐条实测对标参考插件，见 docs/REFERENCE.md §2）----
    extern const juce::String DRYWET;    ///< DRY/WET   0.00 – 1.00      线性
    extern const juce::String PREDELAY;  ///< PRE-DELAY 1 – 200 ms       幂律 1+199·n^(5/3)
    extern const juce::String DECAY;     ///< DECAY     0.50 – 8.00 s    线性
    extern const juce::String LOWCUT;    ///< LOW CUT   50 – 700 Hz      线性
    extern const juce::String HIGHCUT;   ///< HIGH CUT  1000 – 10000 Hz  线性
    extern const juce::String BYPASS;    ///< 旁路开关（对应参考插件的 Reverb Active 取反）

    // ---- 延迟参数（范围/映射律逐条实测对标参考插件，见 docs/REFERENCE.md §14）----
    //
    // ⚠️ 两处「显示值不等于 DSP 值」，都是实测的，别按显示串直觉去改：
    //   * D_FEEDBACK 的显示上限 0.50，环内实际系数上限是 **0.80**（1.6 倍），
    //     且按显示的两位小数**量化**（见 DelayTuning.h kMeasFeedbackQuantSteps）。
    //   * D_LOWPASS / D_HIGHPASS 的显示 fc **就是**真 −3 dB 点（与混响段相反，
    //     §6.1 那边显示 fc 不是 −3 dB 点 —— 两段各测各的，不能类推）。
    extern const juce::String D_ACTIVE;    ///< 延迟段开关
    extern const juce::String D_DRYWET;    ///< DRY/WET     0.00 – 1.00      线性（干路恒 1，湿路 ∝ n）
    extern const juce::String D_TIMEL;     ///< TIME L      100 – 1100 ms    幂律 n^(5/3)
    extern const juce::String D_TIMER;     ///< TIME R      100 – 1100 ms    幂律 n^(5/3)
    extern const juce::String D_FEEDBACK;  ///< FEEDBACK    0.00 – 0.50 显示（环内 ×1.6，量化 0.01）
    extern const juce::String D_LOWPASS;   ///< LOW PASS    1000 – 16000 Hz  幂律 n^2.17404
    extern const juce::String D_HIGHPASS;  ///< HIGH PASS   20 – 800 Hz      幂律 n^(5/3)
    extern const juce::String D_STEREO;    ///< Stereo / Mono（true = Stereo，零交叉馈送）
    extern const juce::String D_SYNC;      ///< 时长同步到 tempo（用 D_NOTE 档位）
    extern const juce::String D_NOTE;      ///< Sync 音符档位 0–20（两端被 100–1100 ms 截断）
    extern const juce::String D_TEMPO;     ///< TEMPO       40 – 240 BPM     线性

} // namespace ParamID

// ============================================================
// getAllParameterDefs()
//
// 返回所有参数的完整元数据表。
// 静态本地变量，生命周期与进程相同，线程安全（C++11 保证）。
// ============================================================
const std::vector<ParameterDef>& getAllParameterDefs();

// ============================================================
// createParameterLayout()
//
// 遍历 getAllParameterDefs()，生成可直接传入 APVTS 构造函数的布局。
// PluginProcessor 构造函数调用此函数；调用者无需关心参数细节。
// ============================================================
juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout();
