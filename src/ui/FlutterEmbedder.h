#pragma once

#include <juce_gui_basics/juce_gui_basics.h>
#include <functional>
#include <string>
#include <string_view>
#include <map>
#include <vector>

// 前向声明，避免在头文件引入 ObjC 或 FlutterEnginePrewarmer.h
class FlutterEnginePrewarmer;

// ============================================================
// FLUTTER_LOG — 在所有构建模式下均可用的日志宏（含 Release/Profile）。
// 同时写入 JUCE DBG 输出（仅 Debug 有效）、JUCE Logger，
// 并送入 FlutterEmbedder 静态日志缓冲区，可显示在 Fallback UI 及
// Flutter 调试页面中。
//
// 用法：FLUTTER_LOG("[FlutterEmbedder] 初始化完成");
//       FLUTTER_LOG("[FlutterEmbedder] 路径: " + path);
// ============================================================
#define FLUTTER_LOG(msg)                                           \
    do {                                                           \
        juce::String _fl_str__ = (msg);                           \
        DBG(_fl_str__);                                            \
        FlutterEmbedder::appendLog(_fl_str__);                    \
    } while (false)

/**
 * @brief Flutter Engine 嵌入器
 *
 * 封装 Flutter Engine 嵌入 API，将 Flutter 渲染到 JUCE Component 内部。
 * 支持通过方法通道与 Flutter UI 进行双向通信。
 *
 * 当 FLUTTER_ENGINE_ENABLED=0 时，回退为 JUCE 原生 UI。
 */
