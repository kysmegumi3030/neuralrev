import 'dart:math' as math;

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// 参考插件 Reverb 踏板上的**奶油色尖头旋钮**（chicken-head / pointer knob）。
///
/// 造型依据参考面板：象牙白本体、顶部深色扇形指针从中心指向外缘、
/// 底部深色底座环、旋钮上方一枚小三角刻度标记与全大写细体标签。
///
/// 交互沿用模板 [MetalKnob] 的约定，保证操作手感与工程其它部分一致：
///   * 竖向拖动改值（按住 Shift 精细十倍）
///   * 双击回默认值
///   * 滚轮微调
/// 值域与单位由调用方给出（来自 AudioBridge 的 param_schema，即插件参数表）。
class PedalKnob extends StatefulWidget {
  const PedalKnob({
    super.key,
    required this.label,
    required this.value,
    required this.min,
    required this.max,
    required this.onChanged,
    this.defaultValue,
    this.unit = '',
    this.size = 64.0,
    this.skew = 1.0,
    this.valueFormatter,
  });

  final String label;
  final double value;
  final double min;
  final double max;
  final ValueChanged<double> onChanged;

  /// 双击回归的默认值（不给则取 min..max 中点）
  final double? defaultValue;

  final String unit;
  final double size;

  /// 与 JUCE NormalisableRange 的 skew 一致：
  /// 归一比例 p 与真实值的关系为 value = min + (max−min)·p^(1/skew)。
  /// PRE-DELAY 的实测律是 ms = 1 + 199·n^(5/3)，对应 skew = 0.6，
  /// 传入后旋钮的**转角**与参考插件的旋钮位置一一对应。
  final double skew;

  final String Function(double value)? valueFormatter;

  @override
  State<PedalKnob> createState() => _PedalKnobState();
}

class _PedalKnobState extends State<PedalKnob> {
  double? _dragStartY;
  double? _valueAtDragStart;
  bool _hovering = false;
  bool _editing = false;
  late final TextEditingController _textController;
  late final FocusNode _focusNode;

  @override
  void initState() {
    super.initState();
    _textController = TextEditingController(text: _display);
    _focusNode = FocusNode();
    _focusNode.addListener(_onFocusChange);
  }

