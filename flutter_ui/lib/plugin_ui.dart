import 'package:flutter/material.dart';
import 'package:juce_flutter_shell/juce_flutter_shell.dart';
import 'package:provider/provider.dart';

import 'widgets/pedal_knob.dart';
import 'widgets/stomp_switch.dart';

// ============================================================
// 配色 —— 对标参考插件 Reverb 踏板的实物外观
// （深炭灰机身 + 银白面板 + 奶油色尖头旋钮 + 手写体 "Reverb" 标）
// ============================================================
const _kBackdrop = Color(0xff1b1b1d); // 插件窗口底色
const _kShell = Color(0xff3a3733); // 踏板外壳（深炭灰）
const _kShellEdge = Color(0xff26241f);
const _kFace = Color(0xffdedbd4); // 面板银白
const _kFaceHi = Color(0xfff2f0ea);
const _kFaceLo = Color(0xffbfbcb2);
const _kInk = Color(0xff33312d); // 面板丝印

/// 面板固定设计尺寸；外层用 FittedBox 等比缩放，
/// 于是任意窗口尺寸下版式与参考面板保持一致（CMakeLists 里已锁定纵横比）。
const double _kDesignW = 760;
const double _kDesignH = 460;

class PluginMainPage extends StatelessWidget {
  const PluginMainPage({super.key});

  @override
  Widget build(BuildContext context) {
    // 禁用系统文字缩放：面板是固定像素版式，跟随系统缩放会错位
    return MediaQuery.withNoTextScaling(
      child: Scaffold(
        backgroundColor: _kBackdrop,
        body: Center(
          child: FittedBox(
            fit: BoxFit.contain,
            child: SizedBox(
              width: _kDesignW,
              height: _kDesignH,
              child: const _Pedal(),
            ),
          ),
        ),
      ),
    );
  }
}

// ============================================================
// 踏板本体
// ============================================================
class _Pedal extends StatelessWidget {
  const _Pedal();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(26),
      child: DecoratedBox(
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(26),
          gradient: const LinearGradient(
            begin: Alignment.topCenter,
            end: Alignment.bottomCenter,
            colors: [_kShell, _kShellEdge],
          ),
          boxShadow: const [
            BoxShadow(color: Color(0x99000000), blurRadius: 22, offset: Offset(0, 10)),
          ],
        ),
        child: Padding(
          // 外壳边框宽度 —— 参考实物是一圈明显的深色包边
          padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
          child: DecoratedBox(
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(14),
              gradient: const LinearGradient(
                begin: Alignment(-0.6, -1),
                end: Alignment(0.5, 1),
                colors: [_kFaceHi, _kFace, _kFaceLo],
                stops: [0.0, 0.45, 1.0],
              ),
              border: Border.all(color: const Color(0x33000000), width: 0.8),
            ),
            child: const _Face(),
          ),
        ),
      ),
    );
  }
}

// ============================================================
// 面板内容：3 上 + 2 下旋钮、Reverb 标、踏钉开关
// ============================================================
class _Face extends StatelessWidget {
  const _Face();

  @override
  Widget build(BuildContext context) {
    return Consumer<AudioBridge>(
      builder: (_, bridge, __) {
        // 范围/单位/默认值/skew 全部取自 param_schema（即 PluginParameters.cpp 的表）。
        // 面板不重复硬编码任何数值，也不硬编码 skew —— PRE-DELAY 的 skew=0.6
        // 已在参数表里（对应实测律 ms = 1+199·n^(5/3)），这里读到即用，
        // 于是旋钮转角自动与参考插件一一对应，且不会与 DSP 侧漂移。
        PedalKnob knob(String id, String label) {
          final d = bridge.defOf(id);
          return PedalKnob(
            label: label,
            value: bridge.paramValue(id),
            min: d?.min ?? 0.0,
            max: d?.max ?? 1.0,
            defaultValue: d?.defaultValue,
            unit: d?.unit ?? '',
            skew: d?.skew ?? 1.0,
            size: 74,
            onChanged: (v) => bridge.setParam(id, v),
          );
        }

        final bypassed = bridge.paramValue('bypass') > 0.5;

        return Padding(
          padding: const EdgeInsets.fromLTRB(30, 16, 30, 14),
          child: Column(
            children: [
              // ---- 上排三个：DRY/WET、PRE-DELAY、DECAY ----
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                children: [
                  knob('drywet', 'DRY/WET'),
                  knob('predelay', 'PRE-DELAY'),
                  knob('decay', 'DECAY'),
                ],
              ),
              const SizedBox(height: 10),
              // ---- 下排两个（靠外侧）：LOW CUT、HIGH CUT ----
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 6),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    knob('lowcut', 'LOW CUT'),
                    knob('highcut', 'HIGH CUT'),
                  ],
                ),
              ),
              const Spacer(),
              // ---- 手写体标识 ----
              const Text(
                'Reverb',
                style: TextStyle(
                  color: _kInk,
                  fontSize: 30,
                  fontStyle: FontStyle.italic,
                  fontFamily: 'Georgia',
                  fontWeight: FontWeight.w500,
                  letterSpacing: 1.2,
                ),
              ),
              const SizedBox(height: 8),
              // ---- 踏钉开关 + LED ----
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
        );
      },
    );
  }
}
