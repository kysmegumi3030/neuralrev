// ============================================================
// FlutterEmbedder_mac.mm
// macOS Objective-C++ 实现：将 FlutterViewController 的 NSView
// 嵌入到 JUCE Component 的 NSView 层级内
// ============================================================

#include "FlutterEmbedder.h"

#if FLUTTER_ENGINE_ENABLED && defined(__APPLE__)

#import <FlutterMacOS/FlutterMacOS.h>
#import <Cocoa/Cocoa.h>
#include <dlfcn.h>
#include <juce_gui_basics/juce_gui_basics.h>
#include <algorithm>
#include <mutex>
#include "FlutterEnginePrewarmer.h"

// ============================================================
// 诊断日志开关（默认关闭）
// ------------------------------------------------------------
// 本子系统的宿主相关竞态（teardown 时序 / GPU surface / merged run loop）只能
// 手动复现调试，故保留一套写入 /tmp/juce_flutter_init.log 的诊断埋点，但默认
// 编译期关闭——出货构建不写文件、零开销。需要现场排查时，构建加
// -DJF_FLUTTER_DIAG=1 即可打开全部埋点（initialize 各阶段、attach、首帧 reveal、
// STUCK-BLACK、parentHierarchyChanged 等）。
// ============================================================
#ifndef JF_FLUTTER_DIAG
 #define JF_FLUTTER_DIAG 0
#endif

static inline void jfDiagLog(const juce::String& msg)
{
#if JF_FLUTTER_DIAG
    juce::File("/tmp/juce_flutter_init.log")
        .appendText(juce::Time::getCurrentTime().toString(true, true) + "  " + msg + "\n");
#else
    juce::ignoreUnused(msg);
#endif
}

// ============================================================
// macOS 专用 EngineImpl 扩展
// 在 FlutterEmbedder.h 中已声明前向声明 struct EngineImpl
// ============================================================
struct FlutterEmbedder::EngineImpl
{
    // 常驻 headless engine：跨 Editor 开/关存活，只在插件实例销毁（~FlutterEmbedder）
    // 时才 shutDownEngine。它拥有进程级 FlutterRunLoop 上的平台消息回调
    // （engineCallbackOnPlatformMessage:）——常驻即保证延迟消息永不落到已销毁的
    // engine 上，根除 deref-0x10 崩溃类。用 initWithName:allowHeadlessExecution:YES
    // 创建，故无 implicit VC，可反复 attach 新 VC。
    // ⚠️ 全部 ObjC 成员必须显式 __strong。本文件以 -fobjc-arc 编译，而 C++ struct
    //    的 ObjC 指针成员在 ARC 下默认 __unsafe_unretained（不 retain）。persistentEngine
    //    是独立创建的 headless engine，ARC 侧没有任何其他强引用持有它——若用裸指针，
    //    initialize() 返回、autorelease pool 排空后即被释放 → 后续 objc_msgSend 落到
    //    已释放 engine → EXC_BAD_ACCESS/PAC 失败（Stayer 加载即崩即此，间歇因内存是否
    //    被覆盖而定）。旧架构下 viewController 侥幸存活是因 attach 进 NSView 树被 retain，
    //    但 headless engine 无此保护，必悬垂。fixed 15b896a 只补了 coldStartPathCache，
    //    这里才是主因。
    __strong FlutterEngine*         persistentEngine { nil };

    __strong FlutterViewController* viewController { nil };
    __strong FlutterEngine*         engine         { nil };   // == persistentEngine（当前 attach 时），便于既有代码引用
    __strong NSView*                flutterView    { nil };
    __strong id                     resizeObserver { nil };
    bool                            firstFrameReceived { false };

    // 首帧检测改用 GCD 主队列自排轮询（见 attachFlutterViewToHost），无需成员状态。

    // 每次 attach 递增；供延迟 block（dispatch_after）判断自己是否已过期，
    // 避免重开/销毁 Editor 后陈旧 block 触碰新一代（或已失效）状态。
    uint64_t               attachGeneration { 0 };
    // 拆除进行中标志：排空 run loop 期间若有事件重入 shutdownEngine，直接返回。
    bool                   tearingDown { false };

    // [DIAG-VIS] "卡黑屏"检测节流计数：engine 在跑但 view 仍不可见时，
    //           每 ~60 帧（约 1s）记一次状态，避免刷屏。
    int                    stuckDiagTicks { 0 };
};

// 前向声明：钉住 Flutter 代码常驻（定义在 shutdownEngine 附近），供 initialize() 调用。
static void pinFrameworksResident();

// ============================================================
// 引擎生命周期串行化门（本 dylib 进程级）
// ------------------------------------------------------------
// 本引擎跑在 merged UI+platform thread 实验模式：FlutterRunLoop 是进程级
// 单例（绑定主线程），引擎为 implicit-view-only（无法一引擎多 view，无
// FlutterEngineGroup）。每个插件实例各自独立引擎，但多个引擎的「创建 /
// 销毁」若在同一主线程 run loop 上重叠，会争抢 lease 计数的线程合并器与
// 共享 CVDisplayLink → 宿主卡死（Ableton）/ 全黑传染（REAPER 多实例）。
//
// 该门把引擎生命周期变迁（cold-start 创建、engine 销毁）串行化：任意时刻
// 只允许一个变迁在跑。递归锁保证同实例 initialize()→shutdownEngine()
// 嵌套（幂等清理路径）不自锁。作用域是本 dylib——每种插件用唯一化的
// FlutterMacOS_<name>.framework，故各插件类型天然隔离，互不阻塞。
//
// 变迁几乎都在主线程发生，锁竞争窗口极短（仅覆盖 alloc/attach 与 park/
// 销毁调度，不覆盖运行期）。个别宿主在后台线程实例化插件时，此锁亦保证
// 与主线程上他实例的变迁不交叠。
static std::recursive_mutex& engineLifecycleGate()
{
    static std::recursive_mutex m;
    return m;
}

// ============================================================
// 冷启动路径解析：不缓存 ObjC 对象（曾用 dylib 级 static 缓存 NSBundle*/NSString*，
// 是 Stayer 加载即崩的真正根因）。
// ------------------------------------------------------------
// 教训：绝不能在 dylib 级 static 里缓存 ObjC 对象。宿主（REAPER/Ableton 扫描）
// 会 dlclose/reload 插件 bundle；跨 load 后，static 里 __strong 持有的 NSString/
// NSBundle 属于上一个 dylib 生命周期，其 backing 已失效 → 留下悬垂对象 →
// [appPath UTF8String] 等 objc_msgSend 落到坏 isa → EXC_BAD_ACCESS/PAC 失败
// （探针实测崩在 [P4b]→[P4c] 之间，即 appExe 缓存命中后对其发消息）。即便标注
// __strong 也救不了——问题不是没 retain，是对象所属的 dylib 已卸载。
//
// 修复：删除缓存，每次冷启动重新解析。省下的那点文件系统 I/O 不值得这个崩溃风险，
// 且 attach 只在开窗时发生（非热路径）。
// ============================================================

