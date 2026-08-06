import 'package:flutter/material.dart';

import 'pedal_shell.dart';

/// 面板上的二态拨动开关（mini toggle）。
///
/// 参考面板的延迟部分用的是拨钮而不是旋钮来选 MONO/STEREO 与 MS/SYNC：
/// 这两个参数在 DSP 里是布尔量（AudioParameterBool），旋钮会给出
/// 「中间值」的错觉，所以这里也用拨钮，语义与参考一致。
///
/// 上下两端各印一行丝印文字（上 = on，下 = off），拨柄停在生效的一端。
class PanelToggle extends StatelessWidget {
  const PanelToggle({
    super.key,
    required this.label,
    required this.onText,
    required this.offText,
    required this.value,
    required this.onChanged,
  });

  /// 开关本体上方的组名（如 MODE / TIME）
  final String label;

  /// value == true 时生效的档位文字（印在上方）
  final String onText;

  /// value == false 时生效的档位文字（印在下方）
  final String offText;

  final bool value;
  final ValueChanged<bool> onChanged;

  static const TextStyle _legend = TextStyle(
    color: kInk,
    fontSize: 9,
    letterSpacing: 1.1,
    fontWeight: FontWeight.w700,
  );

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label,
            style: _legend.copyWith(fontSize: 10, letterSpacing: 1.6)),
        const SizedBox(height: 3),
        Text(onText, style: _legend),
        const SizedBox(height: 2),
        _Body(value: value, onChanged: onChanged),
        const SizedBox(height: 2),
        Text(offText, style: _legend),
      ],
    );
  }
}

/// 拨钮本体：金属底座 + 斜向拨柄。
class _Body extends StatelessWidget {
  const _Body({required this.value, required this.onChanged});

  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      toggled: value,
      child: Tooltip(
        message: value ? '点击切到下档' : '点击切到上档',
        child: GestureDetector(
          onTap: () => onChanged(!value),
          child: Container(
            width: 22,
            height: 34,
            decoration: BoxDecoration(
              borderRadius: BorderRadius.circular(4),
              border: Border.all(color: const Color(0x66000000), width: 0.8),
              gradient: const LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [Color(0xffb9b6ae), Color(0xff7e7b74)],
              ),
            ),
            child: Align(
              // 拨柄倒向生效的一端
              alignment: value ? Alignment.topCenter : Alignment.bottomCenter,
              child: Container(
                width: 10,
                height: 17,
                margin: const EdgeInsets.symmetric(vertical: 2),
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(5),
                  gradient: const LinearGradient(
                    begin: Alignment.centerLeft,
                    end: Alignment.centerRight,
                    colors: [Color(0xfff4f2ec), Color(0xff8e8b84)],
                  ),
                  boxShadow: const [
                    BoxShadow(
                        color: Color(0x77000000),
                        blurRadius: 3,
                        offset: Offset(0, 1)),
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
