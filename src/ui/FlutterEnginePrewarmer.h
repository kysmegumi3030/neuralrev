#pragma once
// ============================================================
// FlutterEnginePrewarmer.h
//
// 跨平台接口：在 PluginProcessor 构造时预热 Flutter Engine。
// 实现在 FlutterEnginePrewarmer_mac.mm（macOS）；
// 其他平台提供空 stub（同文件 #else 分支）。
//
// 重要：takeEngine() 返回的是 ARC __bridge_retained void*，
// 调用方（FlutterEmbedder_mac.mm）负责通过 __bridge_transfer 或
// CFRelease 接管所有权。
// ============================================================

#include <juce_core/juce_core.h>
#include <memory>

class FlutterEnginePrewarmer
{
public:
    explicit FlutterEnginePrewarmer(const juce::File& assetsDir);
    ~FlutterEnginePrewarmer();

    // 预热是否已完成（engine ready 或 失败）
    bool isReady() const;

    // 取走预热好的 FlutterEngine*（ARC __bridge_retained void*）。
    // 只能调用一次；调用后 prewarmer 不再持有 engine。
    // 返回 nullptr 表示预热失败或尚未完成。
    void* takeEngine();

    // 不可拷贝
    FlutterEnginePrewarmer(const FlutterEnginePrewarmer&) = delete;
    FlutterEnginePrewarmer& operator=(const FlutterEnginePrewarmer&) = delete;

private:
    struct Impl;
    std::unique_ptr<Impl> pImpl;
};