// ============================================================
// 为单个 channel 设置 FlutterBasicMessageChannel 消息回调
// 封装为独立函数，供 registerMethodHandler 和 attachFlutterViewToHost 共用
// （与 Windows 的 registerChannelCallback 对称）
// ============================================================
static void registerMacChannelCallback(FlutterEngine* engine,
                                        const std::string& chanStr,
                                        FlutterEmbedder* self)
{
    if (!engine || !engine.binaryMessenger) return;

    NSString* chName = [NSString stringWithUTF8String: chanStr.c_str()];
    FlutterBasicMessageChannel* msgCh =
        [FlutterBasicMessageChannel
            messageChannelWithName: chName
                   binaryMessenger: engine.binaryMessenger
                             codec: [FlutterStringCodec sharedInstance]];

    if (!msgCh) return;

    // 将 channel 名拷贝一份供 block 持有，防止 map 键的引用在 block 生命周期内失效
    std::string capturedChannel = chanStr;
    [msgCh setMessageHandler: ^(id message, FlutterReply reply) {
        std::string payloadStr;
        if ([message isKindOfClass: [NSString class]])
        {
            const char* utf8 = [(NSString*)message UTF8String];
            if (utf8 != nullptr)
                payloadStr = utf8;
        }
        // 使用 handlePlatformMessage 的返回值作为回包（bootstrap 握手等需要此响应）。
        // macOS 对应 Windows 的 FlutterDesktopMessengerSendResponse，必须回传响应，
        // 否则 Dart 侧 BasicMessageChannel.send() 将收到 null，bootstrap 握手永远失败。
        std::string responseStr = self->handlePlatformMessage(capturedChannel, payloadStr);
        if (reply)
        {
            if (!responseStr.empty())
            {
                NSString* nsResp = [NSString stringWithUTF8String: responseStr.c_str()];
                reply(nsResp);
            }
            else
            {
                reply(nil);
            }
        }
    }];
}

// ============================================================
// detachFromParent — Editor 关闭时只分离 VC，**常驻 engine 继续运行**
// ============================================================
void FlutterEmbedder::detachFromParent()
{
    // 关窗只 detach 当前 VC（其 surface 绑定在即将销毁的窗口，不可复用），
    // 但保留常驻 engine / Dart VM 运行。engine 存活是根治闪退的关键：
    // 进程级 FlutterRunLoop 上排队的延迟平台消息（engineCallbackOnPlatformMessage:）
    // 触发时，engine 始终在 → 不会 deref 已销毁对象（0x10 崩溃类）。
    // 重开时 initialize() 复用 engine、只新建 VC → 秒开。
    // engine 真正销毁只在 ~FlutterEmbedder（实例被宿主移除）时经 shutdownEngine()。
    detachViewController();
}

// ============================================================
// reattachToParent — Editor 打开时将已运行的 FlutterView 重新挂载
// 调用前需已 addAndMakeVisible(this) + setBounds
// ============================================================
void FlutterEmbedder::reattachToParent()
{
    if (!engineImpl || !engineImpl->flutterView)
        return;

    // reattachToParent 由 PluginEditor 在 addAndMakeVisible(this) 之后调用，
    // 此刻 JUCE peer 通常已就绪。直接尝试 attach——**不依赖 juce::Timer 兜底**。
    //
    // 为什么不能依赖 timer：持久引擎的 runWithEntrypoint 运行在 merged
    // UI+platform thread 实验模式，会把主线程 run loop 合并进引擎的
    // FlutterRunLoop。引擎创建后，挂在 JUCE vblank/CVDisplayLink 定时源上的
    // juce::Timer 可能不再被稳定泵动，导致 timerCallback 永不触发 → 第二次
    // 打开 / 第二个实例卡在加载页（peer 已就绪但没人来 attach）。故这里主动
    // attach；attachFlutterViewToHost 内部若 peer 仍 nil 会安全跳过，timer
    // 作为退化兜底仍保留。
    if (getPeer() != nullptr)
    {
        attachFlutterViewToHost();
        return;
    }

    // peer 尚未就绪：用 callAsync 轮询驱动 attach（不依赖 juce::Timer）。
    if (!isTimerRunning())
        startTimerHz(60);   // 退化兜底
    scheduleAttachPoll(0);
}

// ============================================================
// setPrewarmer — 接收 PluginProcessor 提前创建的 Engine
// ============================================================
void FlutterEmbedder::setPrewarmer(std::shared_ptr<FlutterEnginePrewarmer> pw)
{
    prewarmer = std::move(pw);
}

// ============================================================
// scheduleAttachPoll — 经 MessageManager::callAsync 自我重排的 attach 轮询
// ------------------------------------------------------------
// 替代不可靠的 juce::Timer（merged FlutterRunLoop 下 vblank 定时源可能不泵动）。
// callAsync 经主线程 CFRunLoop source 投递，正是 merged 引擎所泵的 run loop，
// 故一定被执行。每次检查 peer 是否就绪：就绪则 attach 并停；否则隔一小段再排，
// 直到 engineRunning、对象销毁、或达到重试上限（~5s 兜底，防止无限自排）。
// ============================================================
void FlutterEmbedder::scheduleAttachPoll(int attempt)
{
    static constexpr int kMaxAttempts = 300;   // ~5s（16ms/次）
    if (attempt >= kMaxAttempts)
    {
        jfDiagLog("[attach-poll] giving up after max attempts ns=" + juce::String(channelNamespace.c_str()));
        return;
    }

    juce::Component::SafePointer<FlutterEmbedder> safeThis(this);
    juce::MessageManager::callAsync([safeThis, attempt]()
    {
        auto* self = safeThis.getComponent();
        if (self == nullptr || !self->engineImpl)
            return;                              // 对象已销毁
        if (self->engineRunning)
            return;                              // 已 attach，停止轮询
        if (!self->engineImpl->flutterView)
            return;                              // 无 VC（已 detach），停止

        if (self->getPeer() != nullptr)
        {
            self->attachFlutterViewToHost();     // peer 就绪 → attach
            if (self->engineRunning)
                return;                          // 成功，停止
        }
        // peer 仍未就绪或 attach 未成功：延一帧再试。
        self->scheduleAttachPoll(attempt + 1);
    });
}

