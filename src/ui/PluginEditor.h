#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "PluginProcessor.h"
#include "FlutterEmbedder.h"
#include "AudioParameterBridge.h"

// ============================================================
// 编辑器默认窗口尺寸 —— 各插件工程通过 CMake 编译宏覆盖默认值，
// 例如：target_compile_definitions(${PROJECT_NAME} PUBLIC
//           JUCE_FLUTTER_EDITOR_DEFAULT_WIDTH=1040
//           JUCE_FLUTTER_EDITOR_DEFAULT_HEIGHT=400
//           JUCE_FLUTTER_EDITOR_MIN_WIDTH=700
//           JUCE_FLUTTER_EDITOR_MIN_HEIGHT=260
//           JUCE_FLUTTER_EDITOR_MAX_WIDTH=1800
//           JUCE_FLUTTER_EDITOR_MAX_HEIGHT=700)
// 未覆盖时使用本模板的默认尺寸。
// ============================================================
#ifndef JUCE_FLUTTER_EDITOR_DEFAULT_WIDTH
#define JUCE_FLUTTER_EDITOR_DEFAULT_WIDTH 800
#endif
#ifndef JUCE_FLUTTER_EDITOR_DEFAULT_HEIGHT
#define JUCE_FLUTTER_EDITOR_DEFAULT_HEIGHT 520
#endif
#ifndef JUCE_FLUTTER_EDITOR_MIN_WIDTH
#define JUCE_FLUTTER_EDITOR_MIN_WIDTH 400
#endif
#ifndef JUCE_FLUTTER_EDITOR_MIN_HEIGHT
#define JUCE_FLUTTER_EDITOR_MIN_HEIGHT 300
#endif
#ifndef JUCE_FLUTTER_EDITOR_MAX_WIDTH
#define JUCE_FLUTTER_EDITOR_MAX_WIDTH 1600
#endif
#ifndef JUCE_FLUTTER_EDITOR_MAX_HEIGHT
#define JUCE_FLUTTER_EDITOR_MAX_HEIGHT 1000
#endif

/**
 * @brief 插件 GUI 编辑器 - 基于 Flutter UI
 */
class JuceFlutterPluginEditor : public juce::AudioProcessorEditor
{
public:
    explicit JuceFlutterPluginEditor(JuceFlutterPluginProcessor& processor);
    ~JuceFlutterPluginEditor() override;

    void resized() override;

private:
    JuceFlutterPluginProcessor& audioProcessor;
    FlutterEmbedder* flutterEmbedder { nullptr };
    std::unique_ptr<AudioParameterBridge> paramBridge;

    void initFlutterUI();

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(JuceFlutterPluginEditor)
};