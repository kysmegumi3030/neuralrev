// ============================================================
// FlutterEmbedder_win.cpp
// Windows C++ 实现：使用 FlutterDesktopViewControllerRef 嵌入
// Flutter view 到 JUCE Component 的 HWND 内
// ============================================================

#include "FlutterEmbedder.h"

#if FLUTTER_ENGINE_ENABLED && defined(_WIN32)

#include <flutter_windows.h>
#include <juce_core/juce_core.h>
#include <commctrl.h>
#include <cmath>
#include <delayimp.h>
#include <algorithm>

#pragma comment(lib, "comctl32.lib")

// ============================================================
// 引擎 DLL 的「物理文件名」（可被每个插件唯一化）
//
// 关键问题：Windows 加载器按「DLL 基名」查找进程内已加载模块，
// 若同名模块已存在，LoadLibrary 直接返回它并忽略传入路径。
// 两个插件若都随包携带同名的 flutter_windows.dll，第二个插件会
// 复用第一个插件已加载的引擎模块（及其已 Dart_Initialize 的 VM），
// 从而显示第一个插件的 UI。
//
// 解决：让每个插件把引擎 DLL 复制成唯一文件名（由 CMake 传入
// FLUTTER_ENGINE_DLL_NAME 宏），使两个插件加载到彼此独立的模块实例、
// 各自持有独立的 Dart VM，实现完全隔离。
//
// 注意：延迟加载导入表中的名字仍是 "flutter_windows.dll"（由导入库
// flutter_windows.dll.lib 决定），因此下方钩子按该「导入名」匹配，
// 但实际加载的是 FLUTTER_ENGINE_DLL_NAME 指定的唯一文件。
// ============================================================
#ifndef FLUTTER_ENGINE_DLL_NAME
    #define FLUTTER_ENGINE_DLL_NAME "flutter_windows.dll"
#endif
#define FL_WIDEN_2(x) L##x
#define FL_WIDEN(x)   FL_WIDEN_2(x)
#define FLUTTER_ENGINE_DLL_NAME_W FL_WIDEN(FLUTTER_ENGINE_DLL_NAME)

// 延迟加载导入表中记录的引擎导入名（固定，不随插件唯一化而改变）
static constexpr const char* kFlutterImportName = "flutter_windows.dll";

// ============================================================
// 引擎 DLL 句柄缓存
// 由 ensureFlutterEngineDllLoaded() 写入，由延迟加载钩子读取。
// 静态文件作用域，每个插件 DLL 独立持有，不跨插件共享。
// ============================================================
static HMODULE g_flutterWindowsDllHandle = nullptr;

// ============================================================
// Windows 专用工具函数
// ============================================================
namespace {

bool ensureFlutterEngineDllLoaded()
{
    HMODULE thisModule = nullptr;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(&ensureFlutterEngineDllLoaded),
                           &thisModule) == 0 || thisModule == nullptr)
        return false;

    wchar_t modulePath[MAX_PATH] = {};
    const auto len = GetModuleFileNameW(thisModule, modulePath, MAX_PATH);
    if (len == 0 || len >= MAX_PATH)
        return false;

    juce::File moduleFile { juce::String(modulePath) };
    const auto dllPath = moduleFile.getParentDirectory().getChildFile(FLUTTER_ENGINE_DLL_NAME);
    if (!dllPath.existsAsFile())
        return false;

    auto* loaded = LoadLibraryExW(dllPath.getFullPathName().toWideCharPointer(),
                                  nullptr,
                                  LOAD_WITH_ALTERED_SEARCH_PATH);
    if (loaded)
        g_flutterWindowsDllHandle = loaded; // 缓存句柄供延迟加载钩子使用
    return loaded != nullptr;
}

void attachFlutterViewToOwnerWindow(FlutterDesktopViewRef view, HWND ownerWindow)
{
    if (!view || !ownerWindow) return;
    auto* flutterHwnd = FlutterDesktopViewGetHWND(view);
    if (!flutterHwnd) return;
    // 父窗口已经正确时立即返回，避免每帧调用 SetWindowLongPtr(GWL_STYLE)。
    // SetWindowLongPtr 会触发 WM_STYLECHANGED，Flutter 引擎响应时会
    // 重新评估布局，每帧触发必然导致周期性 ~100ms 掉帧。
    if (GetParent(flutterHwnd) == ownerWindow)
        return;
    SetParent(flutterHwnd, ownerWindow);
    auto style = GetWindowLongPtr(flutterHwnd, GWL_STYLE);
    style |= WS_CHILD;
    style &= ~WS_POPUP;
    SetWindowLongPtr(flutterHwnd, GWL_STYLE, style);
}