// ============================================================
// initialize — macOS 实现
// ============================================================
bool FlutterEmbedder::initialize()
{
    auto writeLog = [](const juce::String& msg) { jfDiagLog(msg); };
    writeLog("initialize() called");

    // 串行化引擎创建：与其他实例的 create/destroy 变迁互斥，避免在共享的
    // 进程级 run loop / 线程合并器上重叠（见 engineLifecycleGate 说明）。
    // 递归锁——本函数内部可能调用 shutdownEngine()（幂等重建路径），
    // 该函数会再次取同一把锁，递归语义保证不自锁。RAII 覆盖所有提前返回。
    std::lock_guard<std::recursive_mutex> gate(engineLifecycleGate());

    // 钉住 Flutter 代码 + 本插件二进制常驻，防止宿主（如 REAPER）unload 插件后
    // Flutter 引擎线程 / 延迟 block 执行到已 unmap 的代码页而崩溃。一次性生效。
    pinFrameworksResident();

    // --------------------------------------------------------
    // 已有 VC attach（重开 Editor 而上次未干净 detach，或幂等重入）：只 detach
    // 旧 VC，**不**销毁常驻 engine。旧 VC 的 surface 绑定在上一个窗口，不可复用，
    // 必须换新 VC（见 detachViewController / attach 说明）。
    // --------------------------------------------------------
    if (engineImpl && engineImpl->viewController)
    {
        writeLog("existing VC present — detaching before re-attach (engine kept)");
        detachViewController();
    }

    // --------------------------------------------------------
    // 路径 A：接管预热 Engine（无冷启动延迟）
    // --------------------------------------------------------
    FlutterEngine* prewarmEngine = nil;
    if (prewarmer && prewarmer->isReady())
    {
        void* rawPtr = prewarmer->takeEngine();
        if (rawPtr)
        {
            // CFBridgingRelease 把 retained CF 对象转交给 ARC 管理
            prewarmEngine = (FlutterEngine*)CFBridgingRelease(rawPtr);
            writeLog("Using prewarmed FlutterEngine");
        }
        prewarmer.reset();
    }
    else if (prewarmer)
    {
        // 预热尚未完成：仍有 prewarmer，timerCallback 中会重试
        writeLog("Prewarmer not ready yet — deferring initialize");
        startTimerHz(60);
        return true;
    }

    // --------------------------------------------------------
    // 路径 B（回退）：冷启动 — 定位 flutterBundle，创建新 Engine
    // 每次重新解析（不缓存 ObjC 对象，见文件顶部说明）。
    // --------------------------------------------------------
    NSBundle* flutterBundle = nil;
    if (!prewarmEngine)
    {
        // 辅助：从路径找到 bundle 根（向上遍历直至 .app / .vst3 / .framework / .bundle）
        auto findBundleRoot = [](const juce::File& start) -> juce::File {
            auto p = start;
            for (int i = 0; i < 6; ++i)
            {
                const auto name = p.getFileName();
                if (name.endsWith(".app") || name.endsWith(".vst3")
                    || name.endsWith(".framework") || name.endsWith(".bundle"))
                    return p;
                p = p.getParentDirectory();
            }
            return start; // fallback
        };

        // (a) 主 bundle（仅 Standalone 有效；VST3 中 mainBundle 是宿主）
        NSString* fa = [[NSBundle mainBundle]
            pathForResource: @"flutter_assets" ofType: nil inDirectory: nil];
        if (fa)
            flutterBundle = [NSBundle mainBundle];

        // (b) assetsDir 父目录（用作 bundle 根）
        if (!flutterBundle && assetsDir.isDirectory())
        {
            juce::File bundleRoot = findBundleRoot(assetsDir);
            NSString* rootPath = [NSString stringWithUTF8String:
                bundleRoot.getFullPathName().toRawUTF8()];
            flutterBundle = [NSBundle bundleWithPath: rootPath];
            if (flutterBundle)
                writeLog("flutterBundle from assetsDir parent: " + bundleRoot.getFullPathName());
        }

        // (c) 可执行文件目录 → 向上找到 bundle 根
        if (!flutterBundle)
        {
            const auto exeDir = juce::File::getSpecialLocation(
                juce::File::currentExecutableFile).getParentDirectory();
            juce::File bundleRoot = findBundleRoot(exeDir);
            NSString* rootPath = [NSString stringWithUTF8String:
                bundleRoot.getFullPathName().toRawUTF8()];
            flutterBundle = [NSBundle bundleWithPath: rootPath];
            if (flutterBundle)
                writeLog("flutterBundle from exe dir: " + bundleRoot.getFullPathName());
        }

        // (d) 最终回退：扫描可执行文件附近目录（含 App.framework 内部）
        //     同时用 findBundleRoot 向上追溯到真正的 bundle
        if (!flutterBundle)
        {
            const auto exeDir = juce::File::getSpecialLocation(
                juce::File::currentExecutableFile).getParentDirectory();
            const std::vector<std::function<juce::File(const juce::File&)>> patterns = {
                [](const juce::File& d){ return d.getChildFile("flutter_assets"); },
                [](const juce::File& d){ return d.getChildFile("Resources").getChildFile("flutter_assets"); },
                [](const juce::File& d){ return d.getParentDirectory().getChildFile("flutter_assets"); },
                [](const juce::File& d){ return d.getParentDirectory().getChildFile("Resources").getChildFile("flutter_assets"); },
                // macOS AOT: flutter_assets 内嵌在 App.framework 中
                [](const juce::File& d){ return d.getParentDirectory().getChildFile("Frameworks")
                    .getChildFile("App.framework").getChildFile("Versions")
                    .getChildFile("A").getChildFile("Resources")
                    .getChildFile("flutter_assets"); },
            };
            for (int up = 0; up < 5 && !flutterBundle; ++up)
            {
                auto probe = exeDir;
                for (int i = 0; i < up; ++i) probe = probe.getParentDirectory();
                for (auto& pat : patterns)
                {
                    auto f = pat(probe);
                    if (f.isDirectory())
                    {
                        juce::File bundleRoot = findBundleRoot(f);
                        NSString* rootPath = [NSString stringWithUTF8String:
                            bundleRoot.getFullPathName().toRawUTF8()];
                        flutterBundle = [NSBundle bundleWithPath: rootPath];
                        if (flutterBundle)
                        {
                            writeLog("flutterBundle found by scan: " + bundleRoot.getFullPathName());
                            break;
                        }
                    }
                }
            }
        }

        if (!flutterBundle)
        {
            writeLog("ERROR: flutter_assets not found. assetsDir=" + assetsDir.getFullPathName());
            fallbackMode = true;
            fallbackLabel.setVisible(true);
            return false;
        }
        writeLog("flutterBundle found (cold start): " + juce::String([flutterBundle.bundlePath UTF8String]));
    }

    // --------------------------------------------------------
    // 在创建 Flutter 引擎之前，显式加载 App.framework。
    // VST3 等非独立应用场景中，App.framework 不会自动加载到
    // 进程符号表，导致 Flutter 引擎调用 dlsym(RTLD_DEFAULT)
    // 查找 AOT 快照符号时失败 → DartVM::GetVMData() 空指针崩溃。
    //
    // flutterBundle 可能是两种情况：
    //   (A) App.framework 本身 — initWithPrecompiledDartBundle 需要它
    //   (B) .app / .vst3 — 当 flutter_assets 不在 App.framework 内时
    // 需要分别处理这两种情况下的 App.framework 可执行文件路径。
    // --------------------------------------------------------
    // 在创建 Flutter 引擎之前，显式加载 App.framework（VST3 等非独立
    // 应用场景下不会自动加载）。RTLD_NODELETE：快照 image 一旦
    // 加载即常驻，避免反复 load/unload 时被卸载。
    {
        // 每次重新解析 App.framework 可执行文件路径（不缓存，见文件顶部说明）。
        NSString* appPath = nil;

        {
            // flutterBundle 本身就是 App.framework（AOT 模式的正常情况）
            if ([[flutterBundle.bundlePath lastPathComponent] isEqualToString:@"App.framework"])
            {
                // 直接获取 framework 内的可执行文件
                appPath = [flutterBundle executablePath];
                // executablePath 可能返回 .../App.framework/Versions/A/App 或 .../App.framework/App
                if (![[NSFileManager defaultManager] fileExistsAtPath:appPath])
                    appPath = [[flutterBundle bundlePath]
                        stringByAppendingPathComponent:@"App"];
            }
            else
            {
                // flutterBundle 是 .app / .vst3：在 Frameworks 子目录中查找
                NSString* frameworksPath = [flutterBundle privateFrameworksPath];
                if (!frameworksPath)
                    frameworksPath = [[flutterBundle bundlePath]
                        stringByAppendingPathComponent:@"Contents/Frameworks"];

                appPath = [frameworksPath
                    stringByAppendingPathComponent:@"App.framework/Versions/A/App"];
                if (![[NSFileManager defaultManager] fileExistsAtPath:appPath])
                    appPath = [frameworksPath
                        stringByAppendingPathComponent:@"App.framework/App"];
            }

            if (!(appPath && [[NSFileManager defaultManager] fileExistsAtPath:appPath]))
                appPath = nil;
        }

        if (appPath)
        {
            // RTLD_NODELETE：AOT 快照 image 一旦加载即常驻，避免反复 load/unload 时被卸载
            void* handle = dlopen([appPath UTF8String], RTLD_NOW | RTLD_NODELETE);
            if (handle)
                writeLog("dlopen App.framework OK: " + juce::String([appPath UTF8String]));
            else
                writeLog("dlopen App.framework FAILED: " + juce::String(dlerror()));
        }
        else
        {
            writeLog("WARNING: App.framework not found near " + juce::String([flutterBundle.bundlePath UTF8String]));
        }
    }

    // --------------------------------------------------------
    // 确保常驻 headless engine 存在（每实例仅创建一次，跨开/关存活）。
    //   · 预热 engine（prewarmer 产出的即 headless engine）直接接管；
    //   · 否则用 initWithName:project:allowHeadlessExecution:YES 独立创建，
    //     **不**用 initWithProject:（那会把首个 VC 设为永久 implicit VC，
    //     导致后续换 VC 触发 “engine already has an implicit view controller”
    //     断言崩溃——正是之前热启动 Attempt B 失败的原因）。
    //   · runWithEntrypoint:nil 启动 Dart isolate（幂等：引擎已跑则立即返回）。
    // --------------------------------------------------------
    if (!engineImpl->persistentEngine)
    {
        if (prewarmEngine)
        {
            engineImpl->persistentEngine = prewarmEngine;
            writeLog("persistent engine adopted from prewarmer");
        }
        else
        {
            FlutterDartProject* project =
                [[FlutterDartProject alloc] initWithPrecompiledDartBundle: flutterBundle];
            engineImpl->persistentEngine =
                [[FlutterEngine alloc] initWithName: @"JuceFlutter"
                                            project: project
                             allowHeadlessExecution: YES];
            if (engineImpl->persistentEngine)
            {
                const BOOL ran = [engineImpl->persistentEngine runWithEntrypoint: nil];
                writeLog("persistent engine created + runWithEntrypoint="
                    + juce::String(ran ? "YES" : "NO"));
                if (!ran)
                    engineImpl->persistentEngine = nil;   // 启动失败 → 走下方 fallback
            }
        }
    }

    if (!engineImpl->persistentEngine)
    {
        writeLog("ERROR: persistent FlutterEngine unavailable");
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    // --------------------------------------------------------
    // 每次开窗新建 FlutterViewController 并 attach 到常驻 engine。
    // 新建（而非复用）是必须的：VC 的 Metal surface 绑定到创建它的窗口，
    // 换窗复用会白屏（之前热启动 Attempt A 失败原因）。initWithEngine: 头文件
    // 明确「适用于首个及后续 VC」。设置 engine.viewController 令 engine 认得该 VC。
    // --------------------------------------------------------
    engineImpl->viewController = [[FlutterViewController alloc]
        initWithEngine: engineImpl->persistentEngine
               nibName: nil
                bundle: nil];

    if (!engineImpl->viewController)
    {
        writeLog("ERROR: FlutterViewController creation failed");
        fallbackMode = true;
        fallbackLabel.setVisible(true);
        return false;
    }

    engineImpl->engine      = engineImpl->persistentEngine;
    engineImpl->flutterView = engineImpl->viewController.view;
    writeLog("VC created via initWithEngine: — flutterView="
        + juce::String(engineImpl->flutterView ? "OK" : "nil"));

    auto* peer = getPeer();
    writeLog("peer=" + juce::String(peer ? "OK" : "nil"));
    if (!peer)
    {
        // peer 尚未就绪。**不依赖 juce::Timer**——持久引擎的 runWithEntrypoint
        // 把主线程 run loop 合并进 FlutterRunLoop 后，juce::Timer（vblank/
        // CVDisplayLink 源）不再稳定泵动，timerCallback 不触发 → 第二次打开卡加载页。
        // 改用 MessageManager::callAsync 自我重排轮询：它经 CFRunLoop source 投递，
        // 正是 merged FlutterRunLoop 所泵的主线程 run loop，故一定会被执行。
        startTimerHz(60);   // 仍保留 timer 作退化兜底
        writeLog("peer=nil early-return, scheduling async attach poll");
        scheduleAttachPoll(0);
        return true;
    }

    attachFlutterViewToHost();
    startTimerHz(60);
    writeLog("initialize() done, engineRunning=" + juce::String((int)engineRunning));
    return true;
}

// ============================================================
// attachFlutterViewToHost — 将 FlutterView 插入 JUCE NSView 树
// ============================================================
void FlutterEmbedder::attachFlutterViewToHost()
{
    auto writeLog = [](const juce::String& msg) { jfDiagLog("[attach] " + msg); };

    if (!engineImpl || !engineImpl->flutterView)
    {
        writeLog("skip: no engineImpl or flutterView");
        return;
    }

    auto* peer = getPeer();
    if (!peer)
    {
        // peer 尚未就绪：直接返回。重试由 scheduleAttachPoll 的 callAsync 轮询驱动
        // （不依赖 juce::Timer——见 scheduleAttachPoll 说明）。
        writeLog("skip: no peer (attach-poll will retry)");
        return;
    }

    // 获取 JUCE peer 的底层 NSView
    NSView* juceNSView = (__bridge NSView*)(peer->getNativeHandle());
    if (!juceNSView)
    {
        writeLog("skip: getNativeHandle returned nil");
        return;
    }
    writeLog("juceNSView OK, bounds=" + juce::String(juceNSView.bounds.size.width) + "x" + juce::String(juceNSView.bounds.size.height)
        + " isFlipped=" + juce::String(juceNSView.isFlipped ? "YES" : "NO"));

    NSView* fv = engineImpl->flutterView;

    // 如果已经是子 view，先移除再重新添加（避免重复）
    if ([fv superview] != nil)
        [fv removeFromSuperview];

    [fv setTranslatesAutoresizingMaskIntoConstraints: YES];

    // -------------------------------------------------------
    // 坐标计算：JUCE 的 JUCEView（peer NSView）设置了 isFlipped=YES，
    // 因此其局部坐标系 Y 朝下，与 JUCE component 坐标完全一致。
    // 直接用 component 在 peer component 内的相对坐标即可，无需 Y 翻转。
    // -------------------------------------------------------
    auto& peerComp = peer->getComponent();
    // 把 FlutterEmbedder 的左上角（全局坐标）转为 peer component 局部坐标
    auto globalTopLeft = localPointToGlobal(juce::Point<int>(0, 0));
    auto relInPeer     = peerComp.getLocalPoint(nullptr, globalTopLeft);

    NSRect fvFrame = NSMakeRect((CGFloat)relInPeer.x,
                                (CGFloat)relInPeer.y,
                                (CGFloat)getWidth(),
                                (CGFloat)getHeight());

    // [DIAG-VIS] 记录 attach 时的几何——多项目互切黑屏时，若此处 w/h 为 0
    //           或坐标离谱，即证实是"引擎在画但 view 零尺寸/错位"的可见性竞争。
    writeLog(juce::String("[DIAG-VIS] attach ns=") + juce::String(channelNamespace.c_str())
        + " compWH=" + juce::String(getWidth()) + "x" + juce::String(getHeight())
        + " fvFrame=" + juce::String(fvFrame.origin.x) + "," + juce::String(fvFrame.origin.y)
        + " " + juce::String(fvFrame.size.width) + "x" + juce::String(fvFrame.size.height)
        + " win=" + juce::String(juceNSView.window ? "OK" : "nil"));

    [fv setFrame: fvFrame];
    [fv setAlphaValue: 0.0];   // 首帧到来前隐藏，消除空白闪烁
    [juceNSView addSubview: fv];

    // -------------------------------------------------------
    // 首帧检测：用 CALayer 内容变化（sublayers/contents 被 Flutter 填充）
    // 来判断首帧是否就绪。通过 timerCallback 中每帧检查，
    // 同时设置保底定时器（最多等 1.5 秒后强制显示）。
    // -------------------------------------------------------
    engineImpl->firstFrameReceived = false;

    // 保底：最多等待 1500ms 后无论如何显示（防止首帧回调永不触发）。
    // 加固：block 生命周期可能横跨 Editor 关闭/重开——
    //   · SafePointer 守护 this：Embedder 已析构则整体跳过；
    //   · 代际比对：本 attach 若已被 shutdown（generation 递增）或被新一代 attach
    //     取代，则本 block 过期，不再触碰状态；
    //   · 强引用 strongFv 仅保活“本次”的 view，不误改新一代的 view。
    NSView* strongFv = fv;
    juce::Component::SafePointer<FlutterEmbedder> safeThis(this);
    const uint64_t myGeneration = ++engineImpl->attachGeneration;
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW,
                                 (int64_t)(1500 * NSEC_PER_MSEC)),
                   dispatch_get_main_queue(), ^{
        auto* self = safeThis.getComponent();
        if (self == nullptr || !self->engineImpl)
            return;
        if (self->engineImpl->attachGeneration != myGeneration)
        {
            // reveal 兜底 block 因代际不符提前过期（已被 shutdown 或被新一代 attach 取代）。
            jfDiagLog("[DIAG-VIS] reveal-block EXPIRED (gen mismatch) ns="
                + juce::String(self->channelNamespace.c_str()));
            return;
        }
        if (strongFv && strongFv.alphaValue < 1.0) {
            [strongFv setAlphaValue: 1.0];
            self->engineImpl->firstFrameReceived = true;
            jfDiagLog("[DIAG-VIS] reveal-block FIRED alpha->1 ns="
                + juce::String(self->channelNamespace.c_str()));
        }
    });

    // -------------------------------------------------------
    // 首帧检测（主路）：GCD 主队列自排轮询，每帧查 layer.sublayers。
    //
    // 为什么用 GCD 而非 CADisplayLink 或 juce::Timer：
    //   · juce::Timer（vblank 源）在引擎 merged UI+platform thread 模式下不被
    //     泵动 → 首帧检测失效 → 干等 1500ms 兜底 → 长白屏。
    //   · CADisplayLink 曾用过，但它由 NSView/AppKit 持有并在 _NSDisplayLinkForwarder
    //     的独立回调栈里 fire，与 attach/detach 的释放存在竞态 → 野指针崩溃
    //     （0x6777，栈 displayLinkDidFire: → block）。且是 macOS 14+ API。
    //   · GCD 主队列 dispatch_after 在 merged run loop 下**可靠泵动**（1500ms 兜底
    //     block 即证），无 NSView 生命周期耦合，且兼容 macOS 10.6+（回到 13+ 支持）。
    //
    // 生命周期安全：block 用 SafePointer 守 this + 代际比对；不强引用 engineImpl
    // 内部对象，只在代际一致时经 self 访问；过期即 return，不 self-reschedule。
    // -------------------------------------------------------
    {
        juce::Component::SafePointer<FlutterEmbedder> safeThis2(this);
        const uint64_t linkGen = myGeneration;
        // 自排轮询函数：__block 持自身，末尾按需重排或断开。
        __block int pollTicks = 0;
        __block void (^framePoll)(void) = nil;
        framePoll = [^{
            auto* self = safeThis2.getComponent();
            if (self == nullptr || !self->engineImpl
                || self->engineImpl->attachGeneration != linkGen)
            {
                framePoll = nil;   // 本次 attach 已过期/对象已毁 → 断开自持，停止
                return;
            }
            ++pollTicks;
            NSView* fvNow = self->engineImpl->flutterView;
            CALayer* layer = fvNow ? fvNow.layer : nil;
            const bool ready = (layer && layer.sublayers.count > 0);
            if (ready && fvNow.alphaValue < 1.0)
            {
                [fvNow setAlphaValue: 1.0];
                self->engineImpl->firstFrameReceived = true;
                self->repaint();
                framePoll = nil;   // 完成，停止
                return;
            }
            if (pollTicks > 180)   // ~3s 硬上限（1500ms 兜底 block 已先揭开），停止
            {
                framePoll = nil;
                return;
            }
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(16 * NSEC_PER_MSEC)),
                           dispatch_get_main_queue(), framePoll);
        } copy];
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(16 * NSEC_PER_MSEC)),
                       dispatch_get_main_queue(), framePoll);
    }

    engineRunning = true;

    // 补注册所有在 engine 就绪前已存入 methodHandlers 的通道回调
    // 场景：Processor 预热启动引擎，此时 Editor 还未就绪，
    // paramBridge->initialize() 在 engineRunning=false 时被调用，
    // registerMethodHandler 将 handler 存入了 map 但跳过了 setMessageHandler。
    // 这里补注册，确保 Flutter→C++ 消息能被接收（与 Windows 对齐）。
    FlutterEngine* attachEngine = engineImpl->engine;
    if (attachEngine)
    {
        for (const auto& pair : methodHandlers)
            registerMacChannelCallback(attachEngine, pair.first, this);
    }

    repaint();
    writeLog("attached! engineRunning=true, frame="
        + juce::String(fvFrame.origin.x) + "," + juce::String(fvFrame.origin.y)
        + " " + juce::String(fvFrame.size.width) + "x" + juce::String(fvFrame.size.height));
    DBG("[FlutterEmbedder] Flutter NSView attached to JUCE peer NSView");

    // 通知外部 Engine 已就绪（e.g. PluginEditor 可立即同步参数到 Flutter）
    if (onEngineAttached)
        onEngineAttached();
}

