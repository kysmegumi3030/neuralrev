// ============================================================
// debug_quick_controls.dart
// CONSOLE 页面的「快调控制条」——框架自带、六个插件统一。
//
// 在不返回主界面的前提下，于调试页直接调节最常用的三个参数
// （输入音量 / 输出音量 / 干湿比）并提供一个 Bypass 开关，
// 方便调试时快速 A/B 对比。
//
// 关键设计：各插件的这几个参数 ID 各不相同（如 input_gain / input，
// dry_wet_mix / dry_wet / mix / fxMix …），本组件通过「候选 ID 表」
// 从 AudioBridge 已持有的 schema 中【自动发现】，命中即渲染、
// 未命中则隐藏对应控件——因此下游插件【零配置】接入。
// ============================================================

import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/gestures.dart';
import 'package:provider/provider.dart';

import 'audio_bridge.dart';

// ── 色彩常量（与 debug_log_page.dart 保持一致） ──────────────
const _kPanelBg = Color(0xff181825);
const _kBorder = Color(0xff313244);
const _kTextDim = Color(0xff6c7086);
const _kLabel = Color(0xffcdd6f4);
const _kAccent = Color(0xff89dceb); // 青
const _kAccentAlt = Color(0xffcba6f7); // 紫

// ── 自动发现候选 ID 表（按优先级，命中第一个存在的即用） ─────
// 顺序即优先级：更「标准」的命名排前。
const List<String> _kInputIds = [
  'input_gain', 'input', 'in_gain', 'inputGain', 'input_level', 'inGain',
];
const List<String> _kOutputIds = [
  'output_gain', 'output', 'outputVolume', 'output_db', 'out_gain',
  'outputGain', 'output_level', 'outGain', 'gain',
];
const List<String> _kMixIds = [
  'dry_wet_mix', 'dry_wet', 'mix', 'fxMix', 'drywet', 'wet',
  'dry_wet_ratio', 'fx_mix', 'dryWet',
];
const List<String> _kBypassIds = [
  'bypass', 'power', 'on_off', 'onoff', 'enabled',
];

/// CONSOLE 页面的快调控制条。
///
/// 自动从 [AudioBridge] 的 schema 里发现 输入 / 输出 / 干湿 / Bypass
/// 四类参数并渲染对应控件；一个都发现不到时整条隐藏（不占位）。
class DebugQuickControls extends StatelessWidget {
  const DebugQuickControls({super.key});

