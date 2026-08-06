// ============================================================
// FlutterEnginePrewarmer_mac.mm
//
// 在 PluginProcessor 构造时（后台线程）预热 Flutter Engine，
// 使 Dart VM 在 Editor 打开之前已经完成冷启动。
//
// 用法（macOS only）：
//   创建：auto pw = std::make_shared<FlutterEnginePrewarmer>(assetsDir);
//   使用：FlutterEngine* engine = pw->takeEngine();   // 只能取一次
//         pw 可在取走 engine 后销毁。
// ============================================================

#include "FlutterEnginePrewarmer.h"

#if defined(__APPLE__)

#import <FlutterMacOS/FlutterMacOS.h>
#import <Cocoa/Cocoa.h>
#include <dlfcn.h>
#include <juce_core/juce_core.h>

// ============================================================
// ObjC 包装对象（隐藏在 .mm 内部，头文件不暴露 ObjC）
// ============================================================
@interface JFPrewarmerBox : NSObject
@property (nonatomic, strong, nullable) FlutterEngine* engine;
@property (nonatomic, assign) BOOL ready;
@end

@implementation JFPrewarmerBox
- (instancetype)init {
    self = [super init];
    _engine = nil;
    _ready  = NO;
    return self;
}
@end

// ============================================================
// FlutterEnginePrewarmer 实现
// ============================================================
struct FlutterEnginePrewarmer::Impl {
    JFPrewarmerBox*  box     { nil };
    dispatch_queue_t queue   { nullptr };
};