  @override
  void didUpdateWidget(covariant PedalKnob oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_editing && oldWidget.value != widget.value) {
      _textController.text = _display;
    }
  }

  @override
  void dispose() {
    _focusNode.removeListener(_onFocusChange);
    _focusNode.dispose();
    _textController.dispose();
    super.dispose();
  }

  void _onFocusChange() {
    if (!_focusNode.hasFocus && _editing) {
      _commitEdit();
    }
  }

  void _startEdit() {
    setState(() {
      _editing = true;
      _textController.text = _display;
    });
    _focusNode.requestFocus();
    _textController.selection = TextSelection(
      baseOffset: 0,
      extentOffset: _textController.text.length,
    );
  }

  void _commitEdit() {
    final raw = _textController.text.trim();
    final parsed = double.tryParse(raw);
    setState(() => _editing = false);
    if (parsed != null) {
      widget.onChanged(parsed.clamp(widget.min, widget.max));
    } else {
      _textController.text = _display;
    }
  }

  void _cancelEdit() {
    setState(() => _editing = false);
    _textController.text = _display;
    _focusNode.unfocus();
  }

  // 旋钮可转范围：与实体旋钮一致的 ~300°（7 点钟到 5 点钟）
  static const double _minAngle = -2.618; // -150°
  static const double _maxAngle = 2.618; //  150°

  double get _proportion {
    final t = ((widget.value - widget.min) / (widget.max - widget.min))
        .clamp(0.0, 1.0);
    // value = min + span·p^(1/skew)  ⇒  p = t^skew
    return widget.skew == 1.0 ? t : math.pow(t, widget.skew).toDouble();
  }

  double _valueFromProportion(double p) {
    final c = p.clamp(0.0, 1.0);
    final t = widget.skew == 1.0
        ? c
        : math.pow(c, 1.0 / widget.skew).toDouble();
    return widget.min + (widget.max - widget.min) * t;
  }

  void _onDragStart(DragStartDetails d) {
    _dragStartY = d.globalPosition.dy;
    _valueAtDragStart = widget.value;
  }

  void _onDragUpdate(DragUpdateDetails d) {
    if (_dragStartY == null || _valueAtDragStart == null) return;
    final fine = HardwareKeyboard.instance.isShiftPressed;
    // 150 px 走满全程；精细模式 10 倍
    final span = fine ? 1500.0 : 150.0;
    final dy = _dragStartY! - d.globalPosition.dy;
    final startP = widget.skew == 1.0
        ? ((_valueAtDragStart! - widget.min) / (widget.max - widget.min))
        : math
            .pow(
              ((_valueAtDragStart! - widget.min) /
                      (widget.max - widget.min))
                  .clamp(0.0, 1.0),
              widget.skew,
            )
            .toDouble();
    widget.onChanged(_valueFromProportion(startP + dy / span));
  }

  void _onDragEnd(DragEndDetails _) {
    _dragStartY = null;
    _valueAtDragStart = null;
  }

  String get _display {
    if (widget.valueFormatter != null) return widget.valueFormatter!(widget.value);
    final v = widget.value;
    // 位数按量级自适应，避免 "10000.0 Hz" 这类冗余
    final s = v.abs() >= 1000
        ? v.toStringAsFixed(0)
        : (v.abs() >= 100 ? v.toStringAsFixed(0) : v.toStringAsFixed(2));
    return widget.unit.isEmpty ? s : '$s ${widget.unit}';
  }

  @override
  Widget build(BuildContext context) {
    final knob = GestureDetector(
      onVerticalDragStart: _onDragStart,
      onVerticalDragUpdate: _onDragUpdate,
      onVerticalDragEnd: _onDragEnd,
      onDoubleTap: () => widget.onChanged(
        widget.defaultValue ?? (widget.min + widget.max) * 0.5,
      ),
      child: Listener(
        onPointerSignal: (e) {
          if (e is PointerScrollEvent) {
            final step = HardwareKeyboard.instance.isShiftPressed ? 0.002 : 0.02;
            widget.onChanged(
              _valueFromProportion(_proportion - e.scrollDelta.dy.sign * step),
            );
          }
        },
        child: MouseRegion(
          onEnter: (_) => setState(() => _hovering = true),
          onExit: (_) => setState(() => _hovering = false),
          cursor: SystemMouseCursors.resizeUpDown,
          child: CustomPaint(
            size: Size.square(widget.size),
            painter: _PedalKnobPainter(
              angle: _minAngle + (_maxAngle - _minAngle) * _proportion,
            ),
          ),
        ),
      ),
    );

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        // 顶部刻度小三角（与参考面板一致）
        CustomPaint(
          size: const Size(8, 5),
          painter: _TickPainter(),
        ),
        const SizedBox(height: 2),
        Text(
          widget.label,
          textAlign: TextAlign.center,
          style: const TextStyle(
            color: Color(0xff3c3c3c),
            fontSize: 7.5,
            height: 1.1,
            letterSpacing: 0.9,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 4),
        knob,
        const SizedBox(height: 3),
        // 点按数值输入框：始终显示，点击后弹出键盘直接输入
        SizedBox(
          width: widget.size,
          child: _editing
              ? SizedBox(
                  height: 18,
                  child: TextField(
                    controller: _textController,
                    focusNode: _focusNode,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      color: Color(0xff2b2b2b),
                      fontSize: 8,
                      fontWeight: FontWeight.w600,
                      height: 1.2,
                    ),
                    decoration: const InputDecoration(
                      contentPadding: EdgeInsets.zero,
                      border: OutlineInputBorder(
                        borderSide:
                            BorderSide(color: Color(0xff888888), width: 0.5),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderSide:
                            BorderSide(color: Color(0xff444444), width: 0.5),
                      ),
                      isDense: true,
                    ),
                    keyboardType:
                        const TextInputType.numberWithOptions(decimal: true),
                    onSubmitted: (_) => _commitEdit(),
                    onTapOutside: (_) => _commitEdit(),
                  ),
                )
              : GestureDetector(
                  onTap: _startEdit,
                  child: Opacity(
                    opacity: _hovering || _dragStartY != null ? 1.0 : 0.0,
                    child: Stack(
                      alignment: Alignment.center,
                      children: [
                        // 透明占位保证布局稳定
                        const Text(
                          ' ',
                          style: TextStyle(
                            fontSize: 8,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        Text(
                          _display,
                          style: const TextStyle(
                            color: Color(0xff2b2b2b),
                            fontSize: 8,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
        ),
      ],
    );
  }
}

/// 奶油色尖头旋钮的绘制
class _PedalKnobPainter extends CustomPainter {
  _PedalKnobPainter({required this.angle});

  final double angle;

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = size.width * 0.5;

    // 投影
    canvas.drawCircle(
      c.translate(0, r * 0.06),
      r * 0.96,
      Paint()
        ..color = const Color(0x33000000)
        ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 2.5),
    );

    // 底座环（深色）
    canvas.drawCircle(
      c,
      r * 0.95,
      Paint()..color = const Color(0xff4a4642),
    );

    // 本体：象牙白，左上受光
    canvas.drawCircle(
      c,
      r * 0.86,
      Paint()
        ..shader = const RadialGradient(
          center: Alignment(-0.35, -0.45),
          radius: 1.15,
          colors: [Color(0xfffdfcf7), Color(0xffeae5d8), Color(0xffcfc8b6)],
          stops: [0.0, 0.55, 1.0],
        ).createShader(Rect.fromCircle(center: c, radius: r * 0.86)),
    );

    // 指针：从中心向外的深色扇形（参考面板上是一个收窄的楔形）
    const wedge = 0.30; // 弧度半宽
    final path = Path()..moveTo(c.dx, c.dy);
    final tip = r * 0.84;
    path.arcTo(
      Rect.fromCircle(center: c, radius: tip),
      angle - math.pi / 2 - wedge,
      wedge * 2,
      false,
    );
    path.close();
    canvas.drawPath(
      path,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xff7a746a), Color(0xff544f47)],
        ).createShader(Rect.fromCircle(center: c, radius: tip)),
    );

    // 中心轴帽
    canvas.drawCircle(
      c,
      r * 0.13,
      Paint()..color = const Color(0xffe8e3d6),
    );
  }

  @override
  bool shouldRepaint(_PedalKnobPainter old) => old.angle != angle;
}

/// 旋钮上方的小三角刻度
class _TickPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final p = Path()
      ..moveTo(size.width * 0.5, size.height)
      ..lineTo(0, 0)
      ..lineTo(size.width, 0)
      ..close();
    canvas.drawPath(p, Paint()..color = const Color(0xff59544c));
  }

  @override
  bool shouldRepaint(_TickPainter oldDelegate) => false;
}