class FlutterEmbedder : public juce::Component,
                        private juce::Timer
{
public:
    // --------------------------------------------------------
    // 方法通道消息回调：(channel, method, args_json) -> result_json
    //
    // 参数均为 string_view（指向消息缓冲区），在回调返回前有效。
    // 若需跨异步边界持有字符串，请在 lambda 内显式复制为 std::string。
    // --------------------------------------------------------
    using MethodCallback = std::function<std::string(
        std::string_view channel,
        std::string_view method,
        std::string_view argsJson)>;

    // --------------------------------------------------------
    // 构造 / 析构
    // --------------------------------------------------------
    explicit FlutterEmbedder(const juce::File& flutterAssetsDir);
    ~FlutterEmbedder() override;

    // --------------------------------------------------------
    // 初始化 Flutter Engine（在 Processor 构造时调用，无需窗口）
    // --------------------------------------------------------
    bool initialize();

    // --------------------------------------------------------
    // Editor 打开时：将 Flutter NSView 重新挂到当前 Editor 的 peer 上。
    // 调用前需先 addAndMakeVisible(embedder) 并 setBounds。
    // --------------------------------------------------------
    void reattachToParent();

    // --------------------------------------------------------
    // Editor 关闭时：将 Flutter NSView 从窗口摘下，但保留 Engine 运行。
    // --------------------------------------------------------
    void detachFromParent();

    // --------------------------------------------------------
    // （可选）传入预热好的 Engine，在 initialize() 之前调用
    // 若已提供，initialize() 将直接接管该 Engine 而非冷启动
    // --------------------------------------------------------
    void setPrewarmer(std::shared_ptr<FlutterEnginePrewarmer> prewarmer);

    // --------------------------------------------------------
    // 向 Flutter 发送方法通道消息
    //
    // 参数均接受 string_view，内部使用栈缓冲区构造 JSON 信封，
    // 不产生堆分配（消息 < 1024 字节时）。
    // --------------------------------------------------------
    void sendMessage(std::string_view channel,
                     std::string_view method,
                     std::string_view argsJson = "{}");

    // --------------------------------------------------------
    // 生成实例级隔离通道名：
    //   audio_bridge/<leaf> -> audio_bridge/v2/<instanceNamespace>/<leaf>
    // --------------------------------------------------------
    std::string namespacedChannel(std::string_view baseChannel) const;

    // 当前实例的隔离命名空间（用于调试/诊断）
    std::string_view getChannelNamespace() const { return channelNamespace; }

    // --------------------------------------------------------
    // 注册来自 Flutter 的方法通道处理器
    // --------------------------------------------------------
    void registerMethodHandler(std::string_view channel,
                                MethodCallback callback);

    // --------------------------------------------------------
    // 以基础通道名注册实例隔离 handler。
    // 例如 baseChannel="audio_bridge/spectrum"。
    // --------------------------------------------------------
    void registerNamespacedMethodHandler(std::string_view baseChannel,
                                         MethodCallback callback);

    // --------------------------------------------------------
    // 注销方法通道处理器
    // 必须在持有 callback 的对象析构前调用，防止悬垂指针。
    // --------------------------------------------------------
    void unregisterMethodHandler(std::string_view channel);

    // 取消基础通道名对应的实例隔离 handler。
    void unregisterNamespacedMethodHandler(std::string_view baseChannel);

    // 发送到基础通道名对应的实例隔离 channel。
    void sendNamespacedMessage(std::string_view baseChannel,
                               std::string_view method,
                               std::string_view argsJson = "{}");

    // 扩展 leaf 便捷 API：leaf 为 "spectrum" / "preset_browser" / ...
    void registerExtensionHandler(std::string_view leaf, MethodCallback callback);
    void unregisterExtensionHandler(std::string_view leaf);
    void sendExtensionMessage(std::string_view leaf,
                              std::string_view method,
                              std::string_view argsJson = "{}");

    // --------------------------------------------------------
    // juce::Component 接口
    // --------------------------------------------------------
    void paint(juce::Graphics& g) override;
    void resized() override;
#if FLUTTER_ENGINE_ENABLED && defined(__APPLE__)
    // macOS 专属：持久引擎跑在 merged UI+platform thread 模式，juce::Timer 不被泵动，
    // 故 attach 改由 parentHierarchyChanged（JUCE 同步派发，不经 message loop）驱动。
    // Windows/Linux 的 embedder 无此问题（engine 本就常驻、timer 正常泵动），不需要。
    // 守卫须与 _mac.mm 的实现一致（FLUTTER_ENGINE_ENABLED && __APPLE__），否则 headless
    // macOS 构建会声明此 override 却无实现 → 链接失败。
    void parentHierarchyChanged() override;
#endif

    // --------------------------------------------------------
    // 输入事件转发
    // --------------------------------------------------------
    void mouseDown(const juce::MouseEvent& e) override;
    void mouseUp(const juce::MouseEvent& e) override;
    void mouseDrag(const juce::MouseEvent& e) override;
    void mouseMove(const juce::MouseEvent& e) override;
    void mouseWheelMove(const juce::MouseEvent& e,
                        const juce::MouseWheelDetails& wheel) override;
    bool keyPressed(const juce::KeyPress& key) override;

    // --------------------------------------------------------
    // 状态查询
    // --------------------------------------------------------
    bool isEngineRunning() const { return engineRunning; }

    // --------------------------------------------------------
    // 静态日志缓冲区（所有构建模式均有效，线程安全写入）
    //
    // appendLog    — 追加一条日志（可从任意线程调用）
    // getLogSnapshot — 获取当前日志快照（消息线程调用）
    // clearLogs    — 清空日志缓冲区（消息线程调用）
    //
    // 活跃实例（有 Editor 打开时）会将新日志实时推送到 Flutter
    // audio_bridge/debug_log 通道，Flutter 调试页面可实时显示。
    // --------------------------------------------------------
    static void appendLog(const juce::String& msg);
    static std::vector<std::string> getLogSnapshot();
    static void clearLogs();

    // Flutter 调试日志通道名（与 audio_bridge.dart 中常量一致）
    static constexpr const char* kDebugLogChannel = "audio_bridge/debug_log";
    static constexpr const char* kBootstrapChannel = "audio_bridge/bootstrap";

    // --------------------------------------------------------
    // 当 Flutter Engine 成功 attach 到宿主窗口时触发（engineRunning 已为 true）。
    // 由 PluginEditor 设置，用于在 Engine 就绪后立即同步参数到 Flutter。
    // --------------------------------------------------------
    std::function<void()> onEngineAttached;

    // Windows 静态回调 registerChannelCallback 需要访问此方法，小心不要在外部直接调用。
    // message 为 Flutter 消息缓冲区的视图，在 handlePlatformMessage 返回前有效。
    std::string handlePlatformMessage(std::string_view channel,
                                      std::string_view message);

private:
    // --------------------------------------------------------
    // Timer 回调（驱动 Flutter 渲染循环）
    // --------------------------------------------------------
    void timerCallback() override;

    // --------------------------------------------------------
    // 内部实现
    // --------------------------------------------------------
    void shutdownEngine();
#if FLUTTER_ENGINE_ENABLED && defined(__APPLE__)
    // macOS 专属（见 FlutterEmbedder_mac.mm，守卫须与其实现一致）：
    //   detachViewController — 关窗时分离当前 VC 但保留常驻 engine。
    //   scheduleAttachPoll   — 经 MessageManager::callAsync 自排的 attach 轮询（不依赖 juce::Timer）。
    void detachViewController();
    void scheduleAttachPoll(int attempt = 0);
#endif
    void registerBuiltinChannels();

    // --------------------------------------------------------
    // Flutter Engine 相关（使用前向声明避免在头文件暴露 engine 类型）
    // --------------------------------------------------------
#if FLUTTER_ENGINE_ENABLED
    struct EngineImpl;
    std::unique_ptr<EngineImpl> engineImpl;
#endif

    // --------------------------------------------------------
    // 平台专用辅助方法（macOS/Windows/Linux 各有实现）
    // --------------------------------------------------------
#if defined(__APPLE__) || defined(__linux__) || defined(_WIN32)
    void attachFlutterViewToHost();
    void syncFlutterViewBounds();
#endif

    // --------------------------------------------------------
    // 成员变量
    // --------------------------------------------------------
    juce::File assetsDir;
    bool engineRunning { false };
    bool fallbackMode  { false };   // 无 Flutter Engine 时使用 JUCE 原生 UI

    // 预热器（可选，由 PluginProcessor 提供）
    std::shared_ptr<FlutterEnginePrewarmer> prewarmer;

    // 通道 map（std::less<> 启用 string_view 透明查找，避免查找时的堆分配）
    std::map<std::string, MethodCallback, std::less<>> methodHandlers;

    // 回退模式下显示的文字
    juce::Label fallbackLabel;

    // --------------------------------------------------------
    // 静态日志缓冲区
    // --------------------------------------------------------
    static juce::CriticalSection s_logLock;
    static std::vector<std::string> s_logLines;
    static constexpr size_t kMaxLogLines = 300;

    // 所有存活实例（用于将全局日志广播到每个运行中的 Flutter UI）。
    static juce::CriticalSection s_instancesLock;
    static std::vector<FlutterEmbedder*> s_liveInstances;

    // 生成实例级 channel 命名空间，避免同进程多插件/多实例 channel 冲突。
    static std::string makeInstanceNamespace(const void* selfPtr);

    std::string channelNamespace;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(FlutterEmbedder)
};