// ============================================================
// deferredReleaseFlutterVC — 延迟释放 FlutterViewController
// ------------------------------------------------------------
// REAPER 崩溃根因：宿主在关闭 Editor 时会对插件 view 触发 re-layout/resize，
// 使 Flutter 的 ResizeSynchronizer 排入一个「延迟」提交任务
// （performCommit(forSize:afterDelay:) → FlutterCompositor::Present）。
// 该任务在「未来某刻」才执行；若此时已同步释放 VC/compositor，
// 任务落在已释放对象上 → 空指针解引用，宿主崩溃。
// （Ableton 关闭时不做这个 resize，故只排入「立即」任务，被 drain 兜住。）
//
// 对策：不同步释放 VC，而是用 block 强引用把它多续命一段（> 任何单次 resize
// 提交超时），期间所有延迟提交都落在「仍存活」的 compositor 上，安全执行完毕；
// block 结束时丢掉最后一个引用，引擎此刻才干净关闭（任务队列已空）。
//
// 该 block 不引用 FlutterEmbedder / Processor 等 C++ 对象，仅持有 ObjC VC，
// 故 Embedder 先析构也不会悬垂。block 代码位于本插件二进制内——由
// pinFrameworksResident() 用 RTLD_NODELETE 钉住，确保宿主 unload 后仍可执行。
// ============================================================
// ------------------------------------------------------------
// parkingWindow — 常驻离屏窗口，拆除期间用作 FlutterView 的临时归宿。
// ------------------------------------------------------------
// 为什么需要它（问题 2 的真正修复）：
//   仅把 VC「对象」多续命 500ms（旧 deferredReleaseFlutterVC）不足以防崩溃。
//   宿主关闭 Editor 时 removeFromSuperview 会让 view.window == nil，其
//   CAMetalLayer 失去可呈现的 drawable（surface == null）。此后 REAPER
//   resize-on-close 排入的延迟 performCommit(afterDelay:) 触发
//   FlutterCompositor::Present 时，落在这个「对象存活但 surface 为 null」的
//   view 上 → deref 0x0，宿主崩溃（今日 15:21 REAPER 崩溃报告即此栈）。
//
//   对策：拆除时不把 view 悬空，而是移入一个「一直存活、带 backing store 的
//   离屏窗口」。延迟 Present 于是落在有效 surface 上，安全执行完毕；等 500ms
//   后再释放 VC，此刻任务队列已空，引擎干净关闭。
//
//   窗口置于远负坐标 + alpha 0 + 不进入窗口列表，用户永不可见；进程级单例，
//   与常驻的 Dart VM / pinned frameworks 生命周期一致，代价可忽略。
static NSWindow* parkingWindow()
{
    static NSWindow* win = nil;
    if (win == nil)
    {
        // 足够大以覆盖任意 Editor 尺寸，避免 park 时触发额外 resize commit。
        NSRect frame = NSMakeRect(-20000, -20000, 2048, 2048);
        win = [[NSWindow alloc] initWithContentRect: frame
                                          styleMask: NSWindowStyleMaskBorderless
                                            backing: NSBackingStoreBuffered
                                              defer: NO];
        [win setReleasedWhenClosed: NO];
        [win setAlphaValue: 0.0];
        [win setIgnoresMouseEvents: YES];
        [win setExcludedFromWindowsMenu: YES];
        // 关键：**不要** orderBack:/orderFront:/makeKeyAndOrderFront: 让窗口上屏。
        //   NSBackingStoreBuffered + defer:NO 已保证 off-screen 窗口拥有有效的
        //   backing surface（延迟 Present 有处可落，仍满足防 null-surface 的目的）。
        //   而一旦窗口上屏（on-screen/visible），把 FlutterView addSubview 进去会
        //   触发 AppKit 的 viewWillAppear → FlutterViewController.viewWillAppear
        //   调 runWithEntrypoint → 在拆除/DartVM 失效时机 deref 0x0（Ableton 打开
        //   editor 即崩 → 被拉黑 → 插件从列表消失。见 0x538 崩溃栈）。
        //   保持窗口永不上屏即可根除这条崩溃链。
    }
    return win;
}

