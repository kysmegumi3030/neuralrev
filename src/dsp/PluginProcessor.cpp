#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "FlutterEmbedder.h"

#if defined(_WIN32)
#include <windows.h>
#endif

namespace {

juce::File getCurrentModuleDirectory()
{
#if defined(_WIN32)
    HMODULE moduleHandle = nullptr;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(&getCurrentModuleDirectory),
                           &moduleHandle) != 0 && moduleHandle != nullptr)
    {
        wchar_t pathBuffer[MAX_PATH] = {};
        const auto length = GetModuleFileNameW(moduleHandle, pathBuffer, MAX_PATH);
        // length >= MAX_PATH 表示缓冲区不足、路径被截断，截断路径不可用
        if (length > 0 && length < MAX_PATH)
            return juce::File(juce::String(pathBuffer)).getParentDirectory();
    }
#endif

    return juce::File::getSpecialLocation(juce::File::currentExecutableFile).getParentDirectory();
}

} // namespace

// ============================================================
// 构造函数
// ============================================================
JuceFlutterPluginProcessor::JuceFlutterPluginProcessor()
    : AudioProcessor(BusesProperties()
        .withInput ("Input",  juce::AudioChannelSet::stereo(), true)
        .withOutput("Output", juce::AudioChannelSet::stereo(), true)),
      apvts(*this, nullptr, "Parameters", createParameterLayout())
{
    // APVTS 监听器遍历 getAllParameterDefs() 自动注册。
    // 新增参数不需要修改此处。
    for (const auto& def : getAllParameterDefs())
        apvts.addParameterListener(def.id, this);

    // 初始化缓存值
    drywet_   = apvts.getRawParameterValue(ParamID::DRYWET)  ->load();
    predelay_ = apvts.getRawParameterValue(ParamID::PREDELAY)->load();
    decay_    = apvts.getRawParameterValue(ParamID::DECAY)   ->load();
    lowcut_   = apvts.getRawParameterValue(ParamID::LOWCUT)  ->load();
    highcut_  = apvts.getRawParameterValue(ParamID::HIGHCUT) ->load();
    bypassed_ = apvts.getRawParameterValue(ParamID::BYPASS)  ->load() > 0.5f;

    dActive_   = apvts.getRawParameterValue(ParamID::D_ACTIVE)  ->load() > 0.5f;
    dDryWet_   = apvts.getRawParameterValue(ParamID::D_DRYWET)  ->load();
    dTimeL_    = apvts.getRawParameterValue(ParamID::D_TIMEL)   ->load();
    dTimeR_    = apvts.getRawParameterValue(ParamID::D_TIMER)   ->load();
    dFeedback_ = apvts.getRawParameterValue(ParamID::D_FEEDBACK)->load();
    dLowpass_  = apvts.getRawParameterValue(ParamID::D_LOWPASS) ->load();
    dHighpass_ = apvts.getRawParameterValue(ParamID::D_HIGHPASS)->load();
    dStereo_   = apvts.getRawParameterValue(ParamID::D_STEREO)  ->load() > 0.5f;
    dSync_     = apvts.getRawParameterValue(ParamID::D_SYNC)    ->load() > 0.5f;
    dNote_     = apvts.getRawParameterValue(ParamID::D_NOTE)    ->load();
    dTempo_    = apvts.getRawParameterValue(ParamID::D_TEMPO)   ->load();

#if FLUTTER_ENGINE_ENABLED
    {
        auto assetsDir = resolveFlutterAssetsDir();
        flutterEmbedder = std::make_unique<FlutterEmbedder>(assetsDir);
        flutterEmbedder->setSize(800, 520);
        flutterEmbedder->initialize();
    }
#endif

}

JuceFlutterPluginProcessor::~JuceFlutterPluginProcessor()
{
    // 销毁前确保 FlutterEmbedder 已从任何窗口摘下
    if (flutterEmbedder)
        flutterEmbedder->detachFromParent();
    flutterEmbedder.reset();

    for (const auto& def : getAllParameterDefs())
        apvts.removeParameterListener(def.id, this);
}

// ============================================================
// acquireEmbedder / releaseEmbedder
// Editor 打开时获取 Embedder 指针；关闭时归还（将其从 Editor 的 Component 树摘下）
// ============================================================
FlutterEmbedder* JuceFlutterPluginProcessor::acquireEmbedder()
{
#if FLUTTER_ENGINE_ENABLED
    if (!flutterEmbedder)
    {
        auto assetsDir = resolveFlutterAssetsDir();
        flutterEmbedder = std::make_unique<FlutterEmbedder>(assetsDir);
        flutterEmbedder->setSize(800, 520);
    }

    if (flutterEmbedder && !flutterEmbedder->isEngineRunning())
    {
        flutterEmbedder->initialize();
    }
#endif

    return flutterEmbedder.get();
}

void JuceFlutterPluginProcessor::releaseEmbedder()
{
    if (flutterEmbedder)
        flutterEmbedder->detachFromParent();
}

