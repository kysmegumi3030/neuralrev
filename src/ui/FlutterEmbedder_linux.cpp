// ============================================================
// FlutterEmbedder_linux.cpp
// Linux GTK 实现：使用 flutter_linux_gtk API 将 FlView (GTK widget)
// 嵌入到 JUCE Component 的 GdkWindow 内。
//
// 依赖：
//   - libflutter_linux_gtk.so (flutter_linux_gtk)
//   - GTK+ 3.0 (gtk+-3.0)
//   - 头文件: <flutter_linux/flutter_linux.h>
// ============================================================

#include "FlutterEmbedder.h"

#if FLUTTER_ENGINE_ENABLED && defined(__linux__)

#include <flutter_linux/flutter_linux.h>
#include <gtk/gtk.h>
#include <gdk/gdkx.h>
#include <juce_core/juce_core.h>
#include <fstream>
#include <string>
#include <cstring>
#include <cmath>
#include <algorithm>

// ============================================================
// 文件日志（调试用，Release 构建中 DBG 不输出时使用）
// ============================================================
namespace {

static std::ofstream& getLogFile()
{
    static std::ofstream logFile("/tmp/juce_flutter_linux.log",
                                  std::ios::out | std::ios::app);
    return logFile;
}

static void logMsg(const std::string& msg)
{
    auto& f = getLogFile();
    if (f.is_open())
    {
        f << "[FlutterEmbedder/Linux] " << msg << "\n";
        f.flush();
    }
    DBG("[FlutterEmbedder/Linux] " + juce::String(msg));
}

} // namespace

// ============================================================
// Linux 专用 EngineImpl
// ============================================================
struct FlutterEmbedder::EngineImpl
{
    FlDartProject*    project    { nullptr };
    FlEngine*         engine     { nullptr };
    FlView*           view       { nullptr };  // GTK widget
    FlBinaryMessenger* messenger { nullptr };

    // 保存用于回调的消息通道（不使用 FlBasicMessageChannel，直接用 binary messenger）
    // 容器 key = channel name
    struct ChannelReg {
        FlutterEmbedder* host;
        std::string channelName;
    };
    std::map<std::string, ChannelReg*> channelRegs;

    // GtkFixed 容器（JUCE peer widget 内部用来定位 FlView 的容器）
    GtkWidget* fixedContainer { nullptr };

    // 是否已将 FlView 加入父容器
    bool viewAttached { false };
};

// ============================================================
// 查找 JUCE peer 的顶层 GtkWidget
// ============================================================
static GtkWidget* getJucePeerWidget(juce::Component& comp)
{
    auto* peer = comp.getPeer();
    if (!peer) return nullptr;
    // JUCE Linux: getNativeHandle() 返回 Window (XID / GdkWindow*)
    // 但对于 GTK 后端，我们通过 gdk_window_get_user_data 反向查找 GtkWidget
    auto* nativeHandle = peer->getNativeHandle();
    if (!nativeHandle) return nullptr;

    // 尝试将其作为 GtkWidget* 使用
    // JUCE with GTK backend: getNativeHandle() 返回底层的 GdkWindow*
    // 我们通过 gdk_window_get_user_data 获取 GtkWidget
    GdkWindow* gdkWin = reinterpret_cast<GdkWindow*>(nativeHandle);
    if (!GDK_IS_WINDOW(gdkWin)) return nullptr;

    gpointer userData = nullptr;
    gdk_window_get_user_data(gdkWin, &userData);
    if (!userData) return nullptr;

    if (GTK_IS_WIDGET(userData))
        return GTK_WIDGET(userData);

    return nullptr;
}