struct PendingFlutterBounds
{
    int x { 0 };
    int y { 0 };
    int width { 1 };
    int height { 1 };
};

bool getFlutterViewTargetBounds(juce::Component& host,
                                HWND ownerWindow,
                                PendingFlutterBounds& targetBounds)
{
    if (!ownerWindow)
        return false;

    POINT origin = {};
    auto globalOrigin = host.localPointToGlobal(juce::Point<int>(0, 0));
    origin.x = globalOrigin.x;
    origin.y = globalOrigin.y;
    ScreenToClient(ownerWindow, &origin);

    const auto bounds = host.getLocalBounds();
    const auto scale = juce::Component::getApproximateScaleFactorForComponent(&host);

    targetBounds.x = origin.x;
    targetBounds.y = origin.y;
    targetBounds.width = juce::jmax(1, (int) std::ceil(bounds.getWidth() * (double) scale));
    targetBounds.height = juce::jmax(1, (int) std::ceil(bounds.getHeight() * (double) scale));
    return true;
}

bool syncFlutterViewBoundsWin(juce::Component& host,
                               FlutterDesktopViewRef view,
                               FlutterDesktopViewControllerRef controller,
                               HWND ownerWindow)
{
    if (!view || !ownerWindow) return false;
    auto* flutterHwnd = FlutterDesktopViewGetHWND(view);
    if (!flutterHwnd) return false;

    attachFlutterViewToOwnerWindow(view, ownerWindow);

    PendingFlutterBounds targetBounds;
    if (!getFlutterViewTargetBounds(host, ownerWindow, targetBounds))
        return false;

    RECT cur = {};
    GetWindowRect(flutterHwnd, &cur);
    MapWindowPoints(HWND_DESKTOP, ownerWindow, (LPPOINT)&cur, 2);

    if (cur.left != targetBounds.x || cur.top != targetBounds.y
        || (cur.right - cur.left) != targetBounds.width
        || (cur.bottom - cur.top) != targetBounds.height)
    {
        SetWindowPos(flutterHwnd, nullptr,
                     targetBounds.x, targetBounds.y,
                     targetBounds.width, targetBounds.height,
                     SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOCOPYBITS);

        // Release 引擎不主动重绘，HWND 变化时始终触发 ForceRedraw 确保填满新区域
        if (controller)
            FlutterDesktopViewControllerForceRedraw(controller);

        InvalidateRect(flutterHwnd, nullptr, FALSE);
    }

    return true;
}

} // namespace

// ============================================================
// Flutter HWND 子类化 — 让 DAW 的 IsDialogMessage 放行所有按键
//
// 症状：VST3 宿主中，鼠标松开后普通 ASCII 键无法输入（伴随系统提示音），
// 但 IME 合成字符可以，且按住鼠标左键时一切正常。
//
// 根因：不是焦点问题（IME 能工作说明焦点在 Flutter）。而是 DAW 的消息
// 循环使用 IsDialogMessage() 处理键盘。IsDialogMessage 向焦点窗口发送
// WM_GETDLGCODE 询问它想要哪些键；Flutter 的输入子窗口默认返回 0，于是
// IsDialogMessage 把字符/方向键/Tab 当作对话框导航吞掉（并 MessageBeep）。
// IME 合成结果消息不经过此路径，故能通过；鼠标 capture 期间 DAW 走另一条
// 分发路径，也能通过。
//
// 解决：子类化 Flutter HWND 及其所有子窗口（焦点实际落在子窗口上），
// 拦截 WM_GETDLGCODE 返回 DLGC_WANTALLKEYS 等，告诉 DAW「所有按键都
// 交给我，别拦截」。仅处理这一个消息，其余全部 DefSubclassProc，
// 因此不破坏 Flutter 引擎自身的键盘/焦点处理。
// ============================================================
namespace {
    constexpr UINT_PTR kWantAllKeysSubclassId = 0x57414B59; // 'WAKY'

