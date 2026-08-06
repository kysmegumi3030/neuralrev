// ============================================================
// FlutterEmbedder.cpp
// 平台无关的通用实现：paint、fallback 绘制、
// handlePlatformMessage，以及非 macOS/Windows/Linux 的 stub 实现。
// macOS 平台特定代码在 FlutterEmbedder_mac.mm
// Windows 平台特定代码在 FlutterEmbedder_win.cpp
//
// 注意：构造/析构（包含 EngineImpl 的 make_unique/reset）
//       由各平台文件负责，这里只包含无 EngineImpl 的通用代码。
// ============================================================

#include "FlutterEmbedder.h"
#include <juce_core/juce_core.h>
#include <cstdio>
#include <atomic>

// ============================================================
// 静态成员定义
// ============================================================
juce::CriticalSection FlutterEmbedder::s_logLock;
std::vector<std::string> FlutterEmbedder::s_logLines;
juce::CriticalSection FlutterEmbedder::s_instancesLock;
std::vector<FlutterEmbedder*> FlutterEmbedder::s_liveInstances;

std::string FlutterEmbedder::makeInstanceNamespace(const void* selfPtr)
{
    static std::atomic<uint64_t> s_counter { 0 };
    const auto cnt = ++s_counter;
    const auto ptr = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(selfPtr));
    const auto tick = static_cast<uint64_t>(juce::Time::getMillisecondCounterHiRes());

    char buf[64]{};
    std::snprintf(buf, sizeof(buf), "t%llx_i%llx_c%llx",
                  static_cast<unsigned long long>(tick),
                  static_cast<unsigned long long>(ptr),
                  static_cast<unsigned long long>(cnt));
    return std::string(buf);
}

std::string FlutterEmbedder::namespacedChannel(std::string_view baseChannel) const
{
    constexpr std::string_view kPrefix = "audio_bridge/";
    if (baseChannel.rfind(kPrefix, 0) != 0)
        return std::string(baseChannel);

    const auto leaf = baseChannel.substr(kPrefix.size());
    std::string out;
    out.reserve(kPrefix.size() + 3 + channelNamespace.size() + 1 + leaf.size());
    out += "audio_bridge/v2/";
    out += channelNamespace;
    out += '/';
    out.append(leaf.data(), leaf.size());
    return out;
}

void FlutterEmbedder::registerNamespacedMethodHandler(std::string_view baseChannel,
                                                      MethodCallback callback)
{
    registerMethodHandler(namespacedChannel(baseChannel), std::move(callback));
}

void FlutterEmbedder::unregisterNamespacedMethodHandler(std::string_view baseChannel)
{
    unregisterMethodHandler(namespacedChannel(baseChannel));
}

void FlutterEmbedder::sendNamespacedMessage(std::string_view baseChannel,
                                            std::string_view method,
                                            std::string_view argsJson)
{
    sendMessage(namespacedChannel(baseChannel), method, argsJson);
}

namespace
{
    std::string makeExtensionBaseChannel(std::string_view leaf)
    {
        std::string out;
        out.reserve(13 + leaf.size());
        out += "audio_bridge/";
        out.append(leaf.data(), leaf.size());
        return out;
    }
}

void FlutterEmbedder::registerExtensionHandler(std::string_view leaf, MethodCallback callback)
{
    registerNamespacedMethodHandler(makeExtensionBaseChannel(leaf), std::move(callback));
}

void FlutterEmbedder::unregisterExtensionHandler(std::string_view leaf)
{
    unregisterNamespacedMethodHandler(makeExtensionBaseChannel(leaf));
}

void FlutterEmbedder::sendExtensionMessage(std::string_view leaf,
                                           std::string_view method,
                                           std::string_view argsJson)
{
    sendNamespacedMessage(makeExtensionBaseChannel(leaf), method, argsJson);
}

