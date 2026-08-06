#include "AudioParameterBridge.h"
#include <juce_core/juce_core.h>
#include "PluginProcessor.h"
#include <cstdio>   // snprintf
#include <cstdlib>  // strtof
#include <cstring>  // memcmp
#include <limits>   // quiet_NaN

// ============================================================
// 性能读数（perf_update 通道）开关
// ------------------------------------------------------------
// 默认开启：向 Flutter CONSOLE 性能条推送 DSP 耗时 / 每帧大小 / CPU 负载，
// 需宿主 Processor 实现 getDspTimeNs() 与 getLastBlockSize()。
// 未实现这两个接口的工程可在 CMake 传 NO_PERF_READOUT（等价定义本宏为 0）关闭，
// 从而无需改动各自 Processor 代码。
// ============================================================
#ifndef JUCE_FLUTTER_ENABLE_PERF_READOUT
#define JUCE_FLUTTER_ENABLE_PERF_READOUT 1
#endif

// ============================================================
// 构造函数
// ============================================================
AudioParameterBridge::AudioParameterBridge(
    juce::AudioProcessorValueTreeState& apvtsRef,
    FlutterEmbedder& embedderRef)
    : apvts(apvtsRef), embedder(embedderRef)
{
}

AudioParameterBridge::~AudioParameterBridge()
{
    stopTimer();

    // 必须先注销通道，防止 Flutter 在 Editor 关闭后
    // 仍能触发持有悬垂 this 的旧 lambda（UAF-2/4 修复）
    unregisterBridgeMethodHandler(CHANNEL_PARAM_SET);
    unregisterBridgeMethodHandler(CHANNEL_REQUEST_SYNC);

    // 移除所有参数监听
    for (auto* param : apvts.processor.getParameters())
    {
        if (auto* rangedParam = dynamic_cast<juce::RangedAudioParameter*>(param))
            apvts.removeParameterListener(rangedParam->getParameterID(), this);
    }
}

// ============================================================
// initialize
// ============================================================
void AudioParameterBridge::initialize()
{
    // 预建参数跟踪表（一次性分配，热路径与 timer 均不再分配）。
    trackedParams_.clear();
    for (auto* param : apvts.processor.getParameters())
    {
        if (auto* rangedParam = dynamic_cast<juce::RangedAudioParameter*>(param))
            trackedParams_.push_back(
                { rangedParam, rangedParam->getParameterID().toStdString() });
    }
    lastSentValues_.assign(trackedParams_.size(),
                           std::numeric_limits<float>::quiet_NaN());

    // 注册所有参数监听
    for (const auto& tp : trackedParams_)
        apvts.addParameterListener(juce::String(tp.id), this);

    // 注册来自 Flutter 的参数设置消息处理器
    registerBridgeMethodHandler(
        CHANNEL_PARAM_SET,
        [this](std::string_view /*channel*/,
               std::string_view method,
               std::string_view argsJson) -> std::string
        {
            // 期望 JSON 格式: {"method":"setParam","args":{"id":"gain","value":0.5}}
            std::string_view paramId;
            auto idPos = argsJson.find("\"id\":\"");
            if (idPos != std::string_view::npos)
            {
                idPos += 6;
                auto idEnd = argsJson.find('"', idPos);
                if (idEnd != std::string_view::npos)
                    paramId = argsJson.substr(idPos, idEnd - idPos);
            }

            const float value = extractFloatFromJson(argsJson, "value");

            if (!paramId.empty())
            {
                // callAsync 跨线程：必须将 paramId 拷贝为拥有字符串，
                // 因为 string_view 指向的消息缓冲区在回调返回后可能已失效。
                // 捕获 apvts 引用而非 this：apvts 属于 PluginProcessor，
                // 生命周期长于 Bridge 和 Editor，不会悬垂（UAF-2 修复）
                juce::MessageManager::callAsync(
                    [&localApvts = this->apvts, paramIdStr = std::string(paramId), value]()
                    {
                        const auto id = juce::String::fromUTF8(
                            paramIdStr.c_str(), (int) paramIdStr.size());
                        if (auto* param = localApvts.getParameter(id))
                            param->setValueNotifyingHost(
                                param->convertTo0to1(value));
                    });
            }

            juce::ignoreUnused(method);
            return "{\"status\":\"ok\"}";
        }
    );

    // 注册来自 Flutter 的 requestSync 消息处理器
    // Dart 端在所有 handler 注册完毕（首帧渲染后）发送此消息，
    // C++ 响应：推送 schema + 所有参数当前值，保证首次打开时 UI 与 DSP 同步。
    registerBridgeMethodHandler(
        CHANNEL_REQUEST_SYNC,
        [this](std::string_view /*channel*/,
               std::string_view /*method*/,
               std::string_view /*argsJson*/) -> std::string
        {
            syncAllToFlutter();
            return "{\"status\":\"ok\"}";
        }
    );

    // 以 30Hz 推送输入电平到 Flutter VU 表。
    startTimerHz(30);

    DBG("[AudioParameterBridge] Initialized, listening "
        + juce::String(apvts.processor.getParameters().size())
        + " parameters");
}