    LRESULT CALLBACK wantAllKeysProc(
        HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam,
        UINT_PTR /*uIdSubclass*/, DWORD_PTR /*dwRefData*/)
    {
        if (msg == WM_GETDLGCODE)
            return DLGC_WANTALLKEYS | DLGC_WANTCHARS | DLGC_WANTARROWS | DLGC_WANTTAB;
        return DefSubclassProc(hwnd, msg, wParam, lParam);
    }

    BOOL CALLBACK applyWantAllKeysToChild(HWND child, LPARAM)
    {
        SetWindowSubclass(child, wantAllKeysProc, kWantAllKeysSubclassId, 0);
        return TRUE;
    }

    // 对 Flutter HWND 及其整棵子树安装 WM_GETDLGCODE 钩子。
    void installWantAllKeys(HWND flutterHwnd)
    {
        if (!flutterHwnd) return;
        SetWindowSubclass(flutterHwnd, wantAllKeysProc, kWantAllKeysSubclassId, 0);
        EnumChildWindows(flutterHwnd, applyWantAllKeysToChild, 0);
    }

    BOOL CALLBACK removeWantAllKeysFromChild(HWND child, LPARAM)
    {
        RemoveWindowSubclass(child, wantAllKeysProc, kWantAllKeysSubclassId);
        return TRUE;
    }

    void uninstallWantAllKeys(HWND flutterHwnd)
    {
        if (!flutterHwnd) return;
        EnumChildWindows(flutterHwnd, removeWantAllKeysFromChild, 0);
        RemoveWindowSubclass(flutterHwnd, wantAllKeysProc, kWantAllKeysSubclassId);
    }

    // ========================================================
    // Cubase / Nuendo 键盘输入修复 — 线程级 WH_GETMESSAGE 钩子
    //
    // 症状（Cubase 特有，Ableton/Reaper 无）：编辑旋钮时普通字母键会
    // 优先触发 Cubase 的全局快捷键命令，只有未绑定快捷键的键才漏到
    // Flutter；IME 中文仍可输入。
    //
    // 根因：Cubase 是 Steinberg 自研宿主，其消息泵在 GetMessage 之后、
    // DispatchMessage 之前调用自己的加速键/键命令预处理（TranslateAccelerator
    // 类逻辑），在键盘消息到达焦点子窗口之前就把它拿去匹配快捷键。这一步
    // 与焦点窗口是否想要键无关，故 WM_GETDLGCODE（只影响 IsDialogMessage）
    // 拦不住它。IME 合成消息不走加速键路径，故能通过。
    //
    // 解决：在编辑器所在线程安装 WH_GETMESSAGE 钩子。GetMessage 取出键盘
    // 消息时，钩子先于 Cubase 的加速键预处理运行：若该消息目标窗口在
    // Flutter HWND 子树内，就自己 TranslateMessage+DispatchMessage 派发给
    // Flutter，然后把消息抹成 WM_NULL，使 Cubase 后续只看到空消息、不再
    // 当快捷键吞掉。焦点不在插件内时（msg->hwnd 不在子树），原样放行，
    // 不影响宿主自身的快捷键。
    // ========================================================
    HHOOK g_cubaseKeyHook = nullptr;
    HWND  g_cubaseKeyHookFlutterHwnd = nullptr;

    bool isKeyboardMessage(UINT m)
    {
        switch (m)
        {
            case WM_KEYDOWN:  case WM_KEYUP:
            case WM_CHAR:     case WM_DEADCHAR:
            case WM_SYSKEYDOWN: case WM_SYSKEYUP:
            case WM_SYSCHAR:  case WM_SYSDEADCHAR:
                return true;
            default:
                return false;
        }
    }

    LRESULT CALLBACK cubaseKeyHookProc(int code, WPARAM wParam, LPARAM lParam)
    {
        if (code == HC_ACTION && wParam == PM_REMOVE)
        {
            auto* msg = reinterpret_cast<MSG*>(lParam);
            HWND fl = g_cubaseKeyHookFlutterHwnd;
            if (msg && fl && ::IsWindow(fl) && isKeyboardMessage(msg->message))
            {
                HWND target = msg->hwnd;
                if (target && (target == fl || ::IsChild(fl, target)))
                {
                    // 抢在 Cubase 加速键预处理之前，自己派发给 Flutter 窗口。
                    ::TranslateMessage(msg);
                    ::DispatchMessage(msg);
                    // 抹成空消息：Cubase 的键命令处理拿到 WM_NULL，不再吞键；
                    // 也避免宿主再次 DispatchMessage 造成重复输入。
                    msg->message = WM_NULL;
                    msg->wParam  = 0;
                    msg->lParam  = 0;
                }
            }
        }
        return CallNextHookEx(g_cubaseKeyHook, code, wParam, lParam);
    }

