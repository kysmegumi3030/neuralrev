#include "PluginParameters.h"

// ============================================================
// ParamID — 字符串常量定义
//
// 新增参数：
//   在此块中添加一行 const juce::String，
//   同时在 PluginParameters.h 的 namespace ParamID 中添加 extern 声明，
//   并在下方 getAllParameterDefs() 的表格中追加元数据行。
// ============================================================
namespace ParamID
{
    const juce::String DRYWET   = "drywet";
    const juce::String PREDELAY = "predelay";
    const juce::String DECAY    = "decay";
    const juce::String LOWCUT   = "lowcut";
    const juce::String HIGHCUT  = "highcut";
    const juce::String BYPASS   = "bypass";

    const juce::String D_ACTIVE   = "d_active";
    const juce::String D_DRYWET   = "d_drywet";
    const juce::String D_TIMEL    = "d_timel";
    const juce::String D_TIMER    = "d_timer";
    const juce::String D_FEEDBACK = "d_feedback";
    const juce::String D_LOWPASS  = "d_lowpass";
    const juce::String D_HIGHPASS = "d_highpass";
    const juce::String D_STEREO   = "d_stereo";
    const juce::String D_SYNC     = "d_sync";
    const juce::String D_NOTE     = "d_note";
    const juce::String D_NOTER    = "d_noter";
    const juce::String D_TEMPO    = "d_tempo";

} // namespace ParamID

// ============================================================
// getAllParameterDefs() — 全量参数元数据表
//
// 每一行对应一个插件参数，字段含义见 PluginParameters.h 中 ParameterDef 的注释。
//
// ★ 新增参数只需在此处追加一行 ★
//   JUCE APVTS、状态序列化、Flutter schema、参数监听注册
//   全部自动生效，无需修改任何其他文件。
//
// 列顺序：id  label  unit  min  max  default  skew  step  bool  uiHint
// ============================================================
// ------------------------------------------------------------
// PRE-DELAY 的 skew
// ------------------------------------------------------------
// 参考插件的 PRE-DELAY 律实测为  ms = 1 + 199·n^(5/3)（n = 归一值，指数精确 5/3，
// 见 docs/REFERENCE.md §2）。JUCE 的 NormalisableRange 在给定 skew 时用
//     value = start + (end − start) · proportion^(1/skew)
// 故要得到指数 5/3，需 1/skew = 5/3 → skew = 3/5 = 0.6。
static constexpr float kPredelaySkew = 0.6f;

// ------------------------------------------------------------
// 延迟段的 skew
// ------------------------------------------------------------
// 同一个换算关系：JUCE 用 value = start + (end−start)·proportion^(1/skew)，
// 而实测的映射律是 value = min + (max−min)·n^E，故 skew = 1/E。
//
//   TIME L/R  E = 5/3      → skew = 0.6      （与 PRE-DELAY 同一个 UI 习惯）
//   HIGH PASS E = 5/3      → skew = 0.6
//   LOW PASS  E = 2.174040 → skew = 0.460       ← 这一条不是整分数，是拟合值
//
// 实测依据见 docs/REFERENCE.md §14 与 tools/measure/ref_delay_params.py：
// 21 个采样点上与显示串对齐到显示精度。
static constexpr float kDelayTimeSkew     = 0.6f;
static constexpr float kDelayHighpassSkew = 0.6f;
static constexpr float kDelayLowpassSkew  = 1.0f / 2.174040f;