  /// 在候选表中找第一个存在于 schema、且满足 [wantBoolean] 约束的参数 id。
  static String? _discover(
    AudioBridge bridge,
    List<String> candidates, {
    required bool wantBoolean,
  }) {
    for (final id in candidates) {
      final def = bridge.defOf(id);
      if (def != null && def.isBoolean == wantBoolean) return id;
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    // watch：schema 到达 / 参数值变化时重建本条。
    final bridge = context.watch<AudioBridge>();

    final inputId = _discover(bridge, _kInputIds, wantBoolean: false);
    final outputId = _discover(bridge, _kOutputIds, wantBoolean: false);
    final mixId = _discover(bridge, _kMixIds, wantBoolean: false);
    final bypassId = _discover(bridge, _kBypassIds, wantBoolean: true);

    // Bypass 与 output 都可能命中 "gain"（模板工程）；避免同一 id 双绑。
    final knobs = <Widget>[
      if (inputId != null)
        _DebugKnob(bridge: bridge, id: inputId, fallbackLabel: 'INPUT'),
      if (outputId != null && outputId != inputId)
        _DebugKnob(bridge: bridge, id: outputId, fallbackLabel: 'OUTPUT'),
      if (mixId != null && mixId != inputId && mixId != outputId)
        _DebugKnob(bridge: bridge, id: mixId, fallbackLabel: 'DRY/WET'),
    ];

    // 什么都没发现 → 不渲染，也不占位。
    if (knobs.isEmpty && bypassId == null) return const SizedBox.shrink();

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 10),
      decoration: const BoxDecoration(
        color: _kPanelBg,
        border: Border(bottom: BorderSide(color: _kBorder)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // 小标题
          const Padding(
            padding: EdgeInsets.only(bottom: 6, left: 2),
            child: Text(
              'QUICK CONTROLS',
              style: TextStyle(
                color: _kTextDim,
                fontSize: 9,
                fontWeight: FontWeight.w700,
                letterSpacing: 2,
                fontFamily: 'monospace',
              ),
            ),
          ),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              for (final k in knobs) Expanded(child: Center(child: k)),
              if (bypassId != null)
                Expanded(
                  child: Center(
                    child: _DebugToggle(bridge: bridge, id: bypassId),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }
}

// ============================================================
// _DebugKnob — 轻量通用旋钮
// 手感对齐各插件私有 knob：垂直拖拽（向上增大）、双击复位、滚轮微调；
// 数值经 schema 范围 clamp（由 AudioBridge.setParam 完成）。
// ============================================================
class _DebugKnob extends StatefulWidget {
  const _DebugKnob({
    required this.bridge,
    required this.id,
    required this.fallbackLabel,
  });

  final AudioBridge bridge;
  final String id;
  final String fallbackLabel;

  @override
  State<_DebugKnob> createState() => _DebugKnobState();
}

class _DebugKnobState extends State<_DebugKnob> {
  double? _valueAtDragStart;
  double? _dragStartY;

  ParamDef get _def =>
      widget.bridge.defOf(widget.id) ??
      ParamDef(
        id: widget.id,
        label: widget.fallbackLabel,
        unit: '',
        min: 0,
        max: 1,
        defaultValue: 0,
        skew: 1.0,
        step: 0,
        isBoolean: false,
        uiHint: 'knob',
      );

  double get _value => widget.bridge.paramValue(widget.id);

  double _norm(double v, ParamDef d) =>
      ((v - d.min) / (d.max - d.min)).clamp(0.0, 1.0);

  bool _isBipolar(ParamDef d) => d.min < 0 && d.max > 0;

  void _set(double v, ParamDef d) =>
      widget.bridge.setParam(widget.id, v.clamp(d.min, d.max));

  void _onPanStart(DragStartDetails d) {
    _dragStartY = d.globalPosition.dy;
    _valueAtDragStart = _value;
  }

  void _onPanUpdate(DragUpdateDetails d) {
    if (_dragStartY == null || _valueAtDragStart == null) return;
    final def = _def;
    final deltaPx = _dragStartY! - d.globalPosition.dy; // 向上为正
    const sensitivity = 0.005;
    final range = def.max - def.min;
    _set(_valueAtDragStart! + deltaPx * sensitivity * range, def);
  }

  void _onPanEnd(DragEndDetails _) {
    _dragStartY = null;
    _valueAtDragStart = null;
  }

  void _onScroll(PointerScrollEvent e) {
    final def = _def;
    const step = 0.02;
    final delta = e.scrollDelta.dy < 0 ? step : -step;
    final n = (_norm(_value, def) + delta).clamp(0.0, 1.0);
    _set(def.min + n * (def.max - def.min), def);
  }

  String _fmtValue(ParamDef d) {
    final v = _value;
    // 步进为整数（如 bit/档位）显示整数，否则两位小数。
    final precise = d.step >= 1.0 ? v.round().toString() : v.toStringAsFixed(2);
    return d.unit.isEmpty ? precise : '$precise ${d.unit}';
  }

  @override
  Widget build(BuildContext context) {
    final def = _def;
    final norm = _norm(_value, def);
    final bipolar = _isBipolar(def);

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Listener(
          onPointerSignal: (e) {
            if (e is PointerScrollEvent) _onScroll(e);
          },
          child: GestureDetector(
            onPanStart: _onPanStart,
            onPanUpdate: _onPanUpdate,
            onPanEnd: _onPanEnd,
            onDoubleTap: () => _set(def.defaultValue, def),
            child: SizedBox(
              width: 46,
              height: 46,
              child: CustomPaint(
                painter: _DebugKnobPainter(norm: norm, bipolar: bipolar),
              ),
            ),
          ),
        ),
        const SizedBox(height: 5),
        Text(
          def.label,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: _kLabel,
            fontSize: 9,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.4,
          ),
        ),
        const SizedBox(height: 1),
        Text(
          _fmtValue(def),
          style: const TextStyle(
            color: _kTextDim,
            fontSize: 8.5,
            fontFeatures: [FontFeature.tabularFigures()],
          ),
        ),
      ],
    );
  }
}

class _DebugKnobPainter extends CustomPainter {
  const _DebugKnobPainter({required this.norm, required this.bipolar});

  final double norm;
  final bool bipolar;

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = math.min(size.width, size.height) / 2 - 6;