    void installCubaseKeyHook(HWND flutterHwnd)
    {
        if (!flutterHwnd) return;
        g_cubaseKeyHookFlutterHwnd = flutterHwnd;
        if (g_cubaseKeyHook) return; // 本线程已安装
        g_cubaseKeyHook = SetWindowsHookExW(
            WH_GETMESSAGE, cubaseKeyHookProc, nullptr, GetCurrentThreadId());
    }

    void uninstallCubaseKeyHook()
    {
        if (g_cubaseKeyHook)
        {
            UnhookWindowsHookEx(g_cubaseKeyHook);
            g_cubaseKeyHook = nullptr;
        }
        g_cubaseKeyHookFlutterHwnd = nullptr;
    }
} // namespace

// ============================================================
// 延迟加载通知钩子 — 将 flutter_windows.dll 重定向到插件自身目录
//
// 问题根因：DAW 宿主加载 VST3 时，Windows DLL 搜索路径以宿主的启动目录
// 为基准，不包含 .vst3 包内的 Contents/x86_64-win/ 目录。
// 延迟加载存根调用 LoadLibraryW("flutter_windows.dll") 时使用的是
// 纯名称（无路径），而 ensureFlutterEngineDllLoaded() 以完整路径预加载
// 的模块在 Windows 的模块表中以完整路径为键存储，纯名称查找不会命中它，
// 导致存根在搜索失败后抛出 0xC06D007E 异常。
//
// 修复方案：通过 __pfnDliNotifyHook2 在存根查找之前拦截，直接提供
// 从插件目录加载的正确句柄，完全绕过标准搜索路径。
//
// 注：__pfnDliNotifyHook2 在 delayimp.lib 中以弱符号定义（初值 nullptr），
// 此处强定义将其覆盖；作用域限于本 DLL，不影响同进程中其他插件。
// ============================================================
static FARPROC WINAPI flutterDelayLoadHook(unsigned dliNotify,
                                           PDelayLoadInfo pdli) noexcept
{
    // 只处理「即将加载库」通知。
    // pdli->szDll 是导入表里记录的名字（固定为 flutter_windows.dll），
    // 与实际磁盘上的唯一文件名 FLUTTER_ENGINE_DLL_NAME 无关。
    if (dliNotify != dliNotePreLoadLibrary)
        return nullptr;
    if (!pdli || _stricmp(pdli->szDll, kFlutterImportName) != 0)
        return nullptr;

    // 优先返回 ensureFlutterEngineDllLoaded() 已预加载的句柄
    if (g_flutterWindowsDllHandle)
        return reinterpret_cast<FARPROC>(g_flutterWindowsDllHandle);

    // 回退路径：钩子在预加载之前被触发时，自行从插件目录加载
    HMODULE thisModule = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCWSTR>(&flutterDelayLoadHook),
                            &thisModule) || !thisModule)
        return nullptr;

    wchar_t buf[4096];
    constexpr DWORD kBufSize = static_cast<DWORD>(sizeof(buf) / sizeof(buf[0]));
    const DWORD len = GetModuleFileNameW(thisModule, buf, kBufSize);
    if (len == 0 || len >= kBufSize)
        return nullptr;

    // 截取目录部分（保留末尾反斜杠）
    wchar_t* lastSlash = wcsrchr(buf, L'\\');
    if (!lastSlash) return nullptr;
    const DWORD dirLen = static_cast<DWORD>(lastSlash - buf) + 1;

    // 拼接「唯一化」的引擎文件名，检查缓冲区是否足够。
    // 加载唯一文件名可避免与其他插件的 flutter_windows.dll 基名冲突。
    constexpr wchar_t kDllName[] = FLUTTER_ENGINE_DLL_NAME_W;
    constexpr DWORD kDllNameLen = static_cast<DWORD>(sizeof(kDllName) / sizeof(kDllName[0]) - 1);
    if (dirLen + kDllNameLen >= kBufSize)
        return nullptr;

    wmemcpy(buf + dirLen, kDllName, kDllNameLen + 1); // +1 包含 null 终止符

    HMODULE loaded = LoadLibraryExW(buf, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (loaded)
        g_flutterWindowsDllHandle = loaded;
    return reinterpret_cast<FARPROC>(loaded);
}