// ============================================================
// timerCallback —— 推送输入电平（VU）
// ============================================================
void AudioParameterBridge::timerCallback()
{
    auto* processor = dynamic_cast<JuceFlutterPluginProcessor*>(&apvts.processor);
    if (processor == nullptr)
        return;

    const float rawL = processor->getMeterLevelLeft();
    const float rawR = processor->getMeterLevelRight();

    // 转换为 dBFS 归一化值发送给 Flutter VU 表
    //   -60 dBFS → 0.0（静音底线）  /  0 dBFS → 1.0（满幅）
    // 这样线性 0.1（约 -20 dBFS）映射为 ~0.67，段式表能正常显示。
    static constexpr float kFloorDb = -60.0f;
    auto toNorm = [](float rms) -> float {
        if (rms < 1e-7f) return 0.0f;
        const float db = 20.0f * std::log10(rms);
        return juce::jlimit(0.0f, 1.0f, (db - kFloorDb) / (-kFloorDb));
    };

    char buf[64];
    const int n = std::snprintf(buf, sizeof(buf),
        "{\"left\":%.6g,\"right\":%.6g}", toNorm(rawL), toNorm(rawR));
    sendBridgeMessage(CHANNEL_METER_UPDATE,
                      "meterChanged",
                      std::string_view(buf, static_cast<size_t>(n)));

    // 性能读数：复用同一 tick 推送 DSP 耗时 / 每帧大小 / CPU 负载。
    // CPU 负载 = DSP 耗时 / 该 block 时长（block/sampleRate），在 C++ 侧算好。
    // 需 Processor 实现 getDspTimeNs()/getLastBlockSize()；未实现的工程用
    // NO_PERF_READOUT 关闭本块（见文件头 JUCE_FLUTTER_ENABLE_PERF_READOUT）。
#if JUCE_FLUTTER_ENABLE_PERF_READOUT
    {
        const double dspNs = processor->getDspTimeNs();
        const int    blk   = processor->getLastBlockSize();
        const double sr    = processor->getSampleRate();
        const double blockNs = (sr > 0.0 && blk > 0) ? (blk / sr * 1.0e9) : 0.0;
        const float  load  = (blockNs > 0.0)
                           ? static_cast<float>(dspNs / blockNs * 100.0) : 0.0f;

        char pbuf[96];
        const int pn = std::snprintf(pbuf, sizeof(pbuf),
            "{\"dspNs\":%.0f,\"block\":%d,\"load\":%.1f}", dspNs, blk, load);
        if (pn > 0 && pn < static_cast<int>(sizeof(pbuf)))
            sendBridgeMessage(CHANNEL_PERF_UPDATE,
                              "perfChanged",
                              std::string_view(pbuf, static_cast<size_t>(pn)));
    }
#endif

    // 参数回传：消息线程上统一 drain（去抖 + 线程安全）。
    // exchange 消费脏标志；若在扫描后又有新变化，会再次置脏，下一 tick 补发，不丢更新。
    if (paramsDirty_.exchange(false, std::memory_order_acquire))
        flushChangedParameters();
}