static void deferredReleaseFlutterVC(FlutterViewController* vc, NSView* parkedView)
{
    if (!vc)
        return;


    // ------------------------------------------------------------
    // 自适应静默检测（替代固定 500ms）：
    //   宿主关闭时排入的延迟提交（尤其 REAPER 的 resize-on-close
    //   performCommit(forSize:)）会改变 parked view 的 frame / layer。
    //   我们每 kProbeMs 轮询一次，当这些量连续 kQuietNeeded 次不再变化，
    //   即认为提交队列已排空，立即释放；否则一直等到变化停止，并用
    //   kCapProbes 设硬上限兜底。
    //
    //   效果：Ableton（关闭不 resize）几帧内即静默 → ~100ms 就释放；
    //         REAPER（关闭 resize）自适应等到 resize 提交稳定才释放，
    //         不再无谓地固定等满 500ms，也不会过早释放。
    //   安全性由 parking 窗口保证——释放前 view 始终在有效 surface 上，
    //   即便估计偏早，最坏也只是多等一个上限周期，不会崩。
    // ------------------------------------------------------------
    static constexpr int kProbeMs     = 50;   // 轮询间隔
    static constexpr int kFloorProbes = 2;    // 最少持有 ~100ms
    static constexpr int kQuietNeeded = 3;    // 连续 ~150ms 无变化视为静默
    static constexpr int kCapProbes   = 40;   // 硬上限 ~2000ms

    __block int probeCount = 0;
    __block int quietStreak = 0;
    __block NSSize    lastSize   = parkedView ? parkedView.frame.size : NSZeroSize;
    __block NSUInteger lastLayers = (parkedView && parkedView.layer)
                                        ? parkedView.layer.sublayers.count : 0;

    // 递归 block：__block 强引用自身，末尾置 nil 断开保留环，
    // 同时释放对 vc / parkedView 的强引用 → 引擎干净关闭。
    __block void (^probe)(void) = nil;
    probe = [^{
        ++probeCount;

        const NSSize    curSize   = parkedView ? parkedView.frame.size : NSZeroSize;
        const NSUInteger curLayers = (parkedView && parkedView.layer)
                                         ? parkedView.layer.sublayers.count : 0;

        const bool changed = !NSEqualSizes(curSize, lastSize) || curLayers != lastLayers;
        lastSize   = curSize;
        lastLayers = curLayers;
        quietStreak = changed ? 0 : (quietStreak + 1);

        const bool quietEnough = (probeCount >= kFloorProbes) && (quietStreak >= kQuietNeeded);
        const bool hitCap      = (probeCount >= kCapProbes);

        if (quietEnough || hitCap)
        {
            if (parkedView)
                [parkedView removeFromSuperview];
            (void)vc;
            probe = nil;   // 断开自我保留 → 释放 block、vc、parkedView
            return;
        }

        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(kProbeMs * NSEC_PER_MSEC)),
                       dispatch_get_main_queue(), probe);
    } copy];

    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(kProbeMs * NSEC_PER_MSEC)),
                   dispatch_get_main_queue(), probe);
}