extern "C" const PfnDliHook __pfnDliNotifyHook2 = flutterDelayLoadHook;

// 将单个通道的 Flutter→C++ 消息回调绑定到 FlutterEmbedder::handlePlatformMessage。
// 封装为独立函数，供 registerMethodHandler 和 attachFlutterViewToHost 共用，
// 避免重复写同一个 lambda。
static void registerChannelCallback(FlutterDesktopMessengerRef messenger,
                                     const std::string& channel,
                                     FlutterEmbedder* self)
{
    FlutterDesktopMessengerSetCallback(
        messenger, channel.c_str(),
        [](FlutterDesktopMessengerRef msgr,
           const FlutterDesktopMessage* msg,
           void* userData)
        {
            auto* embedder = static_cast<FlutterEmbedder*>(userData);
            // string_view 直接指向 Flutter 展示的消息缓冲区，在回调内部有效。
            // handlePlatformMessage 及其内部的所有 handler 均在此同步执行，
            // 完成后才调用 SendResponse，故安全。
            const std::string_view payload(
                reinterpret_cast<const char*>(msg->message), msg->message_size);
            auto result = embedder->handlePlatformMessage(msg->channel, payload);
            if (msg->response_handle)
            {
                FlutterDesktopMessengerSendResponse(
                    msgr, msg->response_handle,
                    reinterpret_cast<const uint8_t*>(result.data()),
                    result.size());
            }
        },
        self);
}

// ============================================================
// Windows 专用 EngineImpl
// ============================================================
struct FlutterEmbedder::EngineImpl
{
    FlutterDesktopViewControllerRef  controller  { nullptr };
    FlutterDesktopEngineRef          engine      { nullptr };
    FlutterDesktopViewRef            view        { nullptr };
    FlutterDesktopPluginRegistrarRef registrar   { nullptr };
    bool viewBoundsSynced { false };
    bool boundsDirty { false };
    double lastResizeRequestMs { 0.0 };
    double lastBoundsApplyMs { 0.0 };
};

// ============================================================
// attachFlutterViewToHost — Windows：将 Flutter HWND 挂入 JUCE HWND
// ============================================================
void FlutterEmbedder::attachFlutterViewToHost()
{
    if (!engineImpl || !engineImpl->view) return;

    auto* ownerHwnd = static_cast<HWND>(getWindowHandle());
    if (!ownerHwnd) return;

    auto* flutterHwnd = FlutterDesktopViewGetHWND(engineImpl->view);
    if (!flutterHwnd) return;

    // 将 Flutter HWND 设为 JUCE HWND 的子窗口
    SetParent(flutterHwnd, ownerHwnd);
    auto style = GetWindowLongPtr(flutterHwnd, GWL_STYLE);
    style |= WS_CHILD;
    style &= ~WS_POPUP;
    SetWindowLongPtr(flutterHwnd, GWL_STYLE, style);

    // 设置初始位置和尺寸，同步显示
    syncFlutterViewBoundsWin(*this, engineImpl->view, engineImpl->controller, ownerHwnd);
    ShowWindow(flutterHwnd, SW_SHOW);

    engineRunning = true;

    // 为 Flutter HWND 及其子树安装 WM_GETDLGCODE 钩子，
    // 让 DAW 的 IsDialogMessage 放行所有按键（修复 ASCII 无法输入）。
    installWantAllKeys(flutterHwnd);

    // 为 Cubase/Nuendo 安装线程级键盘钩子，抢在它的加速键
    // 预处理之前把按键派发给 Flutter（修复字母键被当快捷键吞掉）。
    installCubaseKeyHook(flutterHwnd);

    // 补注册所有在 engine attach 之前就已存入 methodHandlers 的通道回调。
    // 场景：Processor 预热启动引擎，此时 Editor 还未就绪，
    // paramBridge->initialize() 在 engineRunning=false 时被调用，
    // registerMethodHandler 将 handler 存入了 map 但跳过了 FlutterDesktopMessengerSetCallback。
    // 这里补積，确保 Flutter→C++ 消息能被接收。
    if (engineImpl->registrar)
    {
        auto* messenger = FlutterDesktopPluginRegistrarGetMessenger(engineImpl->registrar);
        for (const auto& pair : methodHandlers)
            registerChannelCallback(messenger, pair.first, this);
    }

    engineImpl->viewBoundsSynced = true;
    engineImpl->boundsDirty = false;
    engineImpl->lastBoundsApplyMs = juce::Time::getMillisecondCounterHiRes();
    repaint();
    FLUTTER_LOG("[FlutterEmbedder] Flutter HWND attached to JUCE peer HWND");

    // 通知外部 Engine 已就绪（e.g. PluginEditor 可立即同步参数到 Flutter）
    if (onEngineAttached)
        onEngineAttached();
}

