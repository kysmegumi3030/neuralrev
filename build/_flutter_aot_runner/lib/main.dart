import 'package:flutter/material.dart';
import 'package:juce_flutter_shell/juce_flutter_shell.dart';

import 'plugin_ui.dart';

/// Flutter 入口点
///
/// 此应用被嵌入到 JUCE 插件编辑器中运行。
/// 通过 [AudioBridge]（见 juce_flutter_shell 共享包）与宿主 C++ 侧通信。
///
/// 本工程仅设置标题与配色；其余启动逻辑（Dart 输出捕获、平台通道
/// 初始化、Provider 注入等）由共享的 [runJuceFlutterApp] 统一实现
/// （flutter_ui/packages/juce_flutter_shell，取自 JucyFlutter 模板）。
void main() {
  runJuceFlutterApp(
    title: 'NeuralRev',
    seedColor: const Color(0xffdedbd4),
    brightness: Brightness.dark,
    homeBuilder: (context) => const PluginMainPage(),
  );
}
