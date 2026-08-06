#pragma once

#include <string_view>

// ============================================================
// FlutterExtensionChannels
//
// 统一维护扩展通道 leaf 常量，便于 C++ / Dart 双端保持一致。
// 基础通道完整名为: audio_bridge/<leaf>
// 实际运行时会由 FlutterEmbedder 自动映射到实例隔离通道：
//   audio_bridge/v2/<namespace>/<leaf>
// ============================================================
namespace FlutterExtensionChannels
{
    constexpr std::string_view kSpectrum     = "spectrum";
    constexpr std::string_view kPresetBrowser = "preset_browser";
    constexpr std::string_view kTaskStatus   = "task_status";
}