// ============================================================
// ConsoleRedirectLogger —— 把 juce::Logger::writeToLog() 的所有调用
// （包括 JUCE 内部各模块、未经 FLUTTER_LOG 宏包装的原始日志调用）
// 一并汇入 appendLog() 的统一缓冲区，实现 Flutter 调试页面
// "C++ 标准输出（所有输出流）" 的展示需求。
//
// 注意：appendLog() 内部不能再调用 juce::Logger::writeToLog()，
// 否则会与这里的 logMessage() 互相递归。
// ============================================================
namespace
{
    class ConsoleRedirectLogger final : public juce::Logger
    {
    public:
        void logMessage(const juce::String& msg) override
        {
            FlutterEmbedder::appendLog(msg);
        }
    };

    // 首次调用 appendLog() 时惰性安装（C++11 静态局部变量初始化线程安全），
    // 不依赖任何具体平台的构造/初始化时机。
    void ensureConsoleLoggerInstalled()
    {
        static ConsoleRedirectLogger logger;
        static const bool installedOnce = []
        {
            juce::Logger::setCurrentLogger(&logger);
            return true;
        }();
        juce::ignoreUnused(installedOnce);
    }
}

// ============================================================
// appendLog — 线程安全，可在任意线程调用
// ============================================================
void FlutterEmbedder::appendLog(const juce::String& msg)
{
    ensureConsoleLoggerInstalled();

    const auto str = msg.toStdString();
    {
        juce::ScopedLock lock(s_logLock);
        s_logLines.push_back(str);
        if (s_logLines.size() > kMaxLogLines)
            s_logLines.erase(s_logLines.begin());
    }

    // 向 Flutter 调试页面实时推送（切换到消息线程以调用 sendMessage）
    juce::MessageManager::callAsync([str]()
    {
        std::vector<FlutterEmbedder*> live;
        {
            juce::ScopedLock lock(s_instancesLock);
            live = s_liveInstances;
        }

        // 对 JSON 字符串中的特殊字符做基础转义
        std::string escaped;
        escaped.reserve(str.size() + 8);
        for (const char rc : str)
        {
            const unsigned char c = static_cast<unsigned char>(rc);
            switch (c)
            {
                case '\\': escaped += "\\\\"; break;
                case '"':  escaped += "\\\""; break;
                case '\n': escaped += "\\n";  break;
                case '\r':                    break;  // 忽略 CR
                default:
                    if (c < 0x20)           // 其他控制字符
                        escaped += ' ';
                    else
                        escaped += static_cast<char>(c);
                    break;
            }
        }

        bool pushedToEngine = false;
        for (auto* inst : live)
        {
            if (inst == nullptr)
                continue;

            if (inst->engineRunning)
            {
                inst->sendMessage(inst->namespacedChannel(FlutterEmbedder::kDebugLogChannel),
                                  "appendLog",
                                  "{\"line\":\"" + escaped + "\"}");
                pushedToEngine = true;
            }
            else
            {
                inst->repaint();
            }
        }

        juce::ignoreUnused(pushedToEngine);
    });
}

// ============================================================
// getLogSnapshot — 返回日志快照（消息线程调用）
// ============================================================
std::vector<std::string> FlutterEmbedder::getLogSnapshot()
{
    juce::ScopedLock lock(s_logLock);
    return s_logLines;
}

// ============================================================
// clearLogs — 清空日志缓冲区（消息线程调用）
// ============================================================
void FlutterEmbedder::clearLogs()
{
    juce::ScopedLock lock(s_logLock);
    s_logLines.clear();
}