juce::File JuceFlutterPluginProcessor::resolveFlutterAssetsDir() const
{
    const auto moduleDir = getCurrentModuleDirectory();
    const juce::File candidates[] = {
        // 通用：可执行文件同目录 / 父目录 / Resources 子目录
        moduleDir.getChildFile("flutter_assets"),
        moduleDir.getChildFile("Resources").getChildFile("flutter_assets"),
        moduleDir.getParentDirectory().getChildFile("flutter_assets"),
        moduleDir.getParentDirectory().getChildFile("Resources").getChildFile("flutter_assets"),
#if defined(__APPLE__)
        // macOS AOT: flutter_assets 内嵌在 App.framework 中
        moduleDir.getParentDirectory().getChildFile("Frameworks")
            .getChildFile("App.framework").getChildFile("Versions")
            .getChildFile("A").getChildFile("Resources")
            .getChildFile("flutter_assets"),
#elif defined(_WIN32)
        // Windows AOT (Flutter 3.22+): flutter_assets 在 data/ 子目录
        moduleDir.getChildFile("data").getChildFile("flutter_assets"),
#else
        // Linux AOT: flutter_assets 在 bundle/data/ 或 lib/ 同级
        moduleDir.getChildFile("data").getChildFile("flutter_assets"),
        moduleDir.getParentDirectory().getChildFile("lib").getChildFile("flutter_assets"),
#endif
    };

    for (const auto& candidate : candidates)
    {
        if (candidate.isDirectory())
            return candidate;
    }

    return candidates[0];
}

// ============================================================
// prepareToPlay
// ============================================================
void JuceFlutterPluginProcessor::prepareToPlay(double sampleRate, int samplesPerBlock)
{
    juce::dsp::ProcessSpec spec;
    spec.sampleRate       = sampleRate;
    spec.maximumBlockSize = static_cast<juce::uint32>(samplesPerBlock);
    spec.numChannels      = static_cast<juce::uint32>(getTotalNumOutputChannels());

    reverb_.prepare(spec);
    reverb_.setParameters(drywet_.load(), predelay_.load(), decay_.load(),
                          lowcut_.load(), highcut_.load());
    paramsDirty_.store(false, std::memory_order_relaxed);

    delay_.prepare(spec);
    applyDelayParams();
    delayDirty_.store(false, std::memory_order_relaxed);
}

// ============================================================
// applyDelayParams —— 把缓存的真实工程量喂给 DelayEffect
//
// Sync 路径单列的理由：实测 Sync 是**先按 tempo 与音符算 ms、再夹到
// [100, 1100]**（索引 0…6 全部钉在 100.500 ms，17…20 全部钉在 1100.438 ms），
// 而不是另一套映射。所以这里只需替换掉 ms，其余参数走同一条路。
// ============================================================
void JuceFlutterPluginProcessor::applyDelayParams() noexcept
{
    float msL = dTimeL_.load();
    float msR = dTimeR_.load();

    if (dSync_.load())
    {
        const int note = static_cast<int>(std::lround(dNote_.load()));
        const float ms = static_cast<float>(
            nrev::delaytuning::syncNoteMs(note, dTempo_.load()));
        msL = msR = ms;
    }

    delay_.setParameters(dDryWet_.load(), msL, msR, dFeedback_.load(),
                         dLowpass_.load(), dHighpass_.load(), dStereo_.load());
}

// ============================================================
// releaseResources
// ============================================================
void JuceFlutterPluginProcessor::releaseResources()
{
    reverb_.reset();
    delay_.reset();
}

// ============================================================
// isBusesLayoutSupported
// ============================================================
bool JuceFlutterPluginProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const
{
    if (layouts.getMainOutputChannelSet() != juce::AudioChannelSet::mono() &&
        layouts.getMainOutputChannelSet() != juce::AudioChannelSet::stereo())
        return false;

    if (layouts.getMainOutputChannelSet() != layouts.getMainInputChannelSet())
        return false;

    return true;
}