// ============================================================
// syncFlutterViewBounds — Windows 尺寸同步（包装成员方法）
// ============================================================
void FlutterEmbedder::syncFlutterViewBounds()
{
    if (!engineImpl || !engineImpl->view) return;
    auto* ownerHwnd = static_cast<HWND>(getWindowHandle());
    if (!ownerHwnd) return;
    syncFlutterViewBoundsWin(*this, engineImpl->view, engineImpl->controller, ownerHwnd);
}

// ============================================================
// detachFromParent — Windows：把 Flutter HWND 从父窗口摘下，但保留 Engine
// ============================================================
void FlutterEmbedder::detachFromParent()
{
    if (!engineImpl || !engineImpl->view) return;
    auto* flutterHwnd = FlutterDesktopViewGetHWND(engineImpl->view);
    if (!flutterHwnd) return;

    // 先移除 WM_GETDLGCODE 钩子，再隐藏/解挂
    uninstallWantAllKeys(flutterHwnd);

    // 移除 Cubase 键盘钩子
    uninstallCubaseKeyHook();

    // 隐藏再解除父子关系
    ShowWindow(flutterHwnd, SW_HIDE);
    SetParent(flutterHwnd, nullptr);
    auto style = GetWindowLongPtr(flutterHwnd, GWL_STYLE);
    style &= ~WS_CHILD;
    style |= WS_POPUP;
    SetWindowLongPtr(flutterHwnd, GWL_STYLE, style);

    engineRunning = false;
    FLUTTER_LOG("[FlutterEmbedder] Flutter HWND detached from parent");
}

// ============================================================
// reattachToParent — Windows：Editor 打开时重新挂载
// ============================================================
void FlutterEmbedder::reattachToParent()
{
    if (!engineImpl || !engineImpl->view) return;

    // 如果有 HWND（peer 已就绪），立即 attach
    if (getWindowHandle())
    {
        attachFlutterViewToHost();
        return;
    }

    // peer 还未就绪，timer 会持续重试
    if (!isTimerRunning())
        startTimerHz(60);
}

