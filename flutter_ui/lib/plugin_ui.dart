import 'package:flutter/material.dart';
import 'package:juce_flutter_shell/juce_flutter_shell.dart';
import 'package:provider/provider.dart';

import 'widgets/pedal_knob.dart';
import 'widgets/pedal_shell.dart';
import 'widgets/stomp_switch.dart';
import 'widgets/panel_toggle.dart';

/// 面板固定设计尺寸；外层用 FittedBox 等比缩放，
/// 于是任意窗口尺寸下版式与参考面板保持一致（CMakeLists 里已锁定纵横比）。
///
/// 宽度是**两块踏板并排**的和：参考插件的 Reverb 与 Delay 是链上两块独立
/// 踏板，各有自己的面板、手写体标与踏钉开关，所以这里也并排两块而不是
/// 塞进一块面板 —— 后者会让 11 个延迟参数与 5 个混响参数混在一起，
/// 和参考的物理布局对不上。
///
/// 摆放顺序 = **信号链顺序**：延迟在左、混响在右，与 DSP 里
/// delay_ → reverb_ 的处理次序一致（PluginProcessor.cpp:276-278）。
const double _kDesignW = 1500;
const double _kDesignH = 470;

class PluginMainPage extends StatelessWidget {
  const PluginMainPage({super.key});