// ============================================================
// paint — 通用实现（Flutter 运行时由 Flutter 自行渲染）
// ============================================================
void FlutterEmbedder::paint(juce::Graphics& g)
{
    // 冷启动过渡期（引擎尚未 attach 但未失败）：绘制中性背景 + 静态占位，
    // 遮盖冷启动黑屏。首帧到来后 Flutter 的 native NSView 盖在其上，自然覆盖。
    //
    // 关键：这里**只做静态绘制，绝不调用 repaint()**。之前用 timerCallback
    // 高频 repaint 驱动动画会诱发 JUCE→宿主 resize → Flutter
    // ResizeSynchronizer 排入延迟 performCommit → 落到 null surface 崩溃
    // （REAPER 反复切换必崩，见崩溃栈）。静态占位仅在 JUCE 自然触发 paint
    // （首次显示 / resize）时画一次，不产生额外重绘，不诱发 resize commit。
    // 诊断版 Fallback UI 仅在引擎「真正失败」（fallbackMode）时才显示。
    if (!fallbackMode && !engineRunning)
    {
        const auto bounds = getLocalBounds().toFloat();
        g.fillAll(juce::Colour(0xff1e1e2e));   // 中性深色背景

        // 居中静态占位：一个柔和蓝的圆环轮廓 + "Loading…" 文字。
        // 全部静止，不依赖时间相位，不重绘。
        const auto cx = bounds.getCentreX();
        const auto cy = bounds.getCentreY();
        const float r = juce::jlimit(16.0f, 32.0f,
                                     juce::jmin(bounds.getWidth(), bounds.getHeight()) * 0.06f);

        g.setColour(juce::Colour(0xff89b4fa).withAlpha(0.85f));
        g.drawEllipse(cx - r, cy - r, r * 2.0f, r * 2.0f, juce::jmax(2.0f, r * 0.12f));

        g.setColour(juce::Colour(0xffcdd6f4).withAlpha(0.7f));
        g.setFont(juce::FontOptions(juce::jlimit(11.0f, 15.0f, r * 0.55f)));
        g.drawText("Loading\xe2\x80\xa6",
                   juce::Rectangle<float>(cx - 80.0f, cy + r + 8.0f, 160.0f, 20.0f),
                   juce::Justification::centred);
        return;
    }

    if (fallbackMode || !engineRunning)
    {
        const auto bounds = getLocalBounds();

        g.fillAll(juce::Colour(0xff1e1e2e));

        // ── 标题栏 ──────────────────────────────────────────
        const auto titleArea = bounds.withHeight(40);
        g.setColour(juce::Colour(0xff181825));
        g.fillRect(titleArea);
        g.setColour(juce::Colour(0xff89b4fa));
        g.setFont(juce::FontOptions(15.0f).withStyle("Bold"));
        g.drawText("JuceFlutterPlugin  [Fallback UI]",
                   titleArea,
                   juce::Justification::centred);

        // ── 副标题 ──────────────────────────────────────────
        g.setColour(juce::Colour(0xfff38ba8));
        g.setFont(juce::FontOptions(11.0f));
        const auto subtitleArea = bounds.withY(40).withHeight(18);
        g.drawText(fallbackMode
                       ? "Flutter Engine \xe6\x9c\xaa\xe5\x90\xaf\xe5\x8a\xa8\xef\xbc\x8c\xe8\xaf\xb7\xe6\x9f\xa5\xe7\x9c\x8b\xe4\xb8\x8b\xe6\x96\xb9\xe6\x97\xa5\xe5\xbf\x97"
                       : "Flutter Engine \xe5\x90\xaf\xe5\x8a\xa8\xe4\xb8\xad...",
                   subtitleArea,
                   juce::Justification::centred);

        // ── 日志区域 ─────────────────────────────────────────
        const auto logArea = bounds.withY(62).withHeight(bounds.getHeight() - 62).reduced(6, 2);
        constexpr int kLineH = 14;
        const int maxVisible = logArea.getHeight() / kLineH;

        const auto snapshot = getLogSnapshot();
        const int total = static_cast<int>(snapshot.size());
        const int startIdx = juce::jmax(0, total - maxVisible);

        auto rowArea = logArea;
        g.setFont(juce::FontOptions(11.0f));
        for (int i = startIdx; i < total; ++i)
        {
            const auto& line = snapshot[static_cast<size_t>(i)];
            // 按关键词着色
            if (line.find("ERROR") != std::string::npos ||
                line.find("Failed") != std::string::npos ||
                line.find("failed") != std::string::npos ||
                line.find("not found") != std::string::npos)
                g.setColour(juce::Colour(0xfff38ba8));   // 红
            else if (line.find("WARNING") != std::string::npos ||
                     line.find("WARN") != std::string::npos ||
                     line.find("WARNING") != std::string::npos)
                g.setColour(juce::Colour(0xfffab387));   // 橙
            else if (line.find("[FlutterEmbedder]") != std::string::npos)
                g.setColour(juce::Colour(0xffa6e3a1));   // 绿
            else
                g.setColour(juce::Colour(0xffcdd6f4));   // 白

            g.drawText(juce::String::fromUTF8(line.c_str()),
                       rowArea.removeFromTop(kLineH),
                       juce::Justification::centredLeft,
                       true /* 截断过长文本 */);
        }
    }
    // Flutter 在 native view 中渲染时，此处不绘制任何内容
}