// ============================================================
// initialize — Windows 实现
// ============================================================
bool FlutterEmbedder::initialize()
{
    if (!ensureFlutterEngineDllLoaded())
    {
        FLUTTER_LOG("[FlutterEmbedder] Failed to load flutter_windows.dll from plugin directory");
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    if (!assetsDir.isDirectory())
    {
        FLUTTER_LOG("[FlutterEmbedder] Flutter assets directory not found: "
            + assetsDir.getFullPathName());
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    const auto assetsPath = assetsDir.getFullPathName();
    const auto icuDataPath = assetsDir.getParentDirectory()
                                 .getChildFile("icudtl.dat")
                                 .getFullPathName();

    // Release 引擎需要 AOT 编译的 Dart 代码（Flutter 3.22+ 在 Windows 上命名为 app.so），
    // 位于 flutter_assets 同级目录。JIT 引擎（Debug 构建）忽略此字段（设为 nullptr）
    const auto aotLib    = assetsDir.getParentDirectory().getChildFile("app.so");
    const auto aotLibStr = aotLib.existsAsFile() ? aotLib.getFullPathName() : juce::String{};

    FLUTTER_LOG("[FlutterEmbedder] assetsDir: " + assetsDir.getFullPathName());
    FLUTTER_LOG("[FlutterEmbedder] icuDataPath: " + icuDataPath);
    if (!aotLibStr.isEmpty())
        FLUTTER_LOG("[FlutterEmbedder] AOT library found: " + aotLibStr);
#if FLUTTER_USING_RELEASE_ENGINE
    else
        FLUTTER_LOG("[FlutterEmbedder] WARNING: Release engine requires app.so alongside the plugin. "
            "Run 'flutter build windows --release|--profile' in flutter_ui/ first.");
#endif

    FlutterDesktopEngineProperties props = {};
    props.assets_path      = assetsPath.toWideCharPointer();
    props.icu_data_path    = icuDataPath.toWideCharPointer();
    props.aot_library_path = aotLibStr.isEmpty() ? nullptr : aotLibStr.toWideCharPointer();

    FLUTTER_LOG("[FlutterEmbedder] FlutterDesktopEngineCreate...");
    engineImpl->engine = FlutterDesktopEngineCreate(&props);
    if (!engineImpl->engine)
    {
        FLUTTER_LOG("[FlutterEmbedder] ERROR: FlutterDesktopEngineCreate returned null");
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    const auto bounds = getLocalBounds();
    const int w = juce::jmax(1, bounds.getWidth());
    const int h = juce::jmax(1, bounds.getHeight());

    FLUTTER_LOG("[FlutterEmbedder] FlutterDesktopViewControllerCreate "
        + juce::String(w) + "x" + juce::String(h) + "...");
    engineImpl->controller = FlutterDesktopViewControllerCreate(w, h, engineImpl->engine);
    if (!engineImpl->controller)
    {
        FLUTTER_LOG("[FlutterEmbedder] ERROR: FlutterDesktopViewControllerCreate returned null");
        engineImpl->engine = nullptr;
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    engineImpl->engine    = FlutterDesktopViewControllerGetEngine(engineImpl->controller);
    engineImpl->view      = FlutterDesktopViewControllerGetView(engineImpl->controller);
    engineImpl->registrar = FlutterDesktopEngineGetPluginRegistrar(
                                engineImpl->engine, "audio_bridge");

    // 若 peer（HWND）已就绪，立即 attach；否则 timer 会重试
    if (getWindowHandle())
        attachFlutterViewToHost();
    else
        startTimerHz(60);

    FLUTTER_LOG("[FlutterEmbedder] Flutter Engine started on Windows");
    return true;
}

// ============================================================
// shutdownEngine — Windows 实现
// ============================================================
void FlutterEmbedder::shutdownEngine()
{
    stopTimer();
    if (engineImpl)
    {
        if (engineImpl->controller)
        {
            FlutterDesktopViewControllerDestroy(engineImpl->controller);
            engineImpl->controller = nullptr;
            engineImpl->engine     = nullptr;
            engineImpl->view       = nullptr;
            engineImpl->registrar  = nullptr;
        }
        else if (engineImpl->engine)
        {
            FlutterDesktopEngineDestroy(engineImpl->engine);
            engineImpl->engine = nullptr;
        }
    }
    engineRunning = false;
}

// ============================================================
// sendMessage — Windows 实现
// ============================================================
void FlutterEmbedder::sendMessage(std::string_view channel,
                                   std::string_view method,
                                   std::string_view argsJson)
{
    if (!engineRunning || !engineImpl || !engineImpl->registrar) return;

    // 在栈上构造信封 {"method":"<m>","args":<a>}，不产生堆分配。
    // 典型消息长度：方法名 ~20 + argsJson ~256 ＜总 512 字节。
    char buf[1024];
    const int n = std::snprintf(buf, sizeof(buf),
        "{\"method\":\"%.*s\",\"args\":%.*s}",
        static_cast<int>(method.size()),  method.data(),
        static_cast<int>(argsJson.size()), argsJson.data());

    auto* messenger = FlutterDesktopPluginRegistrarGetMessenger(engineImpl->registrar);
    if (n > 0 && n < static_cast<int>(sizeof(buf)))
    {
        FlutterDesktopMessengerSend(messenger, channel.data(),
            reinterpret_cast<const uint8_t*>(buf),
            static_cast<size_t>(n));
    }
    else
    {
        // 超大消息（极少出现）：退化为堆分配路径
        std::string payload;
        payload.reserve(24 + method.size() + argsJson.size());
        payload  = "{\"method\":\"";
        payload.append(method.data(), method.size());
        payload += "\",\"args\":";
        payload.append(argsJson.data(), argsJson.size());
        payload += '}';
        FlutterDesktopMessengerSend(messenger, channel.data(),
            reinterpret_cast<const uint8_t*>(payload.data()),
            payload.size());
    }
}

// ============================================================
// registerMethodHandler — Windows 实现
// ============================================================
void FlutterEmbedder::registerMethodHandler(std::string_view channel,
                                             MethodCallback callback)
{
    // insert_or_assign 确保旧 lambda 被新的替换，
    // 防止重开 Editor 时 emplace 静默保留悬垂指针。
    auto [it, _] = methodHandlers.insert_or_assign(std::string(channel), std::move(callback));
    if (!engineRunning || !engineImpl || !engineImpl->registrar) return;

    auto* messenger = FlutterDesktopPluginRegistrarGetMessenger(engineImpl->registrar);
    registerChannelCallback(messenger, it->first, this);
}

// ============================================================
// unregisterMethodHandler — Windows 实现
// ============================================================
void FlutterEmbedder::unregisterMethodHandler(std::string_view channel)
{
    methodHandlers.erase(std::string(channel));
    // FlutterDesktopMessengerSetCallback 没有"取消注册"的 API；
    // 将 callback 设为空 lambda，使进来的消息静默忽略。
    if (engineRunning && engineImpl && engineImpl->registrar)
    {
        auto* messenger = FlutterDesktopPluginRegistrarGetMessenger(engineImpl->registrar);
        FlutterDesktopMessengerSetCallback(messenger, std::string(channel).c_str(),
            nullptr, nullptr);
    }
}

// ============================================================
// timerCallback — Windows 实现
// ============================================================
void FlutterEmbedder::timerCallback()
{
    if (!engineImpl || !engineImpl->engine) return;

    constexpr double resizeSettleDelayMs = 120.0;

    FlutterDesktopEngineProcessMessages(engineImpl->engine);

    // DAW（VST3 宿主）在鼠标松开时通常会抜走键盘焦点还给宿主主窗口。
    // Flutter HWND 作为子窗口，若失去 Win32 焦点则无法接收键盘事件。
    // 此处每帧检查：只要焦点落在本插件窗口内（自身或子孙），一律
    // 定向到 Flutter HWND，确保 TextField/旋钮编辑等 Flutter 侧
    // 键盘输入始终可用。
    if (engineRunning && engineImpl->view)
    {
        auto* flutterHwnd = FlutterDesktopViewGetHWND(engineImpl->view);
        auto* ownerHwnd   = static_cast<HWND>(getWindowHandle());
        auto* focused     = ::GetFocus();
        if (flutterHwnd && ownerHwnd && focused != flutterHwnd)
        {
            if (focused == ownerHwnd || ::IsChild(ownerHwnd, focused))
                ::SetFocus(flutterHwnd);
        }
    }

    // 若 Engine 已创建但尚未 attach（Processor 里预启动、peer 还未就绪）
    if (!engineRunning && engineImpl->view)
    {
        if (getWindowHandle())
            attachFlutterViewToHost();
        return;
    }

    if (!engineRunning) return;

    const auto nowMs = juce::Time::getMillisecondCounterHiRes();
    const bool resizeSettled = engineImpl->boundsDirty
        && (nowMs - engineImpl->lastResizeRequestMs) >= resizeSettleDelayMs;

    // 每帧都同步 HWND 位置和尺寸：
    //   - 处理宿主 Standalone 弹出提示栏时 editor 只移动不改变尺寸的场景
    //     （此时 boundsDirty 不会被置位，但 localPointToGlobal 已反映新位置）
    //   - syncFlutterViewBoundsWin 内部比较新旧 RECT，仅在实际变化时调用 SetWindowPos
    auto* ownerHwnd = static_cast<HWND>(getWindowHandle());
    if (ownerHwnd)
    {
        syncFlutterViewBoundsWin(*this,
                                 engineImpl->view,
                                 engineImpl->controller,
                                 ownerHwnd);
    }

    if (resizeSettled)
    {
        engineImpl->lastBoundsApplyMs = nowMs;
        engineImpl->boundsDirty = false;
    }
}

// ============================================================
// resized — Windows 实现
// ============================================================
void FlutterEmbedder::resized()
{
    fallbackLabel.setBounds(getLocalBounds());

    if (engineImpl && engineImpl->view)
    {
        engineImpl->boundsDirty = true;
        engineImpl->lastResizeRequestMs = juce::Time::getMillisecondCounterHiRes();
    }
}

// ============================================================
// 构造函数 — Windows 实现（在 EngineImpl 定义之后 make_unique）
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
// 析构函数 — Windows 实现
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

#endif // FLUTTER_ENGINE_ENABLED && _WIN32