// ============================================================
// pinFrameworksResident — 用 RTLD_NODELETE 钉住 Flutter 相关代码常驻内存
// ------------------------------------------------------------
// REAPER 等宿主支持彻底 unload 插件 bundle（dlclose）；Ableton 通常常驻。
// 一旦 bundle 被卸载，而 Flutter 引擎线程 / Dart VM / 已排队的 run loop 任务
// 或本插件里的延迟 block 仍存活，它们执行的代码页已被 unmap → 必崩。
//
// Dart VM 是进程级单例、无法干净地反复 init/shutdown，其线程实际会常驻；
// 因此这里主动钉住相关镜像（业界对「带不可停后台线程的插件」的标准做法）：
//   · FlutterMacOS.framework —— 引擎 / compositor / VC dealloc 代码
//   · 本插件自身二进制       —— 延迟 block、各类回调
// 代价：这些镜像在进程生命周期内不再被回收（可接受，且与 Dart VM 常驻一致）。
// 一次性执行（C++ 静态局部变量初始化线程安全）。
// ============================================================
static void pinFrameworksResident()
{
    static const bool pinnedOnce = []() -> bool
    {
        // FlutterMacOS.framework：经其类定位 framework 可执行文件后钉住。
        if (Class vcClass = NSClassFromString(@"FlutterViewController"))
        {
            NSBundle* fb = [NSBundle bundleForClass: vcClass];
            if (fb && fb.executablePath)
            {
                if (dlopen(fb.executablePath.UTF8String, RTLD_NOW | RTLD_NODELETE))
                    FLUTTER_LOG("[FlutterEmbedder] pinned FlutterMacOS.framework (RTLD_NODELETE)");
            }
        }
        // 本插件自身：dladdr 反查自身路径后钉住（RTLD_NOLOAD 只提引用不重载）。
        Dl_info info{};
        if (dladdr(reinterpret_cast<const void*>(&pinFrameworksResident), &info) && info.dli_fname)
        {
            if (dlopen(info.dli_fname, RTLD_NOW | RTLD_NODELETE | RTLD_NOLOAD))
                FLUTTER_LOG("[FlutterEmbedder] pinned plugin binary resident (RTLD_NODELETE)");
        }
        return true;
    }();
    juce::ignoreUnused(pinnedOnce);
}