// ============================================================
// handlePlatformMessage — 通用实现
// ============================================================
std::string FlutterEmbedder::handlePlatformMessage(std::string_view channel,
                                                   std::string_view message)
{
    // bootstrap 握手：Dart 启动时请求当前实例的 namespace 与协议版本。
    if (channel == kBootstrapChannel)
    {
        constexpr std::string_view kMethodKey = "\"method\":\"";
        const auto pos = message.find(kMethodKey);
        if (pos != std::string_view::npos)
        {
            const auto start = pos + kMethodKey.size();
            const auto end   = message.find('"', start);
            if (end != std::string_view::npos)
            {
                const auto method = message.substr(start, end - start);
                if (method == "hello" || method == "getNamespace")
                {
                    std::string json;
                    json.reserve(160 + channelNamespace.size());
                    json += "{\"protocol\":2,\"namespace\":\"";
                    json += channelNamespace;
                    json += "\",\"channelPrefix\":\"audio_bridge/v2/";
                    json += channelNamespace;
                    json += "\"}";
                    return json;
                }
            }
        }
        return "{}";
    }

    const auto namespacedDebug = namespacedChannel(kDebugLogChannel);

    // 处理内置调试日志通道（getLogs 请求）
    if (channel == namespacedDebug)
    {
        // 提取 method
        constexpr std::string_view kMethodKey = "\"method\":\"";
        const auto pos = message.find(kMethodKey);
        if (pos != std::string_view::npos)
        {
            const auto start = pos + kMethodKey.size();
            const auto end   = message.find('"', start);
            if (end != std::string_view::npos)
            {
                const auto method = message.substr(start, end - start);
                if (method == "getLogs")
                {
                    // 将日志缓冲区序列化为 JSON 数组
                    const auto snapshot = getLogSnapshot();
                    std::string json;
                    json.reserve(snapshot.size() * 64);
                    json += '[';
                    for (size_t i = 0; i < snapshot.size(); ++i)
                    {
                        if (i > 0) json += ',';
                        json += '"';
                        for (const char rc : snapshot[i])
                        {
                            const unsigned char c = static_cast<unsigned char>(rc);
                            switch (c)
                            {
                                case '\\': json += "\\\\"; break;
                                case '"':  json += "\\\""; break;
                                case '\n': json += "\\n";  break;
                                case '\r':                 break;
                                default:
                                    if (c < 0x20) json += ' ';
                                    else json += static_cast<char>(c);
                            }
                        }
                        json += '"';
                    }
                    json += ']';
                    return json;
                }
                if (method == "clearLogs")
                {
                    clearLogs();
                    return "{}";
                }
            }
        }
        return "{}";
    }

    // std::less<> 支持透明查找，string_view 直接查找无需构造 std::string
    auto it = methodHandlers.find(channel);
    if (it == methodHandlers.end()) return {};

    // 不调用 substr()：直接在 message 上开窗口，零堆分配
    std::string_view method;
    constexpr std::string_view kMethodKey = "\"method\":\"";
    const auto pos = message.find(kMethodKey);
    if (pos != std::string_view::npos)
    {
        const auto start = pos + kMethodKey.size();
        const auto end   = message.find('"', start);
        if (end != std::string_view::npos)
            method = message.substr(start, end - start);
    }
    return it->second(channel, method, message);
}