FlutterEnginePrewarmer::FlutterEnginePrewarmer(const juce::File& assetsDir)
    : pImpl(std::make_unique<Impl>())
{
    pImpl->box   = [[JFPrewarmerBox alloc] init];
    pImpl->queue = dispatch_queue_create("com.juceflutter.prewarm",
                                          DISPATCH_QUEUE_SERIAL);

    // -------------------------------------------------------
    // 必须在主线程创建 FlutterEngine（AppKit 要求）
    // -------------------------------------------------------
    auto startPrewarm = [this, assetsDir]() {
        // (a) 定位 flutterBundle（与 FlutterEmbedder_mac.mm 相同逻辑）
        NSBundle* flutterBundle = nil;

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
            return start;
        };

        // 主 bundle（Standalone .app）
        NSString* fa = [[NSBundle mainBundle]
            pathForResource:@"flutter_assets" ofType:nil inDirectory:nil];
        if (fa)
            flutterBundle = [NSBundle mainBundle];

        // assetsDir → 向上找到 bundle 根
        if (!flutterBundle && assetsDir.isDirectory()) {
            juce::File bundleRoot = findBundleRoot(assetsDir);
            NSString* rootPath = [NSString stringWithUTF8String:
                bundleRoot.getFullPathName().toRawUTF8()];
            flutterBundle = [NSBundle bundleWithPath:rootPath];
        }

        // 可执行文件目录 → 向上找到 bundle 根
        if (!flutterBundle) {
            const auto exeDir = juce::File::getSpecialLocation(
                juce::File::currentExecutableFile).getParentDirectory();
            juce::File bundleRoot = findBundleRoot(exeDir);
            NSString* rootPath = [NSString stringWithUTF8String:
                bundleRoot.getFullPathName().toRawUTF8()];
            flutterBundle = [NSBundle bundleWithPath:rootPath];
        }

        // 最终回退：扫描可执行文件附近目录（含 App.framework 内部）
        if (!flutterBundle) {
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
            for (int up = 0; up < 5 && !flutterBundle; ++up) {
                auto probe = exeDir;
                for (int i = 0; i < up; ++i) probe = probe.getParentDirectory();
                for (auto& pat : patterns) {
                    auto f = pat(probe);
                    if (f.isDirectory()) {
                        juce::File bundleRoot = findBundleRoot(f);
                        NSString* rootPath = [NSString stringWithUTF8String:
                            bundleRoot.getFullPathName().toRawUTF8()];
                        flutterBundle = [NSBundle bundleWithPath:rootPath];
                        if (flutterBundle) break;
                    }
                }
            }
        }

        if (!flutterBundle) {
            juce::File("/tmp/juce_flutter_init.log")
                .appendText("[Prewarmer] ERROR: flutter_assets not found\n");
            return;
        }

        // 显式加载 App.framework（VST3 场景下 AOT 符号不自动全局可见）
        {
            NSString* appPath = nil;

            if ([[flutterBundle.bundlePath lastPathComponent] isEqualToString:@"App.framework"])
            {
                appPath = [flutterBundle executablePath];
                if (![[NSFileManager defaultManager] fileExistsAtPath:appPath])
                    appPath = [[flutterBundle bundlePath]
                        stringByAppendingPathComponent:@"App"];
            }
            else
            {
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

            // RTLD_NODELETE：快照 image 常驻，避免反复 load/unload 时被卸载
            if (appPath && [[NSFileManager defaultManager] fileExistsAtPath:appPath])
                dlopen([appPath UTF8String], RTLD_NOW | RTLD_NODELETE);
        }

        // (b) 创建 FlutterDartProject
        FlutterDartProject* project =
            [[FlutterDartProject alloc] initWithPrecompiledDartBundle:flutterBundle];

        // (c) 创建允许无头执行的 FlutterEngine
        FlutterEngine* engine =
            [[FlutterEngine alloc] initWithName:@"JuceFlutterPrewarm"
                                        project:project
                          allowHeadlessExecution:YES];

        // (d) 在后台线程 run（启动 Dart VM / isolate）
        // 非 ARC：用 __unsafe_unretained 避免 retain cycle；
        // box 的生命期由 pImpl->box 持有，callback 执行前不会释放
        JFPrewarmerBox* __unsafe_unretained unsafeBox = pImpl->box;
        dispatch_async(pImpl->queue, ^{
            juce::File("/tmp/juce_flutter_init.log")
                .appendText("[Prewarmer] runWithEntrypoint begin\n");

            BOOL ok = [engine runWithEntrypoint:nil];  // nil = main()

            dispatch_async(dispatch_get_main_queue(), ^{
                JFPrewarmerBox* b = unsafeBox;
                if (!b) return;
                if (ok) {
                    b.engine = engine;
                    b.ready  = YES;
                    juce::File("/tmp/juce_flutter_init.log")
                        .appendText("[Prewarmer] engine ready\n");
                } else {
                    juce::File("/tmp/juce_flutter_init.log")
                        .appendText("[Prewarmer] runWithEntrypoint FAILED\n");
                    b.ready = YES;   // 标记已结束（失败）
                }
            });
        });
    };

    // FlutterEngine 创建必须在主线程
    if ([NSThread isMainThread]) {
        startPrewarm();
    } else {
        dispatch_sync(dispatch_get_main_queue(), ^{ startPrewarm(); });
    }
}

FlutterEnginePrewarmer::~FlutterEnginePrewarmer() = default;

bool FlutterEnginePrewarmer::isReady() const
{
    return pImpl->box && pImpl->box.ready;
}

void* FlutterEnginePrewarmer::takeEngine()
{
    if (!pImpl->box) return nullptr;
    FlutterEngine* eng = pImpl->box.engine;
    pImpl->box.engine  = nil;   // 转移所有权
    if (!eng) return nullptr;
    // CFRetain 使对象引用计数 +1，以便通过 void* 跨越 ARC/MRC 边界传递
    // 接收方（FlutterEmbedder_mac.mm）通过 CFBridgingRelease 接管所有权
    return (void*)CFRetain((__bridge CFTypeRef)eng);
}

#else // !__APPLE__

// 非 macOS 平台：空实现
struct FlutterEnginePrewarmer::Impl {};

FlutterEnginePrewarmer::FlutterEnginePrewarmer(const juce::File&)
    : pImpl(std::make_unique<Impl>()) {}

FlutterEnginePrewarmer::~FlutterEnginePrewarmer() = default;
bool FlutterEnginePrewarmer::isReady() const  { return false; }
void* FlutterEnginePrewarmer::takeEngine()    { return nullptr; }

#endif