// ============================================================
// detachViewController — 关窗时安全分离当前 VC，**保留常驻 engine**
// ------------------------------------------------------------
// 只拆 VC，不动 persistentEngine。parking 窗口 + 延迟释放 + 代际过期全部保留
// （宿主关窗时排入的延迟 Present 仍需落到有效 surface；见 parkingWindow /
//  deferredReleaseFlutterVC）。与冷启动架构的唯一区别：不再对 engine 调
// shutDownEngine，engine 及其 binaryMessenger / 通道回调跨开关存活。
// ============================================================
void FlutterEmbedder::detachViewController()
{
    stopTimer();

    if (!engineImpl)
    {
        engineRunning = false;
        return;
    }

    // 重入保护：排空期间若有事件再次触发，直接返回。
    if (engineImpl->tearingDown)
        return;
    engineImpl->tearingDown = true;

    // 与其他实例的引擎变迁互斥（VC detach 也会触及共享 run loop 上的 VC 生命周期）。
    std::lock_guard<std::recursive_mutex> gate(engineLifecycleGate());

    // 让所有在途 / 陈旧的延迟 block 立即过期（见 attachFlutterViewToHost 的 dispatch_after）。
    // 首帧检测的 GCD 轮询也靠此代际递增自行过期（下次 tick 见代际不符即断开），无需显式停。
    ++engineImpl->attachGeneration;

    // 1) 把 FlutterView 移到常驻离屏 parking 窗口——保证宿主关窗排入的延迟
    //    performCommit(afterDelay:) → Present 落到有效 backing surface，不 deref 0x0。
    NSView* viewToPark = engineImpl->flutterView;
    if (viewToPark)
    {
        [viewToPark setAlphaValue: 0.0];
        [parkingWindow().contentView addSubview: viewToPark];   // 自动从旧父 view 摘下
    }

    engineRunning = false;   // 当前无 VC attach，视为「不在跑」（engine 仍活）

    // 2) 从常驻 engine 正式分离 VC：engine.viewController 置 nil。engine 以
    //    allowHeadlessExecution:YES 创建，故此操作**不**终止 engine（见头文件契约）。
    //    随后交延迟释放器多续命 VC 一段，让在途提交任务安全排空后再释放。
    if (engineImpl->persistentEngine
        && engineImpl->persistentEngine.viewController == engineImpl->viewController)
    {
        engineImpl->persistentEngine.viewController = nil;
    }

    FlutterViewController* vcToRelease = engineImpl->viewController;
    engineImpl->flutterView    = nil;
    engineImpl->viewController = nil;
    engineImpl->engine         = nil;   // 断开「当前 attach 的 engine」引用（persistentEngine 保留）
    engineImpl->tearingDown    = false;

    deferredReleaseFlutterVC(vcToRelease, viewToPark);

    // 注：延迟释放的最终 VC 释放在本函数返回后的 dispatch_after 回调里（已脱离本锁），
    //     此时 view 已在 parking 窗口有效 surface 上，安全。engine 全程存活。
}

// ============================================================
// shutdownEngine — 销毁常驻 engine（仅 ~FlutterEmbedder，即实例被宿主移除时）
// ------------------------------------------------------------
// 先 detachViewController() 拆掉可能仍在的 VC，再在 gate 下对常驻 engine 调
// shutDownEngine 并置 nil。这是**唯一**销毁 engine 的路径，频率从「每次关窗」
// 降到「实例销毁」——把 deref-0x10 崩溃窗口压到几乎不存在，且此时宿主通常
// 不再向本 engine 发消息。
// ============================================================
void FlutterEmbedder::shutdownEngine()
{
    // 先分离并延迟释放当前 VC（若有）。detachViewController 内部已 stopTimer /
    // 取 gate / park view / 递增代际。
    if (engineImpl && engineImpl->viewController)
        detachViewController();
    else
        stopTimer();

    if (!engineImpl)
        return;

    std::lock_guard<std::recursive_mutex> gate(engineLifecycleGate());

    // 销毁常驻 engine。shutDownEngine 会停止 Dart isolate 并释放引擎侧资源；
    // Dart VM 本身是进程级单例（pinFrameworksResident 常驻），不受影响。
    if (engineImpl->persistentEngine)
    {
        [engineImpl->persistentEngine shutDownEngine];
        engineImpl->persistentEngine = nil;
    }
    engineRunning = false;
}

// ============================================================
// timerCallback — 同步 Flutter view 尺寸 / 重试 attach
// ============================================================
void FlutterEmbedder::timerCallback()
{
    if (!engineImpl)
        return;

    // -------------------------------------------------------
    // 若正在等待预热完成，预热 ready 后自动执行初始化
    // -------------------------------------------------------
    if (!engineRunning && prewarmer)
    {
        if (prewarmer->isReady())
            initialize();   // 此时预热已完成，initialize() 会接管 engine
        return;
    }

    // 如果 VC 已创建但 view 还未 attach（peer 延迟就绪），持续重试
    if (!engineRunning && engineImpl->viewController)
    {
        attachFlutterViewToHost();
        return;
    }

    if (!engineRunning)
        return;

    // 如果 flutterView 已 detach（e.g. 窗口重建），重新 attach
    if (engineImpl->flutterView && [engineImpl->flutterView superview] == nil)
    {
        attachFlutterViewToHost();
        return;
    }

    // 同步尺寸（避免每帧都调用 layout，只在需要时更新）
    syncFlutterViewBounds();

    // -------------------------------------------------------
    // 首帧检测：若 FlutterView 仍处于隐藏（alpha=0）状态，
    // 检查其 CALayer 是否已有内容（Flutter 渲染第一帧后填充 layer）
    // -------------------------------------------------------
    if (!engineImpl->firstFrameReceived && engineImpl->flutterView)
    {
        NSView* fv = engineImpl->flutterView;
        if (fv.alphaValue < 1.0)
        {
            // 检查 layer 是否有子图层（Flutter 渲染层）且内容非空
            CALayer* layer = fv.layer;
            if (layer && layer.sublayers.count > 0)
            {
                [fv setAlphaValue: 1.0];
                engineImpl->firstFrameReceived = true;
                repaint();
                jfDiagLog("[DIAG-VIS] timer reveal alpha->1 ns="
                    + juce::String(channelNamespace.c_str())
                    + " sublayers=" + juce::String((int)layer.sublayers.count));
            }
        }
    }

    // -------------------------------------------------------
    // [DIAG-VIS] 卡黑屏检测：engine 在跑，但 view 仍不可见（alpha≈0 /
    //   零尺寸 / layer 无子层 / 不在窗口上）。每 ~60 帧记一次快照，
    //   多项目互切复现黑屏后，这行会直指卡在哪个可见性条件。
    if (engineRunning && engineImpl->flutterView)
    {
        NSView* fv = engineImpl->flutterView;
        const bool invisible = (fv.alphaValue < 0.99)
            || (fv.frame.size.width < 1.0 || fv.frame.size.height < 1.0)
            || (fv.window == nil)
            || (fv.layer == nil || fv.layer.sublayers.count == 0);
        if (invisible)
        {
            if ((engineImpl->stuckDiagTicks++ % 60) == 0)
                jfDiagLog(juce::String("[DIAG-VIS] STUCK-BLACK ns=") + juce::String(channelNamespace.c_str())
                    + " alpha=" + juce::String((double)fv.alphaValue)
                    + " frameWH=" + juce::String(fv.frame.size.width) + "x" + juce::String(fv.frame.size.height)
                    + " win=" + juce::String(fv.window ? "OK" : "nil")
                    + " sublayers=" + juce::String(fv.layer ? (int)fv.layer.sublayers.count : -1)
                    + " firstFrame=" + juce::String((int)engineImpl->firstFrameReceived));
        }
        else
        {
            engineImpl->stuckDiagTicks = 0;
        }
    }
}

