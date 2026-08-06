#include "PluginEditor.h"
#include "PluginProcessor.h"

JuceFlutterPluginEditor::JuceFlutterPluginEditor(JuceFlutterPluginProcessor& audioProcessorRef)
    : AudioProcessorEditor(&audioProcessorRef), audioProcessor(audioProcessorRef)
{
    setSize(JUCE_FLUTTER_EDITOR_DEFAULT_WIDTH, JUCE_FLUTTER_EDITOR_DEFAULT_HEIGHT);
    setResizable(true, false);  // Release 引擎 HWND 覆盖 corner grip，改为无 grip；仍可拖窗口边框缩放
    setResizeLimits(JUCE_FLUTTER_EDITOR_MIN_WIDTH, JUCE_FLUTTER_EDITOR_MIN_HEIGHT,
                    JUCE_FLUTTER_EDITOR_MAX_WIDTH, JUCE_FLUTTER_EDITOR_MAX_HEIGHT);
#ifdef JUCE_FLUTTER_EDITOR_FIXED_ASPECT
    // 下游工程可定义该宏（宽/高比值表达式）以启用固定纵横比缩放
    if (auto* constrainer = getConstrainer())
        constrainer->setFixedAspectRatio(static_cast<double>(JUCE_FLUTTER_EDITOR_FIXED_ASPECT));
#endif
    initFlutterUI();
}

JuceFlutterPluginEditor::~JuceFlutterPluginEditor()
{
    if (flutterEmbedder)
    {
        // UAF-3: 先清空回调，防止 timer 在 Editor 析构后
        // 触发 attachFlutterViewToHost 时访问已销毁的 Editor this
        flutterEmbedder->onEngineAttached = nullptr;

        // 顺序关键：先销毁 paramBridge——此时引擎仍在运行，其析构可正常
        // 从 binaryMessenger 注销通道回调；之后再 detach/shutdown 引擎，
        // 杜绝拆除期间还有通道回调持悬垂 this 在飞。
        paramBridge.reset();

        removeChildComponent(flutterEmbedder);
        audioProcessor.releaseEmbedder();
        flutterEmbedder = nullptr;
    }
}

void JuceFlutterPluginEditor::initFlutterUI()
{
    auto* embedder = audioProcessor.acquireEmbedder();
    if (embedder == nullptr)
    {
        FLUTTER_LOG("[PluginEditor] FlutterEmbedder unavailable");
        return;
    }

    flutterEmbedder = embedder;
    auto bounds = getLocalBounds();
    flutterEmbedder->setBounds(bounds);
    addAndMakeVisible(*flutterEmbedder);

    // 在 reattachToParent() 之前创建 paramBridge，
    // 以便 onEngineAttached 回调触发时 paramBridge 已就绪。
    paramBridge = std::make_unique<AudioParameterBridge>(audioProcessor.getAPVTS(), *flutterEmbedder);

    // Engine 成功 attach 到宿主 HWND 后立即同步全部参数和 schema。
    // 处理「重开编辑器」场景：Dart 代码已在运行且 handler 已注册，
    // 直接推送即可，无需等待 requestSync 握手。
    flutterEmbedder->onEngineAttached = [this]()
    {
        if (paramBridge)
            paramBridge->syncAllToFlutter();
    };

    // reattachToParent() 内部若 HWND 已就绪则同步调用 attachFlutterViewToHost()，
    // 触发 onEngineAttached；若 HWND 尚未就绪则 timer 稍后重试。
    flutterEmbedder->reattachToParent();

    // 完成其余初始化：注册 requestSync/setParam handler、启动 VU meter timer 等。
    // 「首次启动」场景由 requestSync 握手保证：Dart 首帧渲染后发 requestSync →
    // C++ 调用 syncAllToFlutter()，不依赖 callAsync 时序。
    paramBridge->initialize();
}

void JuceFlutterPluginEditor::resized()
{
    if (flutterEmbedder == nullptr)
        return;

    flutterEmbedder->setBounds(getLocalBounds());
}