    const start = math.pi * 0.75; // 135°（左下）
    const sweepFull = math.pi * 1.5; // 270°
    final arcRect = Rect.fromCircle(center: center, radius: radius + 4);

    // 背景弧
    canvas.drawArc(
      arcRect,
      start,
      sweepFull,
      false,
      Paint()
        ..color = _kAccent.withValues(alpha: 0.20)
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.0
        ..strokeCap = StrokeCap.round,
    );
    // 进度弧
    if (bipolar) {
      final mid = start + sweepFull * 0.5;
      canvas.drawArc(
        arcRect,
        mid,
        (norm - 0.5) * sweepFull,
        false,
        Paint()
          ..color = _kAccent
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.0
          ..strokeCap = StrokeCap.round,
      );
    } else {
      canvas.drawArc(
        arcRect,
        start,
        sweepFull * norm,
        false,
        Paint()
          ..color = _kAccent
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2.0
          ..strokeCap = StrokeCap.round,
      );
    }

    // 盘面（径向渐变）
    const face = Color(0xff2a2a3c);
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..shader = RadialGradient(
          center: const Alignment(-0.3, -0.4),
          colors: [
            Color.lerp(face, Colors.white, 0.10)!,
            face,
            Color.lerp(face, Colors.black, 0.30)!,
          ],
          stops: const [0.0, 0.6, 1.0],
        ).createShader(Rect.fromCircle(center: center, radius: radius)),
    );
    canvas.drawCircle(
      center,
      radius,
      Paint()
        ..color = _kBorder
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.0,
    );

    // 指示线：value 角映射到 -135°..+135°（12 点为 0），再转到画布坐标系
    const minAngle = -2.3562; // -135°
    const maxAngle = 2.3562; //  135°
    final a = (minAngle + norm * (maxAngle - minAngle)) - math.pi / 2;
    final p1 = Offset(
      center.dx + radius * 0.28 * math.cos(a),
      center.dy + radius * 0.28 * math.sin(a),
    );
    final p2 = Offset(
      center.dx + radius * 0.82 * math.cos(a),
      center.dy + radius * 0.82 * math.sin(a),
    );
    canvas.drawLine(
      p1,
      p2,
      Paint()
        ..color = Colors.white.withValues(alpha: 0.92)
        ..strokeWidth = math.max(1.6, radius * 0.09)
        ..strokeCap = StrokeCap.round,
    );
  }

  @override
  bool shouldRepaint(_DebugKnobPainter old) =>
      old.norm != norm || old.bipolar != bipolar;
}

// ============================================================
// _DebugToggle — 小型开关（绑定布尔参数，通常为 Bypass/Power）
// 直接以参数原义 label 显示，避免 power/on_off 与 bypass 语义反相造成误导。
// ============================================================
class _DebugToggle extends StatelessWidget {
  const _DebugToggle({required this.bridge, required this.id});

  final AudioBridge bridge;
  final String id;

  @override
  Widget build(BuildContext context) {
    final def = bridge.defOf(id);
    final label = def?.label.isNotEmpty == true ? def!.label : id.toUpperCase();
    final on = bridge.paramValue(id) > 0.5;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        GestureDetector(
          onTap: () => bridge.setParam(id, on ? 0.0 : 1.0),
          child: AnimatedContainer(
            duration: const Duration(milliseconds: 120),
            width: 44,
            height: 24,
            padding: const EdgeInsets.all(2),
            decoration: BoxDecoration(
              color: on
                  ? _kAccentAlt.withValues(alpha: 0.35)
                  : const Color(0xff2a2a3c),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: on ? _kAccentAlt : _kBorder,
                width: 1,
              ),
            ),
            child: Align(
              alignment: on ? Alignment.centerRight : Alignment.centerLeft,
              child: Container(
                width: 18,
                height: 18,
                decoration: BoxDecoration(
                  color: on ? _kAccentAlt : _kTextDim,
                  shape: BoxShape.circle,
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: 5),
        Text(
          label,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: _kLabel,
            fontSize: 9,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.4,
          ),
        ),
        const SizedBox(height: 1),
        Text(
          on ? 'ON' : 'OFF',
          style: const TextStyle(
            color: _kTextDim,
            fontSize: 8.5,
            fontFeatures: [FontFeature.tabularFigures()],
          ),
        ),
      ],
    );
  }
}
