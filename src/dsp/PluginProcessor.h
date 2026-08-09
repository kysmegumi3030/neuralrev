#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_dsp/juce_dsp.h>
#include "PluginParameters.h"  // ParameterDef, ParamID::*, getAllParameterDefs()
#include "ReverbEffect.h"
#include "DelayEffect.h"

// 前向声明，避免把 FlutterEmbedder 的实现细节暴露到头文件
class FlutterEmbedder;

/**
 * @brief 音频插件处理器
 *
 * 负责所有音频信号处理逻辑：混响（ReverbEffect）+ BYPASS。
 * UI 由 Flutter 提供。
 *
 * 混响的结构、参数范围与各常数均对标 Tone King Imperial MKII 的 Reverb 段，
 * 逐条实测依据见 docs/REFERENCE.md，常数落点见 src/dsp/ReverbTuning.h。
 */
class JuceFlutterPluginProcessor : public juce::AudioProcessor,
                                   public juce::AudioProcessorValueTreeState::Listener
{
public:
    // --------------------------------------------------------
    // 构造 / 析构
    // --------------------------------------------------------
    JuceFlutterPluginProcessor();
    ~JuceFlutterPluginProcessor() override;

    // --------------------------------------------------------
    // AudioProcessor 接口
    // --------------------------------------------------------
    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;

    bool isBusesLayoutSupported(const BusesLayout& layouts) const override;

    void processBlock(juce::AudioBuffer<float>& buffer,
                      juce::MidiBuffer& midiMessages) override;

    // --------------------------------------------------------
    // 编辑器
    // --------------------------------------------------------
    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    // --------------------------------------------------------
    // 元信息
    // --------------------------------------------------------
    const juce::String getName() const override { return JucePlugin_Name; }

    bool   acceptsMidi()  const override { return false; }
    bool   producesMidi() const override { return false; }
    bool   isMidiEffect() const override { return false; }
    /// 混响尾巴：DECAY 最大档实测 T60 极长（接近无限延音），报 12 s 够用。
    /// 延迟段更长：1100 ms 的圈长、每圈 0.80（−1.94 dB）⇒ 衰到 −60 dB 要
    /// 60/1.94 ≈ 31 圈 ≈ 34 s。取两者较大者。
    double getTailLengthSeconds() const override { return 34.0; }

    // --------------------------------------------------------
    // 预设
    // --------------------------------------------------------
    int  getNumPrograms()                             override { return 1; }
    int  getCurrentProgram()                          override { return 0; }
    void setCurrentProgram(int)                       override {}
    const juce::String getProgramName(int)            override { return {}; }
    void changeProgramName(int, const juce::String&)  override {}

    // --------------------------------------------------------
    // 状态序列化
    // --------------------------------------------------------
    void getStateInformation(juce::MemoryBlock& destData) override;
    void setStateInformation(const void* data, int sizeInBytes) override;

    // --------------------------------------------------------
    // APVTS 参数监听
    // --------------------------------------------------------
    void parameterChanged(const juce::String& parameterID,
                          float newValue) override;

    // --------------------------------------------------------
    // 参数状态树
    // --------------------------------------------------------
    juce::AudioProcessorValueTreeState& getAPVTS() { return apvts; }

    // --------------------------------------------------------
    // 电平表数据（线性 0.0 ~ 1.0），供 AudioParameterBridge（共享 UI 桥接层）
    // 以固定接口名 getMeterLevelLeft/Right 读取推送到 Flutter VU 表。
    // 本插件展示输入电平；其他插件可按需展示输出电平或其他自定义含义。
    // --------------------------------------------------------
    float getMeterLevelLeft() const noexcept  { return inputLevelLeft.load(); }
    float getMeterLevelRight() const noexcept { return inputLevelRight.load(); }

    // --------------------------------------------------------
    // 性能读数：供 AudioParameterBridge 通过 perf_update 通道推送到 Flutter
    // CONSOLE 页顶部统计条。DSP 耗时为每 block 计时后一极 IIR 平滑值（纳秒）。
    // 其他插件复用本桥接层时，实现同名接口即可获得同样的性能显示。
    // --------------------------------------------------------
    double getDspTimeNs()     const noexcept { return dspTimeNs_.load(); }
    int    getLastBlockSize() const noexcept { return lastBlockSize_.load(); }

    // --------------------------------------------------------
    // Flutter Engine 嵌入器（Processor 生命期内持有）
    // 构造阶段预热；Editor 打开时 acquireEmbedder() 直接复用。
    // --------------------------------------------------------
    FlutterEmbedder* acquireEmbedder();
    void releaseEmbedder();

private:
    /// 把缓存的延迟参数（真实工程量）喂给 DelayEffect，含 Sync 路径的取代。
    void applyDelayParams() noexcept;

    // Flutter 嵌入器
    std::unique_ptr<FlutterEmbedder> flutterEmbedder;
    juce::File resolveFlutterAssetsDir() const;

    // DSP 对象
    //
    // 串联顺序 **延迟 → 混响**：参考插件里两段是独立的效果块，
    // 而实测隔离两段时用的就是「只开一段」的做法，因此串联顺序无法从
    // 隔离测量里读出来。取延迟在前的理由是它更早出现在参考插件的信号链
    // 显示顺序上；若日后测出相反，只需交换这两行的 process 调用。
    nrev::ReverbEffect reverb_;
    nrev::DelayEffect  delay_;

    // 参数状态树
    juce::AudioProcessorValueTreeState apvts;

    // 缓存参数（原子内存序，供音频线程读取）。存**真实工程量**，
    // 与 PluginParameters.cpp 的参数表一致；ReverbEffect 内部再换算成归一值。
    std::atomic<float> drywet_   { 0.50f };
    std::atomic<float> predelay_ { 63.68f };   // ms
    std::atomic<float> decay_    { 4.25f };    // s
    std::atomic<float> lowcut_   { 50.0f };    // Hz
    std::atomic<float> highcut_  { 10000.0f }; // Hz
    std::atomic<bool>  bypassed_ { false };
    std::atomic<bool>  paramsDirty_ { true };

    // 延迟段的缓存参数（同样存**真实工程量**）。
    // dFeedback_ 存的是**显示值** 0.00–0.50；环内 ×1.6 与两位小数量化
    // 都在 DelayEffect 内部按实测律完成（见 DelayTuning.h feedbackFromNorm）。
    std::atomic<bool>  dActive_   { false };
    std::atomic<float> dDryWet_   { 0.50f };
    std::atomic<float> dTimeL_    { 500.0f };    // ms
    std::atomic<float> dTimeR_    { 500.0f };    // ms
    std::atomic<float> dFeedback_ { 0.25f };     // 显示值 0.00–0.50
    std::atomic<float> dLowpass_  { 16000.0f };  // Hz
    std::atomic<float> dHighpass_ { 20.0f };     // Hz
    std::atomic<bool>  dStereo_   { true };
    std::atomic<bool>  dSync_     { false };
    std::atomic<float> dNote_     { 13.0f };     // 档位索引 0–20（L；Mono Sync 时左右共用）
    std::atomic<float> dNoteR_    { 13.0f };     // 档位索引 0–20（R；仅 Stereo Sync 时生效）
    std::atomic<float> dTempo_    { 120.0f };    // BPM
    std::atomic<bool>  delayDirty_ { true };

    // VU 电平
    std::atomic<float> inputLevelLeft  { 0.0f };
    std::atomic<float> inputLevelRight { 0.0f };

    // 性能读数（音频线程无锁写，消息线程读）
    std::atomic<double> dspTimeNs_     { 0.0 };  // 平滑后的每 block DSP 耗时（ns）
    std::atomic<int>    lastBlockSize_ { 0 };    // 最近一次 processBlock 的 numSamples

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(JuceFlutterPluginProcessor)
};