/// juce_flutter_shell
///
/// JUCE + Flutter 插件模板的通用「壳」代码：JUCE ↔ Flutter 平台通道桥接
/// （[AudioBridge] / [ParamDef]）、调试控制台页面（[DebugLogPage]）与
/// 应用入口启动器（[runJuceFlutterApp]）。
///
/// 本包与具体插件的 DSP 算法、UI 外观无关，由 JucyFlutter 模板仓库维护，
/// 供下游插件工程以 git submodule + path 依赖方式复用，避免各插件各自
/// 维护一份逐渐分叉的拷贝。
library juce_flutter_shell;

export 'audio_bridge.dart';
export 'debug_log_page.dart';
export 'debug_quick_controls.dart';
export 'app_runner.dart';
