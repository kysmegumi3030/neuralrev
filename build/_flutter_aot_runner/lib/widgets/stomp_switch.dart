import 'package:flutter/material.dart';

/// 参考面板上的踏钉开关（footswitch）+ 状态 LED。
///
/// 造型依据参考实物：一枚有金属光泽的圆形踏钉（中心受光、边缘暗），
/// 右侧一颗小 LED（启用时亮红）。
/// 语义上对应插件的 BYPASS 参数取反（踏板「亮」= 效果启用 = bypass 关）。
class StompSwitch extends StatelessWidget {
  const StompSwitch({
    super.key,
    required this.active,
    required this.onToggle,
    this.size = 40,
  });

  /// true = 效果启用（LED 亮）
  final bool active;
  final ValueChanged<bool> onToggle;
  final double size;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        // 左侧的小铭牌（参考面板上开关左边有一块方形贴片）
        Container(
          width: 18,
          height: 11,
          decoration: BoxDecoration(
            color: const Color(0xffcdcabf),
            borderRadius: BorderRadius.circular(2),
            border: Border.all(color: const Color(0x55000000), width: 0.6),
          ),
        ),
        const SizedBox(width: 14),
        // 踏钉
        Tooltip(
          message: active ? '点击旁路' : '点击启用',
          child: GestureDetector(
            onTap: () => onToggle(!active),
            child: MouseRegion(
              cursor: SystemMouseCursors.click,
              child: CustomPaint(
                size: Size.square(size),
                painter: _StompPainter(),
              ),
            ),
          ),
        ),
        const SizedBox(width: 14),
        // LED
        Container(
          width: 7,
          height: 7,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            color: active ? const Color(0xffe8412c) : const Color(0xff4a4640),
            boxShadow: active
                ? const [
                    BoxShadow(
                      color: Color(0xcce8412c),
                      blurRadius: 7,
                      spreadRadius: 1,
                    ),
                  ]
                : null,
          ),
        ),
      ],
    );
  }
}

class _StompPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = size.width * 0.5;

    // 外圈螺纹环
    canvas.drawCircle(
      c,
      r,
      Paint()
        ..shader = const LinearGradient(
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
          colors: [Color(0xffb9b6ae), Color(0xff6e6b64)],
        ).createShader(Rect.fromCircle(center: c, radius: r)),
    );

    // 内侧金属帽
    canvas.drawCircle(
      c,
      r * 0.72,
      Paint()
        ..shader = const RadialGradient(
          center: Alignment(-0.4, -0.5),
          radius: 1.1,
          colors: [Color(0xfff6f5f1), Color(0xffc9c6bd), Color(0xff8d8a82)],
          stops: [0.0, 0.5, 1.0],
        ).createShader(Rect.fromCircle(center: c, radius: r * 0.72)),
    );

    // 中心暗点（实物踏钉顶部的凹陷）
    canvas.drawCircle(
      c,
      r * 0.2,
      Paint()..color = const Color(0x33000000),
    );
  }

  @override
  bool shouldRepaint(_StompPainter oldDelegate) => false;
}
