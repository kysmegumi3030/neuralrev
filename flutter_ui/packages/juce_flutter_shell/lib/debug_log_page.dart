// ============================================================
// debug_log_page.dart
// CONSOLE 页面 — 统一控制台，合并显示：
//   1) C++ 侧输出：FLUTTER_LOG() 宏 + juce::Logger::writeToLog()
//      （含 JUCE 内部各模块日志），经 audio_bridge/debug_log 通道推送；
//   2) Flutter/Dart 侧输出：main.dart 里用 print zone 拦截的所有
//      print()/debugPrint() 调用（标记为 [Dart] 前缀）。
// 支持实时滚动、彩色分级、清空和复制。
// ============================================================

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'audio_bridge.dart';
import 'debug_quick_controls.dart';

// ── 色彩常量（与 plugin_ui.dart 保持一致） ──────────────────
const _kBg = Color(0xff1e1e2e);
const _kPanelBg = Color(0xff181825);
const _kBorder = Color(0xff313244);
const _kTextDim = Color(0xff6c7086);

Color _lineColor(String line) {
  if (line.contains('ERROR') ||
      line.contains('Failed') ||
      line.contains('failed') ||
      line.contains('not found') ||
      line.contains('null')) {
    return const Color(0xfff38ba8); // 红 — 错误
  }
  if (line.contains('WARNING') || line.contains('WARN')) {
    return const Color(0xfffab387); // 橙 — 警告
  }
  if (line.contains('[FlutterEmbedder]')) {
    return const Color(0xffa6e3a1); // 绿 — 引擎事件
  }
  if (line.contains('[Dart]')) {
    return const Color(0xffcba6f7); // 紫 — Flutter/Dart 侧 print 输出
  }
  if (line.contains('[Flutter') || line.contains('[AudioBridge]')) {
    return const Color(0xff89dceb); // 青 — Flutter 日志
  }
  return const Color(0xffcdd6f4); // 白 — 普通
}

// ============================================================
// DebugLogPage
// ============================================================
class DebugLogPage extends StatefulWidget {
  const DebugLogPage({super.key});

  @override
  State<DebugLogPage> createState() => _DebugLogPageState();
}

class _DebugLogPageState extends State<DebugLogPage> {
  final ScrollController _scrollCtrl = ScrollController();
  bool _autoScroll = true;

  @override
  void dispose() {
    _scrollCtrl.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    if (!_scrollCtrl.hasClients) return;
    _scrollCtrl.animateTo(
      _scrollCtrl.position.maxScrollExtent,
      duration: const Duration(milliseconds: 200),
      curve: Curves.easeOut,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: _kBg,
      appBar: AppBar(
        backgroundColor: _kPanelBg,
        foregroundColor: const Color(0xffcdd6f4),
        titleTextStyle: const TextStyle(
          color: Color(0xff89b4fa),
          fontSize: 14,
          fontWeight: FontWeight.w700,
          letterSpacing: 2,
        ),
        title: const Text('CONSOLE'),
        actions: [
          // 自动滚动开关
          IconButton(
            tooltip: _autoScroll ? '关闭自动滚动' : '开启自动滚动',
            icon: Icon(
              _autoScroll
                  ? Icons.vertical_align_bottom_rounded
                  : Icons.pause_rounded,
              size: 20,
            ),
            onPressed: () => setState(() => _autoScroll = !_autoScroll),
          ),
          // 复制全部日志
          IconButton(
            tooltip: '复制全部日志',
            icon: const Icon(Icons.copy_rounded, size: 20),
            onPressed: () {
              final logs = context.read<AudioBridge>().debugLogs;
              Clipboard.setData(ClipboardData(text: logs.join('\n')));
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                  content: Text('日志已复制到剪贴板'),
                  duration: Duration(seconds: 2),
                ),
              );
            },
          ),
          // 清空日志
          IconButton(
            tooltip: '清空日志',
            icon: const Icon(Icons.delete_sweep_rounded, size: 20),
            onPressed: () => context.read<AudioBridge>().clearDebugLogs(),
          ),
          const SizedBox(width: 4),
        ],
      ),
      body: Column(
        children: [
          // 顶部性能统计条（DSP 耗时 / 帧大小 / CPU 负载 / Flutter 帧耗时）
          _PerfStatsBar(),
          // 快调控制条：自动发现 输入/输出/干湿/Bypass，常驻性能条下方一行
          const DebugQuickControls(),
          Expanded(
            child: Consumer<AudioBridge>(
              builder: (context, bridge, _) {
                final logs = bridge.debugLogs;

                // 新日志到达时自动滚动到底部
                if (_autoScroll && logs.isNotEmpty) {
                  WidgetsBinding.instance.addPostFrameCallback((_) {
                    if (_scrollCtrl.hasClients &&
                        _scrollCtrl.position.maxScrollExtent > 0) {
                      _scrollToBottom();
                    }
                  });
                }

                if (logs.isEmpty) {
                  return Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Icon(Icons.feed_outlined, size: 48, color: _kTextDim),
                        const SizedBox(height: 12),
                        Text(
                          '暂无日志\n（合并显示 C++ 标准输出与 Flutter 输出，\nProfile/Release 构建下亦有效）',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            color: _kTextDim,
                            fontSize: 12,
                            height: 1.6,
                          ),
                        ),
                      ],
                    ),
                  );
                }

                return NotificationListener<ScrollNotification>(
                  onNotification: (n) {
                    // 用户手动向上滚动时暂停自动滚动
                    if (n is UserScrollNotification) {
                      final dir = n.direction;
                      if (dir == ScrollDirection.reverse && _autoScroll) {
                        setState(() => _autoScroll = false);
                      }
                    }
                    return false;
                  },
                  child: ListView.builder(
                    controller: _scrollCtrl,
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                    itemCount: logs.length,
                    itemBuilder: (context, index) {
                      final line = logs[index];
                      return _LogLine(index: index, line: line);
                    },
                  ),
                );
              },
            ),
          ),
        ],
      ),
      // 悬浮"回到底部"按钮
      floatingActionButton: _autoScroll
          ? null
          : FloatingActionButton.small(
              backgroundColor: const Color(0xff89b4fa),
              foregroundColor: _kBg,
              tooltip: '滚动到最新日志',
              onPressed: () {
                setState(() => _autoScroll = true);
                _scrollToBottom();
              },
              child: const Icon(Icons.keyboard_double_arrow_down_rounded),
            ),
    );
  }
}