void FlutterEmbedder::registerBuiltinChannels()
{
    // 仅用于触发平台层 callback 注册；实际逻辑由 handlePlatformMessage 前置处理。
    methodHandlers.insert_or_assign(std::string(kBootstrapChannel),
        [](std::string_view, std::string_view, std::string_view) { return std::string(); });

    methodHandlers.insert_or_assign(namespacedChannel(kDebugLogChannel),
        [](std::string_view, std::string_view, std::string_view) { return std::string(); });
}

// ============================================================
// 鼠标 / 键盘事件 — 通用 stub
// ============================================================
void FlutterEmbedder::mouseDown(const juce::MouseEvent& e)      { juce::ignoreUnused(e); }
void FlutterEmbedder::mouseUp(const juce::MouseEvent& e)        { juce::ignoreUnused(e); }
void FlutterEmbedder::mouseDrag(const juce::MouseEvent& e)      { juce::ignoreUnused(e); }
void FlutterEmbedder::mouseMove(const juce::MouseEvent& e)      { juce::ignoreUnused(e); }
void FlutterEmbedder::mouseWheelMove(const juce::MouseEvent& e,
                                      const juce::MouseWheelDetails& w)
{
    juce::ignoreUnused(e, w);
}
bool FlutterEmbedder::keyPressed(const juce::KeyPress& key)
{
    juce::ignoreUnused(key);
    return false;
}

// ============================================================
// 非 macOS / Windows / Linux 平台的完整 stub 实现
// （其他未知平台暂不支持 Flutter，走 fallback 路径）
// ============================================================
#if !defined(__APPLE__) && !defined(_WIN32) && !defined(__linux__)

// EngineImpl stub — 空结构体即可，无成员
struct FlutterEmbedder::EngineImpl {};

FlutterEmbedder::FlutterEmbedder(const juce::File& flutterAssetsDir)
    : assetsDir(flutterAssetsDir)
    , channelNamespace(makeInstanceNamespace(this))
{
    setOpaque(true);
    fallbackLabel.setJustificationType(juce::Justification::centred);
    fallbackLabel.setColour(juce::Label::textColourId, juce::Colours::white);
    fallbackLabel.setText("Flutter Engine unavailable\n(Fallback to JUCE native UI)",
                          juce::dontSendNotification);
    addChildComponent(fallbackLabel);
#if FLUTTER_ENGINE_ENABLED
    engineImpl = std::make_unique<EngineImpl>();
    registerBuiltinChannels();

    {
        juce::ScopedLock lock(s_instancesLock);
        s_liveInstances.push_back(this);
    }
#endif
}

FlutterEmbedder::~FlutterEmbedder()
{
    stopTimer();
    shutdownEngine();

    juce::ScopedLock lock(s_instancesLock);
    const auto it = std::find(s_liveInstances.begin(), s_liveInstances.end(), this);
    if (it != s_liveInstances.end())
        s_liveInstances.erase(it);
}

bool FlutterEmbedder::initialize()
{
    FLUTTER_LOG("[FlutterEmbedder] Platform not supported, using fallback");
    fallbackMode = true;
    fallbackLabel.setVisible(true);
    return false;
}

void FlutterEmbedder::detachFromParent()  {}
void FlutterEmbedder::reattachToParent() {}

void FlutterEmbedder::shutdownEngine()
{
    engineRunning = false;
}

void FlutterEmbedder::sendMessage(std::string_view channel,
                                   std::string_view method,
                                   std::string_view argsJson)
{
    juce::ignoreUnused(channel, method, argsJson);
}

void FlutterEmbedder::registerMethodHandler(std::string_view channel,
                                             MethodCallback callback)
{
    // insert_or_assign 确保旧 lambda 被新的替换，
    // 防止重开 Editor 时 emplace 静默保留悬垂峾针。
    methodHandlers.insert_or_assign(std::string(channel), std::move(callback));
}

void FlutterEmbedder::unregisterMethodHandler(std::string_view channel)
{
    methodHandlers.erase(std::string(channel));
}

void FlutterEmbedder::timerCallback() {}

void FlutterEmbedder::resized()
{
    fallbackLabel.setBounds(getLocalBounds());
}

#endif // !__APPLE__ && !_WIN32 && !__linux__
