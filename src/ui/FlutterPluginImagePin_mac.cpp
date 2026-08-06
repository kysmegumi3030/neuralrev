// ============================================================
// FlutterPluginImagePin_mac.cpp
// ------------------------------------------------------------
// macOS：防止宿主反复 load/unload 插件时崩溃。
//
// Flutter 引擎会常驻多个后台线程（UI / raster / IO）并托管 Dart VM。宿主
// （DAW）卸载插件 bundle 时，若这些线程仍在运行，而插件二进制（含 Flutter
// 消息回调所在的 C++ 代码）被 dyld 卸载/取消映射，任何后续回调都会跳进已
// 失效的内存 → 进程崩溃。
//
// 这里在插件加载时用 RTLD_NODELETE 给「本 image」打上「永不卸载」标记：
// 宿主 dlclose 后代码仍保持映射，其依赖（FlutterMacOS_X.framework、
// App.framework）也随之常驻，从根本上消除 load/unload 崩溃。代价是插件在
// 进程生命周期内不再被卸载（对已发布插件可接受）。
//
// 注：多个基于本模板的 AOT 插件「UI 串台」问题不在此处理——那是通过在
// 构建期把每个插件的 Dart 快照符号改成唯一名字（cmake/RenameDartSnapshot*
// 与 juce_flutter_link_engine 中的 POST_BUILD）解决的。
// ============================================================

#if defined(__APPLE__)

#include <dlfcn.h>

namespace
{
    // 插件加载时自动运行：把本 image 标记为永不卸载。
    __attribute__((constructor)) void jf_pin_plugin_image()
    {
        Dl_info info;
        if (dladdr(reinterpret_cast<const void*>(&jf_pin_plugin_image), &info) && info.dli_fname)
            dlopen(info.dli_fname, RTLD_NOLOAD | RTLD_NODELETE);
    }
}

#endif // __APPLE__