// ============================================================
// syncAllToFlutter —— 推送 schema + 全部参数值（供首次打开和重开时调用）
// ============================================================
void AudioParameterBridge::syncAllToFlutter()
{
    // 先推送 schema（Flutter 需知道参数范围后再渲染旋钮）
    sendBridgeMessage(CHANNEL_PARAM_SCHEMA, "paramSchema", buildSchemaJson());
    // 再推送当前参数值
    pushAllParameters();
}

// ============================================================
// pushAllParameters
// ============================================================
void AudioParameterBridge::pushAllParameters()
{
    for (auto* param : apvts.processor.getParameters())
    {
        if (auto* rangedParam = dynamic_cast<juce::RangedAudioParameter*>(param))
        {
            float value = rangedParam->convertFrom0to1(rangedParam->getValue());
            std::string json = buildParamJson(
                rangedParam->getParameterID().toStdString(), value);
            sendBridgeMessage(CHANNEL_PARAM_UPDATE, "paramChanged", json);
        }
    }

    // 刚全量推送过，登记为基线，避免 timer 紧接着把相同值再增量推送一遍。
    // pushAllParameters 只在消息线程被调用（syncAllToFlutter / request_sync handler），
    // 与 flushChangedParameters 同线程，读写 lastSentValues_ 无竞争。
    primeLastSentValues();
}

// ============================================================
// parameterChanged —— 任意线程置脏，推送延迟到消息线程的 timerCallback
// ============================================================
void AudioParameterBridge::parameterChanged(const juce::String& /*parameterID*/,
                                             float /*newValue*/)
{
    // ⚠ 本回调可能在任意线程被调用（宿主写自动化 → 音频线程）。
    // FlutterDesktopMessengerSend 只能在消息线程调用且非线程安全，
    // 因此这里绝不能直接发消息——只做一次无锁、无堆分配的置脏。
    // 真正的推送由消息线程上的 timerCallback → flushChangedParameters 完成，
    // 顺带把高频变化合并（去抖），避免消息洪峰。
    //
    // APVTS 会在调用本监听器之前就已把参数底层原子值更新完毕，故 timer 端
    // 事后读取到的一定是不早于本次变化的值——不会丢更新。
    paramsDirty_.store(true, std::memory_order_release);
}

// ============================================================
// flushChangedParameters —— 仅消息线程调用（来自 timerCallback）。
// 扫描全部参数，把与上次已推送值不同的推送给 Flutter。
// ============================================================
void AudioParameterBridge::flushChangedParameters()
{
    for (size_t i = 0; i < trackedParams_.size(); ++i)
    {
        auto* rangedParam = trackedParams_[i].param;
        const float value = rangedParam->convertFrom0to1(rangedParam->getValue());

        // 位级比较：完全相同才跳过（避免 -Wfloat-equal，且语义即“值有无变化”）。
        // 基线 lastSentValues_[i] 初始为 NaN，其位型不等于任何正常值 → 首次必推送。
        const float last = lastSentValues_[i];
        if (std::memcmp(&value, &last, sizeof(float)) == 0)
            continue;

        lastSentValues_[i] = value;

        // 热路径：栈上内联 JSON，零堆分配。
        char buf[128];
        const int n = std::snprintf(buf, sizeof(buf),
            "{\"id\":\"%s\",\"value\":%.6g}",
            trackedParams_[i].id.c_str(), value);
        if (n > 0 && n < static_cast<int>(sizeof(buf)))
            sendBridgeMessage(CHANNEL_PARAM_UPDATE,
                              "paramChanged",
                              std::string_view(buf, static_cast<size_t>(n)));
    }
}

