import 'package:flutter/material.dart';

// ============================================================
// 配色 —— 对标参考插件踏板的实物外观
// （深炭灰机身 + 银白面板 + 奶油色尖头旋钮 + 手写体标）
// 从 plugin_ui.dart 提到这里：混响与延迟两块踏板共用同一套材质，
// 各自复制一份会在改配色时漂移。
// ============================================================
const kBackdrop = Color(0xff1b1b1d); // 插件窗口底色
const kShell = Color(0xff3a3733); // 踏板外壳（深炭灰）
const kShellEdge = Color(0xff26241f);
const kFaceCream = Color(0xffdedbd4); // 面板银白
const kFaceHi = Color(0xfff2f0ea);
const kFaceLo = Color(0xffbfbcb2);
const kInk = Color(0xff33312d); // 面板丝印

/// 一块踏板的外壳 + 面板底：深色包边内嵌一块受光的银白面板。
///
/// 只负责「机身」，面板上放什么由 [child] 决定 —— 于是混响与延迟两块
/// 踏板的**外观**由同一段代码保证一致，差异只在各自的旋钮版式。
class PedalShell extends StatelessWidget {
  const PedalShell({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(26),
        gradient: const LinearGradient(
          begin: Alignment.topCenter,
          end: Alignment.bottomCenter,
          colors: [kShell, kShellEdge],
        ),
        boxShadow: const [
          BoxShadow(
              color: Color(0x99000000), blurRadius: 22, offset: Offset(0, 10)),
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
              colors: [kFaceHi, kFaceCream, kFaceLo],
              stops: [0.0, 0.45, 1.0],
            ),
            border: Border.all(color: const Color(0x33000000), width: 0.8),
          ),
          child: child,
        ),
      ),
    );
  }
}

/// 面板下方的手写体标（"Reverb" / "Delay"）
class PedalScriptLabel extends StatelessWidget {
  const PedalScriptLabel(this.text, {super.key, this.fontSize = 30});

  final String text;
  final double fontSize;

  @override
  Widget build(BuildContext context) {
    return Text(
      text,
      style: TextStyle(
        color: kInk,
        fontSize: fontSize,
        fontStyle: FontStyle.italic,
        fontFamily: 'Georgia',
        fontWeight: FontWeight.w500,
        letterSpacing: 1.2,
      ),
    );
  }
}
