// ============================================================
// app_runner.dart
// 通用 Flutter 入口点逻辑，供各插件工程的 main.dart 复用：
//   - 捕获所有 Dart 侧输出（包括框架内部 debugPrint 的调用），一并
//     汇入 AudioBridge 的统一日志缓冲区，与 C++ 侧日志合并显示在
//     CONSOLE 页面（见 debug_log_page.dart），实现"Flutter 输出 +
//     C++ 标准输出"统一控制台。
//   - 初始化 AudioBridge 平台通道通信并注入 Provider。
//   - 构建 MaterialApp（标题 / 主题色由各插件工程传入，保持插件间
//     视觉差异化）。
//
// 注意：`WidgetsFlutterBinding.ensureInitialized()` 和 `runApp()` 必须
// 在同一个 zone 里调用，否则 Flutter 会抛 "Zone mismatch" 断言——
// 所以这里把整个初始化逻辑都包进同一个 runZoned，而不是只包住 runApp。
// ============================================================

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'audio_bridge.dart';

/// 启动一个基于 Flutter UI 的 JUCE 插件应用。
///
/// [title]      MaterialApp 标题（同时用于宿主窗口标题等场景）
/// [seedColor]  Material 3 配色种子色，体现各插件的品牌色
/// [brightness] 主题明暗模式
/// [homeBuilder] 构建插件主界面（各插件工程自己的 PluginMainPage 等）
void runJuceFlutterApp({
  required String title,
  required Color seedColor,
  required Brightness brightness,
  required WidgetBuilder homeBuilder,
}) {
  AudioBridge? bridge;
  runZoned(
    () {
      WidgetsFlutterBinding.ensureInitialized();

      // 初始化平台通道通信
      bridge = AudioBridge();
      bridge!.initialize();

      runApp(
        ChangeNotifierProvider(
          create: (_) => bridge!,
          child: _JuceFlutterApp(
            title: title,
            seedColor: seedColor,
            brightness: brightness,
            homeBuilder: homeBuilder,
          ),
        ),
      );
    },
    zoneSpecification: ZoneSpecification(
      print: (self, parent, zone, line) {
        bridge?.appendDebugLog('[Dart] $line');
        parent.print(zone, line);
      },
    ),
  );
}

class _JuceFlutterApp extends StatelessWidget {
  const _JuceFlutterApp({
    required this.title,
    required this.seedColor,
    required this.brightness,
    required this.homeBuilder,
  });

  final String title;
  final Color seedColor;
  final Brightness brightness;
  final WidgetBuilder homeBuilder;

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: title,
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: seedColor,
          brightness: brightness,
        ),
        useMaterial3: true,
        // 不硬编码 fontFamily，让系统自动处理 CJK 字体回退
      ),
      home: Builder(builder: homeBuilder),
    );
  }
}