// ============================================================
// 单行日志 Widget
// ============================================================
class _LogLine extends StatelessWidget {
  const _LogLine({required this.index, required this.line});
  final int index;
  final String line;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 1),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 行号
          SizedBox(
            width: 36,
            child: Text(
              '${index + 1}',
              style: TextStyle(
                color: _kTextDim,
                fontSize: 10,
                fontFamily: 'monospace',
              ),
              textAlign: TextAlign.right,
            ),
          ),
          const SizedBox(width: 8),
          // 颜色指示条
          Container(
            width: 3,
            height: 14,
            margin: const EdgeInsets.only(top: 1),
            decoration: BoxDecoration(
              color: _lineColor(line).withValues(alpha: 0.6),
              borderRadius: BorderRadius.circular(1),
            ),
          ),
          const SizedBox(width: 6),
          // 日志内容（不硬编码 monospace，保证 CJK 字体回退正常工作）
          Expanded(
            child: SelectableText(
              line,
              style: TextStyle(
                color: _lineColor(line),
                fontSize: 11,
                height: 1.4,
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ============================================================
// 顶部性能统计条
// 监听 AudioBridge.perfStats（C++ DSP 耗时/帧大小/CPU 负载）与
// frameTimings（Flutter 构建/光栅耗时），两行等宽紧凑显示，单位纳秒。
// 独立 ValueListenableBuilder：约 30Hz 更新仅重建本条，不波及日志列表。
// ============================================================
class _PerfStatsBar extends StatelessWidget {
  const _PerfStatsBar();

  // CPU 负载配色：绿 < 50% < 橙 < 80% < 红
  static Color _loadColor(double load) {
    if (load >= 80) return const Color(0xfff38ba8);
    if (load >= 50) return const Color(0xfffab387);
    return const Color(0xffa6e3a1);
  }

  // 纳秒整数化 + 千分位分隔，避免大数难读（如 452000 → 452,000）
  static String _fmtNs(double ns) {
    final s = ns.round().toString();
    final buf = StringBuffer();
    for (int i = 0; i < s.length; i++) {
      if (i > 0 && (s.length - i) % 3 == 0) buf.write(',');
      buf.write(s[i]);
    }
    return buf.toString();
  }

  @override
  Widget build(BuildContext context) {
    final bridge = context.read<AudioBridge>();
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: const BoxDecoration(
        color: _kPanelBg,
        border: Border(bottom: BorderSide(color: _kBorder)),
      ),
      child: ValueListenableBuilder<(double, int, double)>(
        valueListenable: bridge.perfStats,
        builder: (context, perf, _) {
          final (dspNs, block, load) = perf;
          return ValueListenableBuilder<(double, double, double)>(
            valueListenable: bridge.frameTimings,
            builder: (context, frame, __) {
              final (build, raster, total) = frame;
              return Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      _stat('DSP', '${_fmtNs(dspNs)} ns',
                          const Color(0xff89dceb)),
                      _stat('Block', '$block smp', const Color(0xffcdd6f4)),
                      _stat('Load', '${load.toStringAsFixed(1)} %',
                          _loadColor(load)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      _stat('Flutter', '${_fmtNs(total)} ns',
                          const Color(0xffcba6f7)),
                      _stat('build', '${_fmtNs(build)} ns', _kTextDim),
                      _stat('raster', '${_fmtNs(raster)} ns', _kTextDim),
                    ],
                  ),
                ],
              );
            },
          );
        },
      ),
    );
  }

  Widget _stat(String label, String value, Color valueColor) {
    return Expanded(
      child: Row(
        children: [
          Text(
            '$label ',
            style: const TextStyle(
              color: _kTextDim,
              fontSize: 11,
              fontFamily: 'monospace',
            ),
          ),
          Text(
            value,
            style: TextStyle(
              color: valueColor,
              fontSize: 11,
              fontWeight: FontWeight.w600,
              fontFamily: 'monospace',
            ),
          ),
        ],
      ),
    );
  }
}
