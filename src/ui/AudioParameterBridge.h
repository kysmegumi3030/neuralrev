#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "FlutterEmbedder.h"
#include <atomic>
#include <string>
#include <string_view>
#include <vector>

/**
 * @brief JUCE AudioProcessorValueTreeState ↔ Flutter 参数桥接器
 *
 * 负责：
 *  1. 将 JUCE 参数变化推送给 Flutter UI（通过方法通道）
 *  2. 将 Flutter UI 的控件操作同步回 JUCE 参数树
 */
class AudioParameterBridge : private juce::AudioProcessorValueTreeState::Listener
                           , private juce::Timer
{
public:
    // 基础方法通道名（由 embedder.namespacedChannel() 生成 v2 实例隔离名）
    static constexpr const char* CHANNEL_PARAM_UPDATE  = "audio_bridge/param_update";
    static constexpr const char* CHANNEL_PARAM_SET     = "audio_bridge/param_set";
    static constexpr const char* CHANNEL_METER_UPDATE  = "audio_bridge/meter_update";
    static constexpr const char* CHANNEL_PERF_UPDATE   = "audio_bridge/perf_update";
    static constexpr const char* CHANNEL_PARAM_SCHEMA  = "audio_bridge/param_schema";
    static constexpr const char* CHANNEL_REQUEST_SYNC  = "audio_bridge/request_sync";

    // --------------------------------------------------------
    // 构造函数
    // --------------------------------------------------------
    AudioParameterBridge(juce::AudioProcessorValueTreeState& apvtsRef,
                         FlutterEmbedder& embedderRef);

    ~AudioParameterBridge() override;

    // --------------------------------------------------------
    // 初始化：注册所有参数监听与方法通道
    // --------------------------------------------------------
    void initialize();

    // --------------------------------------------------------
    // 推送所有参数的当前值给 Flutter（初始化同步）
    // --------------------------------------------------------
    void pushAllParameters();

    // --------------------------------------------------------
    // 一次性同步：推送 schema + 所有参数值到 Flutter。
    // 可在 Engine attach 后由 PluginEditor 调用（处理重新打开场景），
    // 也可在 request_sync 通道 handler 中调用（处理首次启动场景）。
    // --------------------------------------------------------
    void syncAllToFlutter();

private:
    // --------------------------------------------------------
    // APVTS 参数监听
    // --------------------------------------------------------
    // 注意：JUCE 契约允许 parameterChanged 在任意线程被调用（宿主写自动化时
    // 会落在音频线程）。而 FlutterEmbedder::sendMessage → FlutterDesktopMessengerSend
    // 只能在消息线程调用且非线程安全。因此此处 **绝不能直接发消息**：
    // 只做无锁、无堆分配的“置脏”，真正的推送统一由消息线程上的 timerCallback 完成。
    void parameterChanged(const juce::String& parameterID, float newValue) override;
    void timerCallback() override;

    // 消息线程内：扫描所有参数，把自上次以来发生变化的值推送给 Flutter。
    void flushChangedParameters();

    // --------------------------------------------------------
    // 构造 JSON 工具方法
    // --------------------------------------------------------
    static std::string buildParamJson(const std::string& paramId, float value);
    static std::string buildMeterJson(float left, float right);
    static std::string buildSchemaJson();
    // json / key 均为视图，零堆分配
    static float extractFloatFromJson(std::string_view json, std::string_view key);

    void sendBridgeMessage(std::string_view baseChannel,
                           std::string_view method,
                           std::string_view argsJson);
    void registerBridgeMethodHandler(std::string_view baseChannel,
                                     FlutterEmbedder::MethodCallback callback);
    void unregisterBridgeMethodHandler(std::string_view baseChannel);

    // --------------------------------------------------------
    // 成员
    // --------------------------------------------------------
    juce::AudioProcessorValueTreeState& apvts;
    FlutterEmbedder& embedder;

    // ---- 参数回传去抖 / 跨线程解耦 ----
    // parameterChanged 只置该标志（音频线程安全）；timerCallback（消息线程）观察到后
    // 扫描 trackedParams_，把与 lastSentValues_ 不同的值推送出去，并合并高频变化。
    struct TrackedParam
    {
        juce::RangedAudioParameter* param = nullptr; // 生命周期由 APVTS 持有，长于本桥
        std::string                 id;              // 预缓存的参数 ID，避免热路径构造
    };
    std::vector<TrackedParam> trackedParams_;
    std::vector<float>        lastSentValues_;       // 仅 timerCallback（消息线程）读写
    std::atomic<bool>         paramsDirty_ { false };

    // 记录当前各参数值为“已同步”基线（在全量 push 后调用，避免重复增量推送）。
    void primeLastSentValues();

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(AudioParameterBridge)
};