// ============================================================
// processBlock（音频处理热路径）
// ============================================================
void JuceFlutterPluginProcessor::processBlock(juce::AudioBuffer<float>& buffer,
                                               juce::MidiBuffer& /*midiMessages*/)
{
    juce::ScopedNoDenormals noDenormals;

    // 采集真实输入电平（RMS，线性值），用于 Flutter VU 表。
    const int numSamples = buffer.getNumSamples();
    const int numInputChannels = getTotalNumInputChannels();

    float leftRms = 0.0f;
    float rightRms = 0.0f;

    if (numSamples > 0 && numInputChannels > 0)
    {
        leftRms = buffer.getRMSLevel(0, 0, numSamples);
        rightRms = (numInputChannels > 1) ? buffer.getRMSLevel(1, 0, numSamples) : leftRms;
    }

    const float prevL = inputLevelLeft.load();
    const float prevR = inputLevelRight.load();
    const float rise = 0.45f;
    const float fall = 0.08f;
    const float coeffL = (leftRms > prevL) ? rise : fall;
    const float coeffR = (rightRms > prevR) ? rise : fall;

    inputLevelLeft.store(prevL + (leftRms - prevL) * coeffL);
    inputLevelRight.store(prevR + (rightRms - prevR) * coeffR);

    // 记录每帧大小（供 CONSOLE 性能条显示；bypass 时也应保持有效）
    lastBlockSize_.store(numSamples, std::memory_order_relaxed);

    // BYPASS 检查
    if (bypassed_.load()) return;

    // 清除多余输出通道
    for (int i = numInputChannels; i < getTotalNumOutputChannels(); ++i)
        buffer.clear(i, 0, numSamples);

    juce::dsp::AudioBlock<float> block(buffer);
    juce::dsp::ProcessContextReplacing<float> context(block);

    // 参数换算（滤波器系数/反馈增益）只在参数真的变过时做一次，
    // 避免每块都重算 cos/pow —— 这些是 applyParams() 里最贵的部分。
    if (paramsDirty_.exchange(false, std::memory_order_acq_rel))
        reverb_.setParameters(drywet_.load(), predelay_.load(), decay_.load(),
                              lowcut_.load(), highcut_.load());

    if (delayDirty_.exchange(false, std::memory_order_acq_rel))
        applyDelayParams();

    const auto t0 = juce::Time::getHighResolutionTicks();
    // 延迟在前、混响在后（见 PluginProcessor.h 中 delay_ 的注释）。
    // 延迟段关闭时**完全跳过**它，避免把无声的湿路饱和结果混进干路。
    if (dActive_.load())
        delay_.process(context);
    reverb_.process(context);
    const auto t1 = juce::Time::getHighResolutionTicks();

    const double ns = 1.0e9 * static_cast<double>(t1 - t0)
                    / static_cast<double>(juce::Time::getHighResolutionTicksPerSecond());

    // 一极 IIR 平滑，避免读数抖动（无锁写原子）
    const double prev = dspTimeNs_.load(std::memory_order_relaxed);
    dspTimeNs_.store(prev + 0.1 * (ns - prev), std::memory_order_relaxed);
}

// ============================================================
// 参数变化回调
// ============================================================
void JuceFlutterPluginProcessor::parameterChanged(const juce::String& parameterID,
                                                    float newValue)
{
    if      (parameterID == ParamID::DRYWET)   drywet_   = newValue;
    else if (parameterID == ParamID::PREDELAY) predelay_ = newValue;
    else if (parameterID == ParamID::DECAY)    decay_    = newValue;
    else if (parameterID == ParamID::LOWCUT)   lowcut_   = newValue;
    else if (parameterID == ParamID::HIGHCUT)  highcut_  = newValue;
    else if (parameterID == ParamID::BYPASS) { bypassed_ = (newValue > 0.5f); return; }
    else
    {
        // ---- 延迟段 ----
        // D_ACTIVE 与 BYPASS 同理：只切开关，不需要重算系数，直接返回。
        if      (parameterID == ParamID::D_ACTIVE) { dActive_ = (newValue > 0.5f); return; }
        else if (parameterID == ParamID::D_DRYWET)   dDryWet_   = newValue;
        else if (parameterID == ParamID::D_TIMEL)    dTimeL_    = newValue;
        else if (parameterID == ParamID::D_TIMER)    dTimeR_    = newValue;
        else if (parameterID == ParamID::D_FEEDBACK) dFeedback_ = newValue;
        else if (parameterID == ParamID::D_LOWPASS)  dLowpass_  = newValue;
        else if (parameterID == ParamID::D_HIGHPASS) dHighpass_ = newValue;
        else if (parameterID == ParamID::D_STEREO)   dStereo_   = (newValue > 0.5f);
        else if (parameterID == ParamID::D_SYNC)     dSync_     = (newValue > 0.5f);
        else if (parameterID == ParamID::D_NOTE)     dNote_     = newValue;
        else if (parameterID == ParamID::D_TEMPO)    dTempo_    = newValue;
        else return;

        delayDirty_.store(true, std::memory_order_release);
        return;
    }

    // 换算推迟到音频线程的块首统一做（此回调可能来自消息线程）
    paramsDirty_.store(true, std::memory_order_release);
}

// ============================================================
// 编辑器
// ============================================================
juce::AudioProcessorEditor* JuceFlutterPluginProcessor::createEditor()
{
    return new JuceFlutterPluginEditor(*this);
}

// ============================================================
// 状态序列化
// ============================================================
void JuceFlutterPluginProcessor::getStateInformation(juce::MemoryBlock& destData)
{
    auto state = apvts.copyState();
    std::unique_ptr<juce::XmlElement> xml(state.createXml());
    copyXmlToBinary(*xml, destData);
}

void JuceFlutterPluginProcessor::setStateInformation(const void* data, int sizeInBytes)
{
    std::unique_ptr<juce::XmlElement> xmlState(getXmlFromBinary(data, sizeInBytes));
    if (xmlState && xmlState->hasTagName(apvts.state.getType()))
        apvts.replaceState(juce::ValueTree::fromXml(*xmlState));
}

// ============================================================
// 插件入口（JUCE 宏）
// ============================================================
juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new JuceFlutterPluginProcessor();
}