// ============================================================
// primeLastSentValues —— 把当前各参数值登记为“已同步”基线。
// 在全量 push（syncAllToFlutter / pushAllParameters）之后调用，
// 避免紧随其后的 timer 把同样的值再增量推送一遍。仅消息线程调用。
// ============================================================
void AudioParameterBridge::primeLastSentValues()
{
    for (size_t i = 0; i < trackedParams_.size(); ++i)
    {
        auto* rangedParam = trackedParams_[i].param;
        lastSentValues_[i] = rangedParam->convertFrom0to1(rangedParam->getValue());
    }
}

void AudioParameterBridge::sendBridgeMessage(std::string_view baseChannel,
                                             std::string_view method,
                                             std::string_view argsJson)
{
    embedder.sendMessage(embedder.namespacedChannel(baseChannel), method, argsJson);
}

void AudioParameterBridge::registerBridgeMethodHandler(
    std::string_view baseChannel,
    FlutterEmbedder::MethodCallback callback)
{
    const std::string nsChannel = embedder.namespacedChannel(baseChannel);
    embedder.registerMethodHandler(nsChannel, std::move(callback));
}

void AudioParameterBridge::unregisterBridgeMethodHandler(std::string_view baseChannel)
{
    embedder.unregisterMethodHandler(embedder.namespacedChannel(baseChannel));
}

// ============================================================
// buildParamJson
// 为冷路径（pushAllParameters）保留，热路径已内联改写。
// ============================================================
std::string AudioParameterBridge::buildParamJson(const std::string& paramId,
                                                  float value)
{
    char buf[128];
    const int n = std::snprintf(buf, sizeof(buf),
        "{\"id\":\"%s\",\"value\":%.6g}", paramId.c_str(), value);
    return (n > 0) ? std::string(buf, static_cast<std::size_t>(n)) : std::string("{}");
}

std::string AudioParameterBridge::buildMeterJson(float left, float right)
{
    char buf[64];
    const int n = std::snprintf(buf, sizeof(buf),
        "{\"left\":%.6g,\"right\":%.6g}", left, right);
    return (n > 0) ? std::string(buf, static_cast<std::size_t>(n)) : std::string("{}");
}

// ============================================================
// buildSchemaJson —— 序列化 PARAM_DEFS 为 JSON 数组
// ============================================================
std::string AudioParameterBridge::buildSchemaJson()
{
    const auto& defs = getAllParameterDefs();

    std::ostringstream oss;
    oss << std::fixed << std::setprecision(6);
    oss << "[";
    for (std::size_t i = 0; i < defs.size(); ++i)
    {
        const auto& d = defs[i];
        if (i > 0) oss << ",";
        oss << "{"
            << "\"id\":\""      << d.id.toStdString()    << "\","
            << "\"label\":\""   << d.label.toStdString() << "\","
            << "\"unit\":\""    << d.unit.toStdString()  << "\","
            << "\"min\":"       << d.min        << ","
            << "\"max\":"       << d.max        << ","
            << "\"default\":"   << d.defaultVal << ","
            << "\"skew\":"      << d.skewFactor << ","
            << "\"step\":"      << d.step       << ","
            << "\"bool\":"      << (d.isBoolean ? "true" : "false") << ","
            << "\"ui\":\""      << d.uiHint.toStdString() << "\""
            << "}";
    }
    oss << "]";
    return oss.str();
}

// ============================================================
// extractFloatFromJson
// 零堆分配：string_view 内部搜索 + strtof 直接解析。
// 全程无 substr/string 创建。
// ============================================================
float AudioParameterBridge::extractFloatFromJson(std::string_view json,
                                                  std::string_view key)
{
    // 在栈上构造搜索模式 "key":
    char pattern[68];
    const int plen = std::snprintf(pattern, sizeof(pattern),
        "\"%.*s\":", static_cast<int>(key.size()), key.data());
    if (plen <= 0 || plen >= static_cast<int>(sizeof(pattern)))
        return 0.0f;

    const auto pos = json.find(std::string_view(pattern, static_cast<size_t>(plen)));
    if (pos == std::string_view::npos)
        return 0.0f;

    const char* start = json.data() + pos + plen;
    char* endPtr      = nullptr;
    const float val   = std::strtof(start, &endPtr);
    return (endPtr && endPtr > start) ? val : 0.0f;
}