  @override
  Widget build(BuildContext context) {
    // 禁用系统文字缩放：面板是固定像素版式，跟随系统缩放会错位
    return MediaQuery.withNoTextScaling(
      child: Scaffold(
        backgroundColor: kBackdrop,
        body: Center(
          child: FittedBox(
            fit: BoxFit.contain,
            child: SizedBox(
              width: _kDesignW,
              height: _kDesignH,
              child: const Padding(
                padding: EdgeInsets.all(22),
                // 左→右 = 信号流向。DSP 里是 delay_.process() 再
                // reverb_.process()（PluginProcessor.cpp:276-278），
                // 所以延迟在左、混响在右 —— 面板顺序必须与链序一致，
                // 否则用户按界面从左到右读到的是错的信号流。
                child: Row(
                  children: [
                    // 延迟踏板（宽，11 个控件）——在链上靠前
                    Expanded(child: _DelayPedal()),
                    SizedBox(width: 16),
                    // 混响踏板（窄，5 个旋钮）
                    SizedBox(width: 600, child: _ReverbPedal()),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

// ============================================================
// 从 param_schema 取范围/单位/默认值/skew 的旋钮工厂
// ------------------------------------------------------------
// 面板不重复硬编码任何数值，也不硬编码 skew —— 例如 PRE-DELAY 的 skew=0.6
// （实测律 ms = 1+199·n^(5/3)）、延迟 LOW PASS 的 1/2.174040 都已在参数表里，
// 这里读到即用，于是旋钮转角自动与参考插件一一对应，且不会与 DSP 侧漂移。
// ============================================================
PedalKnob _knob(
  AudioBridge bridge,
  String id,
  String label, {
  double size = 74,
  String Function(double)? fmt,
}) {
  final d = bridge.defOf(id);
  return PedalKnob(
    label: label,
    value: bridge.paramValue(id),
    min: d?.min ?? 0.0,
    max: d?.max ?? 1.0,
    defaultValue: d?.defaultValue,
    unit: d?.unit ?? '',
    skew: d?.skew ?? 1.0,
    size: size,
    valueFormatter: fmt,
    onChanged: (v) => bridge.setParam(id, v),
  );
}

// ============================================================
// 混响踏板：3 上 + 2 下旋钮、Reverb 标、踏钉开关
// ============================================================
class _ReverbPedal extends StatelessWidget {
  const _ReverbPedal();

  @override
  Widget build(BuildContext context) {
    return Consumer<AudioBridge>(
      builder: (_, bridge, __) {
        final bypassed = bridge.paramValue('bypass') > 0.5;

        return PedalShell(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(30, 16, 30, 14),
            child: Column(
              children: [
                // ---- 上排三个：DRY/WET、PRE-DELAY、DECAY ----
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    _knob(bridge, 'drywet', 'DRY/WET'),
                    _knob(bridge, 'predelay', 'PRE-DELAY'),
                    _knob(bridge, 'decay', 'DECAY'),
                  ],
                ),
                const SizedBox(height: 10),
                // ---- 下排两个（靠外侧）：LOW CUT、HIGH CUT ----
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      _knob(bridge, 'lowcut', 'LOW CUT'),
                      _knob(bridge, 'highcut', 'HIGH CUT'),
                    ],
                  ),
                ),
                const Spacer(),
                const PedalScriptLabel('Reverb'),
                const SizedBox(height: 8),
                StompSwitch(
                  active: !bypassed,
                  onToggle: (on) => bridge.setParam('bypass', on ? 0.0 : 1.0),
                ),
                const SizedBox(height: 6),
                const Text(
                  'DESIGNED BY NEURALREV',
                  style: TextStyle(
                    color: Color(0x8833312d),
                    fontSize: 5.5,
                    letterSpacing: 1.4,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

// ============================================================
// 延迟踏板
// ------------------------------------------------------------
// 11 个控件（对标参考插件延迟段的全部可调量，见 docs/REFERENCE.md §14.1）：
//   旋钮 6：DRY/WET、TIME L、TIME R、FEEDBACK、LOW PASS、HIGH PASS
//   开关 2：STEREO（Stereo/Mono）、SYNC（自由 ms / 跟随节拍）
//   同步档：NOTE（Mono Sync）或 NOTE L + NOTE R（Stereo Sync）+ TEMPO（40…240 BPM）
//   踏钉 1：DELAY 段启用（d_active）
//
// SYNC 关时 NOTE/TEMPO 对 DSP 无影响（PluginProcessor 里按 dSync_ 分支），
// 故此处把它们做成一组、SYNC 关时整组变暗 —— 让「哪一路在起作用」在
// 界面上可见，而不是让用户去猜。
// ============================================================

/// 21 档同步音符的档名。**顺序必须与 DelayTuning.h 的
/// kMeasSyncNoteFractions 完全一致**（那张表是实测的 21 档比例）；
/// 这里只负责把档号翻译成人看的字符串，不参与任何换算。
const List<String> _kSyncNoteNames = [
  '1/64T', '1/64', '1/32T', '1/64D', '1/32', '1/16T', '1/32D',
  '1/16', '1/8T', '1/16D', '1/8', '1/4T', '1/8D', '1/4',
  '1/2T', '1/4D', '1/2', '1/1T', '1/2D', '1/1', '1/1D',
];

String _noteName(double v) {
  final i = v.round().clamp(0, _kSyncNoteNames.length - 1);
  return _kSyncNoteNames[i];
}

class _DelayPedal extends StatelessWidget {
  const _DelayPedal();

  @override
  Widget build(BuildContext context) {
    return Consumer<AudioBridge>(
      builder: (_, bridge, __) {
        final active = bridge.paramValue('d_active') > 0.5;
        final synced = bridge.paramValue('d_sync') > 0.5;
        final stereo = bridge.paramValue('d_stereo') > 0.5;

        return PedalShell(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(26, 14, 26, 12),
            child: Column(
              children: [
                // ---- 上排：DRY/WET、TIME L、TIME R、FEEDBACK ----
                // SYNC 开 ⇒ TIME L/R 被 NOTE 覆盖，变暗提示
                // MONO 模式 ⇒ TIME R 无效（仅左声道），变暗提示
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    _knob(bridge, 'd_drywet', 'DRY/WET', size: 66),
                    Opacity(
                      opacity: synced ? 0.35 : 1.0,
                      child: _knob(bridge, 'd_timel', 'TIME L', size: 66),
                    ),
                    Opacity(
                      opacity: (synced || !stereo) ? 0.35 : 1.0,
                      child: _knob(bridge, 'd_timer', 'TIME R', size: 66),
                    ),
                    _knob(bridge, 'd_feedback', 'FEEDBACK', size: 66),
                  ],
                ),
                const SizedBox(height: 6),
                // ---- 下排：LOW PASS、HIGH PASS、NOTE L、NOTE R、TEMPO ----
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                    _knob(bridge, 'd_lowpass', 'LOW PASS', size: 66),
                    _knob(bridge, 'd_highpass', 'HIGH PASS', size: 66),
                    Opacity(
                      opacity: synced ? 1.0 : 0.35,
                      child: _knob(bridge, 'd_note', 'NOTE L',
                          size: 66, fmt: _noteName),
                    ),
                    Opacity(
                      opacity: (synced && stereo) ? 1.0 : 0.35,
                      child: _knob(bridge, 'd_noter', 'NOTE R',
                          size: 66, fmt: _noteName),
                    ),
                    Opacity(
                      opacity: synced ? 1.0 : 0.35,
                      child: _knob(bridge, 'd_tempo', 'TEMPO', size: 66),
                    ),
                  ],
                ),
                const Spacer(),
                const Spacer(),
                // ---- 两个面板拨钮 ----
                Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    PanelToggle(
                      label: 'MODE',
                      onText: 'STEREO',
                      offText: 'MONO',
                      value: stereo,
                      onChanged: (v) =>
                          bridge.setParam('d_stereo', v ? 1.0 : 0.0),
                    ),
                    const SizedBox(width: 26),
                    PanelToggle(
                      label: 'TIME',
                      onText: 'SYNC',
                      offText: 'MS',
                      value: synced,
                      onChanged: (v) =>
                          bridge.setParam('d_sync', v ? 1.0 : 0.0),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                const PedalScriptLabel('Delay', fontSize: 26),
                const SizedBox(height: 6),
                StompSwitch(
                  active: active,
                  onToggle: (on) =>
                      bridge.setParam('d_active', on ? 1.0 : 0.0),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}