// ============================================================
// syncFlutterViewBounds — 更新 Flutter NSView 的 frame
// ============================================================
void FlutterEmbedder::syncFlutterViewBounds()
{
    if (!engineImpl || !engineImpl->flutterView)
        return;

    NSView* fv  = engineImpl->flutterView;
    NSView* sup = [fv superview];
    if (!sup)
        return;

    auto* peer = getPeer();
    if (!peer)
        return;

    // JUCEView 是 isFlipped=YES，Y 朝下，直接用 component 相对坐标
    auto globalTopLeft = localPointToGlobal(juce::Point<int>(0, 0));
    auto relInPeer     = peer->getComponent().getLocalPoint(nullptr, globalTopLeft);

    NSRect newFrame = NSMakeRect((CGFloat)relInPeer.x, (CGFloat)relInPeer.y,
                                 (CGFloat)getWidth(), (CGFloat)getHeight());
    if (!NSEqualRects(fv.frame, newFrame))
        [fv setFrame: newFrame];
}

// ============================================================
// sendMessage — macOS 实现
// ============================================================
void FlutterEmbedder::sendMessage(std::string_view channel,
                                   std::string_view method,
                                   std::string_view argsJson)
{
    if (!engineRunning || !engineImpl) return;

    // 将 engine 提升为本地 __strong 引用，防止在 sendMessage 执行期间
    // engine 被异步释放（shutdownEngine / detachFromParent 等）
    FlutterEngine* engine = engineImpl->engine;
    if (!engine || !engine.binaryMessenger) return;

    // 在栈上构造 JSON 信封，不产生堆分配（与 Windows 对齐）
    char buf[1024];
    const int n = std::snprintf(buf, sizeof(buf),
        "{\"method\":\"%.*s\",\"args\":%.*s}",
        static_cast<int>(method.size()),  method.data(),
        static_cast<int>(argsJson.size()), argsJson.data());

    NSString* chName = [NSString stringWithUTF8String: std::string(channel).c_str()];
    FlutterBasicMessageChannel* msgChannel =
        [FlutterBasicMessageChannel
            messageChannelWithName: chName
                   binaryMessenger: engine.binaryMessenger
                             codec: [FlutterStringCodec sharedInstance]];

    if (n > 0 && n < static_cast<int>(sizeof(buf)))
    {
        NSString* payloadStr = [[NSString alloc] initWithBytes: buf
                                                        length: (NSUInteger)n
                                                      encoding: NSUTF8StringEncoding];
        [msgChannel sendMessage: payloadStr];
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
        [msgChannel sendMessage: [NSString stringWithUTF8String: payload.c_str()]];
    }
}

// ============================================================
// registerMethodHandler — macOS 实现
// ============================================================
void FlutterEmbedder::registerMethodHandler(std::string_view channel,
                                             MethodCallback callback)
{
    // insert_or_assign 确保旧的悬垂 lambda 被替换（UAF-1 修复，与 Windows 对齐）
    auto [it, _] = methodHandlers.insert_or_assign(std::string(channel), std::move(callback));

    if (!engineRunning || !engineImpl) return;

    FlutterEngine* engine = engineImpl->engine;
    if (!engine) return;

    registerMacChannelCallback(engine, it->first, this);
}

// ============================================================
// unregisterMethodHandler — macOS 实现
// ============================================================
void FlutterEmbedder::unregisterMethodHandler(std::string_view channel)
{
    const std::string key(channel);
    methodHandlers.erase(key);

    if (!engineImpl) return;
    FlutterEngine* engine = engineImpl->engine;
    if (!engine || !engine.binaryMessenger) return;

    NSString* chName = [NSString stringWithUTF8String: key.c_str()];
    FlutterBasicMessageChannel* msgChannel =
        [FlutterBasicMessageChannel
            messageChannelWithName: chName
                   binaryMessenger: engine.binaryMessenger
                             codec: [FlutterStringCodec sharedInstance]];
    [msgChannel setMessageHandler: nil];
}

// ============================================================
// resized — 同步 NSView 尺寸
// ============================================================
void FlutterEmbedder::resized()
{
    fallbackLabel.setBounds(getLocalBounds());

    if (engineRunning && engineImpl && engineImpl->flutterView)
    {
        if ([engineImpl->flutterView superview] == nil)
            attachFlutterViewToHost();
        else
            syncFlutterViewBounds();
    }
}

// ============================================================
// parentHierarchyChanged — peer 就绪时 attach（不依赖 message loop）
// ------------------------------------------------------------
// initFlutterUI() 在 Editor **构造函数**内调用 initialize()，此刻 Editor 尚未
// 被宿主 addToDesktop，getPeer() 必为 nil，故 initialize() 无法当场 attach。
// 之前依赖 juce::Timer / callAsync 兜底 attach，但持久引擎的 merged
// FlutterRunLoop 接管主线程后，JUCE 的 Timer 与 MessageManager 投递都可能
// 不被泵动 → 第二次打开永远等不到 attach → 卡加载页。
//
// parentHierarchyChanged 由 JUCE 在组件被加入/移出带 peer 的层级时**同步**
// 派发（就在宿主 addToDesktop 的调用栈里，不经消息队列），因此不受 run loop
// 泵动影响。这里一旦拿到 peer 且 VC 就绪未 attach，立即同步 attach。
// ============================================================
void FlutterEmbedder::parentHierarchyChanged()
{
    if (!engineImpl || !engineImpl->flutterView)
        return;
    if (engineRunning)
        return;                       // 已 attach
    if (getPeer() == nullptr)
        return;                       // 仍无 peer（移出层级 / 尚未就绪）

    jfDiagLog("[hierarchy] peer ready — attaching ns=" + juce::String(channelNamespace.c_str()));
    attachFlutterViewToHost();
}

// ============================================================
// 构造函数 — macOS 实现（需要在 EngineImpl 定义之后才能 make_unique）
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
// 析构函数 — macOS 实现
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

#endif // FLUTTER_ENGINE_ENABLED && __APPLE__