// ============================================================
// initialize — Linux 实现
// ============================================================
bool FlutterEmbedder::initialize()
{
    logMsg("initialize() 开始");

    if (!assetsDir.isDirectory())
    {
        logMsg("Flutter assets 目录不存在: " + assetsDir.getFullPathName().toStdString());
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    const auto assetsPath = assetsDir.getFullPathName();
    logMsg("assets path = " + assetsPath.toStdString());

    // --------------------------------------------------------
    // 1. 创建 FlDartProject 并设置 assets 路径
    // --------------------------------------------------------
    engineImpl->project = fl_dart_project_new();
    if (!engineImpl->project)
    {
        logMsg("fl_dart_project_new() 失败");
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    fl_dart_project_set_assets_path(engineImpl->project, assetsPath.toRawUTF8());

    // --------------------------------------------------------
    // 2. 创建 FlView（同时创建引擎）
    // --------------------------------------------------------
    engineImpl->view = fl_view_new(engineImpl->project);
    if (!engineImpl->view)
    {
        logMsg("fl_view_new() 失败");
        g_object_unref(engineImpl->project);
        engineImpl->project = nullptr;
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    engineImpl->engine = fl_view_get_engine(engineImpl->view);
    if (!engineImpl->engine)
    {
        logMsg("fl_view_get_engine() 返回 nullptr");
        gtk_widget_destroy(GTK_WIDGET(engineImpl->view));
        engineImpl->view = nullptr;
        g_object_unref(engineImpl->project);
        engineImpl->project = nullptr;
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    // --------------------------------------------------------
    // 3. 启动引擎
    // --------------------------------------------------------
    GError* error = nullptr;
    if (!fl_engine_start(engineImpl->engine, &error))
    {
        std::string errMsg = "fl_engine_start() 失败";
        if (error) {
            errMsg += ": ";
            errMsg += error->message;
            g_error_free(error);
        }
        logMsg(errMsg);
        gtk_widget_destroy(GTK_WIDGET(engineImpl->view));
        engineImpl->view   = nullptr;
        engineImpl->engine = nullptr;
        g_object_unref(engineImpl->project);
        engineImpl->project = nullptr;
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    // --------------------------------------------------------
    // 4. 获取 Binary Messenger（用于消息通道）
    // --------------------------------------------------------
    engineImpl->messenger = fl_engine_get_binary_messenger(engineImpl->engine);

    // --------------------------------------------------------
    // 5. 尝试立即附加到 JUCE peer（如 peer 已存在）
    // --------------------------------------------------------
    auto* peer = getPeer();
    if (peer)
    {
        logMsg("peer 已存在，立即尝试 attachFlutterViewToHost()");
        attachFlutterViewToHost();
    }
    else
    {
        logMsg("peer 尚未就绪，将由 timerCallback 重试");
    }

    startTimerHz(60);
    logMsg("initialize() 完成");
    return true;
}

// ============================================================
// detachFromParent — Linux：把 FlView 从 GTK 容器摘下，但保留 Engine 运行
// ============================================================
void FlutterEmbedder::detachFromParent()
{
    if (!engineImpl || !engineImpl->view) return;

    GtkWidget* flWidget = GTK_WIDGET(engineImpl->view);
    GtkWidget* parent = gtk_widget_get_parent(flWidget);
    if (parent)
        gtk_container_remove(GTK_CONTAINER(parent), flWidget);

    engineImpl->viewAttached    = false;
    engineImpl->fixedContainer  = nullptr;
    engineRunning = false;
    logMsg("detachFromParent: FlView 已从 GTK 容器摘下，Engine 继续运行");
}

// ============================================================
// reattachToParent — Linux：Editor 打开时将 FlView 重新挂载
// ============================================================
void FlutterEmbedder::reattachToParent()
{
    if (!engineImpl || !engineImpl->view) return;

    if (getPeer())
    {
        attachFlutterViewToHost();
        return;
    }

    if (!isTimerRunning())
        startTimerHz(60);
}

// ============================================================
// attachFlutterViewToHost — 将 FlView GTK widget 嵌入 JUCE peer
// ============================================================
void FlutterEmbedder::attachFlutterViewToHost()
{
    if (!engineImpl || !engineImpl->view) return;

    auto* peer = getPeer();
    if (!peer)
    {
        logMsg("attachFlutterViewToHost: peer 为 nullptr，等待重试");
        return;
    }

    GtkWidget* peerWidget = getJucePeerWidget(*this);
    if (!peerWidget)
    {
        // 回退方案：尝试直接从 peer 的 component 获取 widget
        auto* peerComp = &peer->getComponent();
        GtkWidget* topWidget = getJucePeerWidget(*peerComp);
        peerWidget = topWidget;
    }

    if (!peerWidget)
    {
        logMsg("attachFlutterViewToHost: 无法获取 JUCE peer GtkWidget，等待重试");
        return;
    }

    // 计算 FlutterEmbedder 相对于 peer 顶层 widget 的偏移量
    auto globalTopLeft = localPointToGlobal(juce::Point<int>(0, 0));
    auto& peerComp = peer->getComponent();
    auto relInPeer = peerComp.getLocalPoint(nullptr, globalTopLeft);

    const int x = relInPeer.x;
    const int y = relInPeer.y;
    const int w = juce::jmax(1, getWidth());
    const int h = juce::jmax(1, getHeight());

    logMsg("attachFlutterViewToHost: 位置=(" + std::to_string(x) + "," + std::to_string(y)
           + ") 尺寸=" + std::to_string(w) + "×" + std::to_string(h));

    GtkWidget* flWidget = GTK_WIDGET(engineImpl->view);

    // 设置 FlView 尺寸
    gtk_widget_set_size_request(flWidget, w, h);

    // 需要找到一个 GtkFixed 或 GtkLayout 容器来精确定位
    // 如果 peerWidget 本身是 GtkFixed，直接添加
    // 否则创建一个 overlay 式的处理方式
    if (!engineImpl->viewAttached)
    {
        // 确保 FlView widget 可见
        gtk_widget_show(flWidget);
        gtk_widget_realize(flWidget);

        // 尝试使用 GtkFixed 容器（如 JUCE peer 用 GtkFixed 布局）
        if (GTK_IS_FIXED(peerWidget))
        {
            gtk_fixed_put(GTK_FIXED(peerWidget), flWidget, x, y);
            engineImpl->fixedContainer = peerWidget;
            engineImpl->viewAttached = true;
            logMsg("attachFlutterViewToHost: 已通过 gtk_fixed_put 添加");
        }
        else if (GTK_IS_CONTAINER(peerWidget))
        {
            // 对于其他容器类型，尝试遍历子 widget 查找 GtkFixed
            GList* children = gtk_container_get_children(GTK_CONTAINER(peerWidget));
            GtkWidget* fixedChild = nullptr;
            for (GList* l = children; l != nullptr; l = l->next)
            {
                if (GTK_IS_FIXED(l->data))
                {
                    fixedChild = GTK_WIDGET(l->data);
                    break;
                }
            }
            g_list_free(children);

            if (fixedChild)
            {
                gtk_fixed_put(GTK_FIXED(fixedChild), flWidget, x, y);
                engineImpl->fixedContainer = fixedChild;
                engineImpl->viewAttached = true;
                logMsg("attachFlutterViewToHost: 已通过子 GtkFixed 的 gtk_fixed_put 添加");
            }
            else
            {
                // 创建一个新的 GtkFixed 容器叠加
                // 对于不支持直接子 widget 定位的容器，使用 GtkOverlay
                if (GTK_IS_BIN(peerWidget))
                {
                    GtkWidget* child = gtk_bin_get_child(GTK_BIN(peerWidget));
                    if (child && GTK_IS_FIXED(child))
                    {
                        gtk_fixed_put(GTK_FIXED(child), flWidget, x, y);
                        engineImpl->fixedContainer = child;
                        engineImpl->viewAttached = true;
                        logMsg("attachFlutterViewToHost: 已通过 GtkBin 子 GtkFixed 添加");
                    }
                }

                if (!engineImpl->viewAttached)
                {
                    logMsg("attachFlutterViewToHost: 无法找到合适的 GTK 容器，回退到 fallback");
                    fallbackMode = true;
                    fallbackLabel.setVisible(true);
                    return;
                }
            }
        }
        else
        {
            logMsg("attachFlutterViewToHost: peerWidget 不是 GtkContainer，无法嵌入");
            fallbackMode = true;
            fallbackLabel.setVisible(true);
            return;
        }
    }

    engineRunning = true;
    repaint();
    logMsg("attachFlutterViewToHost: 完成，engineRunning=true");

    // 通知外部 Engine 已就绪（e.g. PluginEditor 可立即同步参数到 Flutter）
    if (onEngineAttached)
        onEngineAttached();
}

// ============================================================
// syncFlutterViewBounds — 同步 FlView 位置和尺寸
// ============================================================
void FlutterEmbedder::syncFlutterViewBounds()
{
    if (!engineImpl || !engineImpl->view || !engineImpl->viewAttached) return;

    auto* peer = getPeer();
    if (!peer) return;

    auto globalTopLeft = localPointToGlobal(juce::Point<int>(0, 0));
    auto& peerComp = peer->getComponent();
    auto relInPeer = peerComp.getLocalPoint(nullptr, globalTopLeft);

    const int x = relInPeer.x;
    const int y = relInPeer.y;
    const int w = juce::jmax(1, getWidth());
    const int h = juce::jmax(1, getHeight());

    GtkWidget* flWidget = GTK_WIDGET(engineImpl->view);

    // 获取当前位置
    GtkAllocation alloc;
    gtk_widget_get_allocation(flWidget, &alloc);

    // 只在有变化时更新
    if (alloc.x != x || alloc.y != y || alloc.width != w || alloc.height != h)
    {
        gtk_widget_set_size_request(flWidget, w, h);

        if (engineImpl->fixedContainer && GTK_IS_FIXED(engineImpl->fixedContainer))
            gtk_fixed_move(GTK_FIXED(engineImpl->fixedContainer), flWidget, x, y);

        gtk_widget_queue_resize(flWidget);
    }
}

// ============================================================
// shutdownEngine — Linux 实现
// ============================================================
void FlutterEmbedder::shutdownEngine()
{
    stopTimer();
    if (engineImpl)
    {
        // 先从容器中移除 widget
        if (engineImpl->view && engineImpl->viewAttached && engineImpl->fixedContainer)
        {
            GtkWidget* flWidget = GTK_WIDGET(engineImpl->view);
            if (gtk_widget_get_parent(flWidget) == engineImpl->fixedContainer)
                gtk_container_remove(GTK_CONTAINER(engineImpl->fixedContainer), flWidget);
        }

        // 清理已注册的 channel 回调数据
        for (auto& [name, reg] : engineImpl->channelRegs)
        {
            if (reg) { delete reg; }
        }
        engineImpl->channelRegs.clear();

        // 销毁 FlView（会自动清理引擎）
        if (engineImpl->view)
        {
            gtk_widget_destroy(GTK_WIDGET(engineImpl->view));
            engineImpl->view    = nullptr;
            engineImpl->engine  = nullptr;  // 由 FlView 拥有
            engineImpl->messenger = nullptr;
        }

        if (engineImpl->project)
        {
            g_object_unref(engineImpl->project);
            engineImpl->project = nullptr;
        }

        engineImpl->viewAttached    = false;
        engineImpl->fixedContainer  = nullptr;
    }
    engineRunning = false;
}

// ============================================================
// sendMessage — Linux 实现（通过 FlBinaryMessenger 发送 JSON）
// ============================================================
void FlutterEmbedder::sendMessage(std::string_view channel,
                                   std::string_view method,
                                   std::string_view argsJson)
{
    if (!engineRunning || !engineImpl || !engineImpl->messenger) return;

    char buf[1024];
    const int n = std::snprintf(buf, sizeof(buf),
        "{\"method\":\"%.*s\",\"args\":%.*s}",
        static_cast<int>(method.size()),  method.data(),
        static_cast<int>(argsJson.size()), argsJson.data());

    if (n > 0 && n < static_cast<int>(sizeof(buf)))
    {
        fl_binary_messenger_send_on_channel(
            engineImpl->messenger,
            std::string(channel).c_str(),
            reinterpret_cast<const uint8_t*>(buf),
            static_cast<size_t>(n),
            nullptr, nullptr, nullptr
        );
    }
    else
    {
        // 超大消息（极少出现）：退化为堆分配路径（与 Windows 对齐）
        std::string payload;
        payload.reserve(24 + method.size() + argsJson.size());
        payload  = "{\"method\":\"";
        payload.append(method.data(), method.size());
        payload += "\",\"args\":";
        payload.append(argsJson.data(), argsJson.size());
        payload += '}';
        fl_binary_messenger_send_on_channel(
            engineImpl->messenger,
            std::string(channel).c_str(),
            reinterpret_cast<const uint8_t*>(payload.data()),
            payload.size(),
            nullptr, nullptr, nullptr
        );
    }
}

// ============================================================
// registerMethodHandler — Linux 实现
// ============================================================
void FlutterEmbedder::registerMethodHandler(std::string_view channel,
                                             MethodCallback callback)
{
    // insert_or_assign 确保旧的悬垂 lambda 被替换（UAF-1 修复）
    auto [it, _] = methodHandlers.insert_or_assign(std::string(channel), std::move(callback));
    if (!engineImpl || !engineImpl->messenger) return;

    const std::string& ownedChannel = it->first;

    // 清理旧的注册数据
    auto existIt = engineImpl->channelRegs.find(ownedChannel);
    if (existIt != engineImpl->channelRegs.end())
    {
        delete existIt->second;
        engineImpl->channelRegs.erase(existIt);
    }

    auto* reg = new EngineImpl::ChannelReg{ this, ownedChannel };
    engineImpl->channelRegs[ownedChannel] = reg;

    fl_binary_messenger_set_message_handler_on_channel(
        engineImpl->messenger,
        ownedChannel.c_str(),
        [](FlBinaryMessenger* messenger,
           const gchar* channelName,
           GBytes* message,
           FlBinaryMessengerResponseHandle* responseHandle,
           gpointer userData)
        {
            auto* reg = static_cast<EngineImpl::ChannelReg*>(userData);
            if (!reg || !reg->host) return;

            gsize len = 0;
            const guint8* data = static_cast<const guint8*>(
                g_bytes_get_data(message, &len));

            // string_view 指向 GBytes 河有缓冲区，在回调内部有效，不创建副本
            const std::string_view payload(
                reinterpret_cast<const char*>(data), len);
            // 使用返回值作为响应（bootstrap 握手等需要此响应），
            // 对应 Windows 的 FlutterDesktopMessengerSendResponse 模式。
            std::string result = reg->host->handlePlatformMessage(reg->channelName, payload);

            GBytes* responseBytes = nullptr;
            if (!result.empty())
                responseBytes = g_bytes_new(result.data(), result.size());
            fl_binary_messenger_send_response(messenger, responseHandle,
                                               responseBytes, nullptr);
            if (responseBytes)
                g_bytes_unref(responseBytes);
        },
        reg,
        nullptr
    );
}

// ============================================================
// unregisterMethodHandler — Linux 实现
// ============================================================
void FlutterEmbedder::unregisterMethodHandler(std::string_view channel)
{
    const std::string key(channel);
    methodHandlers.erase(key);

    if (!engineImpl) return;

    auto it = engineImpl->channelRegs.find(key);
    if (it != engineImpl->channelRegs.end())
    {
        delete it->second;
        engineImpl->channelRegs.erase(it);
    }

    if (engineImpl->messenger)
    {
        fl_binary_messenger_set_message_handler_on_channel(
            engineImpl->messenger,
            key.c_str(),
            nullptr, nullptr, nullptr);
    }
}

// ============================================================
// timerCallback — Linux 实现
// ============================================================
void FlutterEmbedder::timerCallback()
{
    if (!engineImpl) return;

    // 如果引擎已创建但尚未附加 view，尝试重试
    if (!engineRunning && engineImpl->view)
    {
        attachFlutterViewToHost();
        return;
    }

    if (!engineRunning) return;

    // 如果 view 已附加，同步布局
    if (engineImpl->viewAttached)
        syncFlutterViewBounds();

    // GTK main loop 由宿主应用负责驱动（DAW/Standalone），
    // 这里只需要处理待处理的 GTK 事件
    while (g_main_context_pending(g_main_context_default()))
        g_main_context_iteration(g_main_context_default(), FALSE);
}

// ============================================================
// resized — Linux 实现
// ============================================================
void FlutterEmbedder::resized()
{
    fallbackLabel.setBounds(getLocalBounds());
    if (engineRunning && engineImpl && engineImpl->viewAttached)
        syncFlutterViewBounds();
}

// ============================================================
// 构造函数 — Linux 实现（在 EngineImpl 定义之后 make_unique）
// ============================================================
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

// ============================================================
// 析构函数 — Linux 实现
// ============================================================
FlutterEmbedder::~FlutterEmbedder()
{
    stopTimer();
    shutdownEngine();
    juce::ScopedLock lock(s_instancesLock);
    const auto it = std::find(s_liveInstances.begin(), s_liveInstances.end(), this);
    if (it != s_liveInstances.end())
        s_liveInstances.erase(it);
}

#endif // FLUTTER_ENGINE_ENABLED && __linux__