const std::vector<ParameterDef>& getAllParameterDefs()
{
    static const std::vector<ParameterDef> defs =
    {
        // 范围与映射律逐条对标参考插件实测值（docs/REFERENCE.md §2）：
        // id                 label         unit    min       max        default   skew            step   bool    uiHint
        { ParamID::DRYWET,   "DRY/WET",    "",       0.0f,     1.0f,     0.50f,   1.0f,           0.0f,  false, "knob"   },
        { ParamID::PREDELAY, "PRE-DELAY",  "ms",     1.0f,   200.0f,    63.68f,   kPredelaySkew,  0.0f,  false, "knob"   },
        { ParamID::DECAY,    "DECAY",      "s",      0.5f,     8.0f,     4.25f,   1.0f,           0.0f,  false, "knob"   },
        { ParamID::LOWCUT,   "LOW CUT",    "Hz",    50.0f,   700.0f,    50.0f,    1.0f,           0.0f,  false, "knob"   },
        { ParamID::HIGHCUT,  "HIGH CUT",   "Hz",  1000.0f, 10000.0f, 10000.0f,    1.0f,           0.0f,  false, "knob"   },
        { ParamID::BYPASS,   "BYPASS",     "",       0.0f,     1.0f,     0.0f,    1.0f,           1.0f,  true,  "toggle" },

        // ---- 延迟段（docs/REFERENCE.md §14）----
        // 默认值取参考插件的出厂状态：500 ms / 显示反馈 0.25 / LP 全开 / HP 全关 / Stereo。
        // FEEDBACK 的 step 是 **0.01** —— 这不是 UI 便利，是实测的 DSP 量化
        // （同格内系数相同到 0.0002%，跨格按格号之比跳）。步长必须与 DSP 一致，
        // 否则 UI 能停在 DSP 到不了的值上，A/B 就会出现无法解释的偏差。
        // id                    label          unit    min       max       default    skew                 step   bool    uiHint
        { ParamID::D_ACTIVE,   "DELAY",       "",       0.0f,     1.0f,     0.0f,     1.0f,                1.0f,  true,  "toggle" },
        { ParamID::D_DRYWET,   "D DRY/WET",   "",       0.0f,     1.0f,     0.50f,    1.0f,                0.0f,  false, "knob"   },
        { ParamID::D_TIMEL,    "TIME L",      "ms",   100.0f,  1100.0f,   500.0f,     kDelayTimeSkew,      0.0f,  false, "knob"   },
        { ParamID::D_TIMER,    "TIME R",      "ms",   100.0f,  1100.0f,   500.0f,     kDelayTimeSkew,      0.0f,  false, "knob"   },
        { ParamID::D_FEEDBACK, "FEEDBACK",    "",       0.0f,     0.5f,     0.25f,    1.0f,                0.01f, false, "knob"   },
        { ParamID::D_LOWPASS,  "LOW PASS",    "Hz",  1000.0f, 16000.0f, 16000.0f,     kDelayLowpassSkew,   0.0f,  false, "knob"   },
        { ParamID::D_HIGHPASS, "HIGH PASS",   "Hz",    20.0f,   800.0f,    20.0f,     kDelayHighpassSkew,  0.0f,  false, "knob"   },
        { ParamID::D_STEREO,   "STEREO",      "",       0.0f,     1.0f,     1.0f,     1.0f,                1.0f,  true,  "toggle" },
        { ParamID::D_SYNC,     "SYNC",        "",       0.0f,     1.0f,     0.0f,     1.0f,                1.0f,  true,  "toggle" },
        { ParamID::D_NOTE,     "NOTE",        "",       0.0f,    20.0f,    13.0f,     1.0f,                1.0f,  false, "knob"   },
        { ParamID::D_NOTER,    "NOTE R",      "",       0.0f,    20.0f,    13.0f,     1.0f,                1.0f,  false, "knob"   },
        { ParamID::D_TEMPO,    "TEMPO",       "BPM",   40.0f,   240.0f,   120.0f,     1.0f,                0.0f,  false, "knob"   },
    };
    return defs;
}

// ============================================================
// createParameterLayout() — 自动从元数据表生成 APVTS 布局
//
// 遍历 getAllParameterDefs()：
//   isBoolean == true  → AudioParameterBool
//   isBoolean == false → AudioParameterFloat（带对数 skew 和步长）
// ============================================================
juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout()
{
    std::vector<std::unique_ptr<juce::RangedAudioParameter>> params;
    params.reserve(getAllParameterDefs().size());

    for (const auto& def : getAllParameterDefs())
    {
        if (def.isBoolean)
        {
            params.push_back(std::make_unique<juce::AudioParameterBool>(
                juce::ParameterID { def.id, 1 },
                def.label,
                def.defaultVal > 0.5f
            ));
        }
        else
        {
            params.push_back(std::make_unique<juce::AudioParameterFloat>(
                juce::ParameterID { def.id, 1 },
                def.label,
                juce::NormalisableRange<float>(def.min, def.max, def.step, def.skewFactor),
                def.defaultVal,
                juce::AudioParameterFloatAttributes().withLabel(def.unit)
            ));
        }
    }

    return { params.begin(), params.end() };
}
