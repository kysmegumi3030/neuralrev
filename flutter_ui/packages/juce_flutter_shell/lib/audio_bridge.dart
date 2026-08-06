import 'dart:convert';
import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/foundation.dart';
import 'package:flutter/scheduler.dart';
import 'package:flutter/services.dart';

/// 扩展通道 leaf 常量，保持与 C++ FlutterExtensionChannels 一致。
class AudioBridgeExtensionLeaf {
  static const String spectrum = 'spectrum';
  static const String presetBrowser = 'preset_browser';
  static const String taskStatus = 'task_status';
}

typedef ExtensionHandler = FutureOr<Map<String, dynamic>?> Function(
  String method,
  Map<String, dynamic> args,
);

// -------------------------------------------------------
// 参数元数据（与 C++ PluginProcessor::ParameterDef 镜像）
//
// Dart 侧内置与 C++ PARAM_DEFS 相同的默认值作为"冷启动"状态，
// C++ 推送的 param_schema 消息到达后会覆盖这里的值。
// 因此 UI 在 schema 到达前仍能正确渲染。
// -------------------------------------------------------
class ParamDef {
  const ParamDef({
    required this.id,
    required this.label,
    required this.unit,
    required this.min,
    required this.max,
    required this.defaultValue,
    required this.skew,
    required this.step,
    required this.isBoolean,
    required this.uiHint,
  });

  final String id;
  final String label;
  final String unit;
  final double min;
  final double max;
  final double defaultValue;
  final double skew; // 1.0 = 线性；<1 = 对数感知（与 NormalisableRange skew 对应）
  final double step; // 0 = 连续
  final bool isBoolean;
  final String uiHint; // "knob" | "toggle" | "hidden"

  ParamDef copyWith({
    double? min,
    double? max,
    double? defaultValue,
    double? skew,
    double? step,
    String? label,
    String? unit,
    bool? isBoolean,
    String? uiHint,
  }) =>
      ParamDef(
        id: id,
        label: label ?? this.label,
        unit: unit ?? this.unit,
        min: min ?? this.min,
        max: max ?? this.max,
        defaultValue: defaultValue ?? this.defaultValue,
        skew: skew ?? this.skew,
        step: step ?? this.step,
        isBoolean: isBoolean ?? this.isBoolean,
        uiHint: uiHint ?? this.uiHint,
      );

  static ParamDef fromJson(Map<String, dynamic> j) => ParamDef(
        id: j['id'] as String,
        label: j['label'] as String,
        unit: j['unit'] as String,
        min: (j['min'] as num).toDouble(),
        max: (j['max'] as num).toDouble(),
        defaultValue: (j['default'] as num).toDouble(),
        skew: (j['skew'] as num).toDouble(),
        step: (j['step'] as num).toDouble(),
        isBoolean: j['bool'] as bool,
        uiHint: j['ui'] as String,
      );
}

/// JUCE ↔ Flutter 参数桥接
///
/// 通过 Platform Channel 与宿主 JUCE 插件通信：
///   - [_channelParamSchema] : JUCE → Flutter，一次性推送参数元数据
///   - [_channelParamUpdate] : JUCE → Flutter，推送参数值变化
///   - [_channelParamSet]    : Flutter → JUCE，设置参数值
///   - [_channelMeterUpdate] : JUCE → Flutter，推送 VU 电平
class AudioBridge extends ChangeNotifier {
  // 通道基础名（与 C++ AudioParameterBridge 常量一致）
  static const String _channelParamSchema = 'audio_bridge/param_schema';
  static const String _channelParamUpdate = 'audio_bridge/param_update';
  static const String _channelParamSet = 'audio_bridge/param_set';
  static const String _channelMeterUpdate = 'audio_bridge/meter_update';
  static const String _channelPerfUpdate = 'audio_bridge/perf_update';
  static const String _channelRequestSync = 'audio_bridge/request_sync';
  static const String _channelDebugLog = 'audio_bridge/debug_log';
  static const String _channelBootstrap = 'audio_bridge/bootstrap';

  final _bootstrapChannel = const BasicMessageChannel<String>(
    _channelBootstrap,
    StringCodec(),
  );

  late BasicMessageChannel<String> _paramSchemaChannel;
  late BasicMessageChannel<String> _paramUpdateChannel;
  late BasicMessageChannel<String> _paramSetChannel;
  late BasicMessageChannel<String> _meterUpdateChannel;
  late BasicMessageChannel<String> _perfUpdateChannel;
  late BasicMessageChannel<String> _requestSyncChannel;
  late BasicMessageChannel<String> _debugLogChannel;

  final Map<String, BasicMessageChannel<String>> _extensionChannels = {};
  final Map<String, ExtensionHandler> _extensionHandlers = {};

  bool _initialized = false;
  String _channelNamespace = '';

  // -------------------------------------------------------
  // 参数 schema（内置默认值与 C++ getAllParameterDefs() 保持一致；
  // schema 消息到达时自动覆盖，无需手动维护重复常量）
  //
  // 本模板工程仅演示一个最简单的 GAIN + BYPASS 效果器。
  // 新增参数时：在 C++ 侧 PluginParameters.cpp 中追加即可，
  // Dart 侧 schema 会通过 param_schema 通道自动同步，此处内置值
  // 仅用于 schema 到达前的"冷启动"渲染。
  // -------------------------------------------------------
  final Map<String, ParamDef> _schema = {
    'gain': const ParamDef(
      id: 'gain',
      label: 'GAIN',
      unit: 'dB',
      min: -60,
      max: 12,
      defaultValue: 0,
      skew: 2.0,
      step: 0.1,
      isBoolean: false,
      uiHint: 'knob',
    ),
    'bypass': const ParamDef(
      id: 'bypass',
      label: 'BYPASS',
      unit: '',
      min: 0,
      max: 1,
      defaultValue: 0,
      skew: 1.0,
      step: 1.0,
      isBoolean: true,
      uiHint: 'toggle',
    ),
  };

  // -------------------------------------------------------
  // 参数值（由 C++ paramChanged 推送保持同步）
  // -------------------------------------------------------
  final Map<String, double> _params = {'gain': 0.0, 'bypass': 0.0};

  double _meterLeft = 0.0;
  double _meterRight = 0.0;

  /// 独立 ValueNotifier，仅电平表订阅。
  /// 不调用 notifyListeners()，避免 30Hz 触发全树重建。
  final ValueNotifier<(double, double)> meterLevels = ValueNotifier((0.0, 0.0));

  // debug 日志缓冲（来自 C++ FLUTTER_LOG 宏，通过 audio_bridge/debug_log 通道传入）
  final List<String> _debugLogs = [];
  static const int _kMaxDebugLogs = 500;

  // frame timings 独立为 ValueNotifier，不触发全局 Consumer 重建
  final frameTimings = ValueNotifier<(double, double, double)>((0.0, 0.0, 0.0));

  // DSP 性能读数（dspNs, blockSize, cpuLoad%），由 C++ perf_update 通道推送。
  // 与 frameTimings 同理，独立 ValueNotifier，不触发全局重建。
  final perfStats = ValueNotifier<(double, int, double)>((0.0, 0, 0.0));

  // -------------------------------------------------------
  // 公开 accessor
  // -------------------------------------------------------

  @override
  void dispose() {
    for (final entry in _extensionChannels.entries) {
      if (_extensionHandlers.containsKey(entry.key)) {
        entry.value.setMessageHandler(null);
      }
    }
    _extensionHandlers.clear();
    _extensionChannels.clear();

    meterLevels.dispose();
    frameTimings.dispose();
    perfStats.dispose();
    super.dispose();
  }

  /// 获取参数元数据；若 schema 未到达则返回内置默认 ParamDef
  ParamDef? defOf(String id) => _schema[id];

  /// 参数当前值（与 JUCE 侧保持同步）
  double paramValue(String id) =>
      _params[id] ?? _schema[id]?.defaultValue ?? 0.0;

  double get meterLeft => _meterLeft;
  double get meterRight => _meterRight;

  Map<String, double> get allParams => Map.unmodifiable(_params);

  /// 调试日志列表（只读）
  List<String> get debugLogs => List.unmodifiable(_debugLogs);

  /// 手动追加本地日志行（Flutter 侧调试用）
  void appendDebugLog(String line) {
    _debugLogs.add(line);
    if (_debugLogs.length > _kMaxDebugLogs) _debugLogs.removeAt(0);
    notifyListeners();
  }

  /// 清空调试日志
  void clearDebugLogs() {
    _debugLogs.clear();
    notifyListeners();
  }

  // -------------------------------------------------------
  // 初始化：注册通道处理器
  // -------------------------------------------------------
  void initialize() {
    if (_initialized) return;
    _initialized = true;
    _initializeAsync();
  }

  Future<void> _initializeAsync() async {
    try {
      await _configureChannels();
    } catch (e) {
      debugPrint('$e');
      return;
    }

    _paramSchemaChannel.setMessageHandler(_handleParamSchemaMessage);
    _paramUpdateChannel.setMessageHandler(_handleParamUpdateMessage);
    _meterUpdateChannel.setMessageHandler(_handleMeterUpdateMessage);
    _perfUpdateChannel.setMessageHandler(_handlePerfUpdateMessage);

    // 注册 C++ → Flutter 的调试日志推送通道
    _debugLogChannel.setMessageHandler(_handleDebugLogMessage);

    SchedulerBinding.instance.addTimingsCallback(_handleFrameTimings);

    // 所有 handler 注册完毕后，等待第一帧渲染完成再通知 C++ 推送当前参数值。
    // 同时拉取 C++ 端已积累的历史日志（初始化阶段产生的日志在引擎就绪前无法推送）。
    SchedulerBinding.instance.addPostFrameCallback((_) {
      _requestSyncChannel.send(
        jsonEncode({'method': 'requestSync', 'args': {}}),
      );
      _fetchInitialLogs();
    });

    debugPrint(
        '[AudioBridge] channel mode=namespaced-v2 namespace=$_channelNamespace');
  }

  Future<void> _configureChannels() async {
    // 引擎 attach 早期可能短暂收不到 bootstrap 回包，做短重试避免竞态。
    for (int i = 0; i < 20 && _channelNamespace.isEmpty; i++) {
      try {
        final response = await _bootstrapChannel.send(
          jsonEncode({
            'method': 'hello',
            'args': {
              'protocols': [2],
            },
          }),
        );

        if (response != null && response.isNotEmpty) {
          final decoded = jsonDecode(response);
          if (decoded is Map) {
            final map = Map<String, dynamic>.from(decoded);
            final namespace = map['namespace']?.toString();
            final protocol = map['protocol'];
            if (namespace != null && namespace.isNotEmpty && protocol == 2) {
              _channelNamespace = namespace;
            }
          }
        }
      } catch (_) {
        // ignore and retry
      }

      if (_channelNamespace.isEmpty) {
        await Future<void>.delayed(const Duration(milliseconds: 20));
      }
    }

    if (_channelNamespace.isEmpty) {
      throw StateError(
        '[AudioBridge] bootstrap failed: no v2 namespace from C++ side',
      );
    }

    final schemaName = _resolveChannel(_channelParamSchema);
    final updateName = _resolveChannel(_channelParamUpdate);
    final setName = _resolveChannel(_channelParamSet);
    final meterName = _resolveChannel(_channelMeterUpdate);
    final perfName = _resolveChannel(_channelPerfUpdate);
    final syncName = _resolveChannel(_channelRequestSync);
    final debugName = _resolveChannel(_channelDebugLog);

    _paramSchemaChannel =
        BasicMessageChannel<String>(schemaName, const StringCodec());
    _paramUpdateChannel =
        BasicMessageChannel<String>(updateName, const StringCodec());
    _paramSetChannel =
        BasicMessageChannel<String>(setName, const StringCodec());
    _meterUpdateChannel =
        BasicMessageChannel<String>(meterName, const StringCodec());
    _perfUpdateChannel =
        BasicMessageChannel<String>(perfName, const StringCodec());
    _requestSyncChannel =
        BasicMessageChannel<String>(syncName, const StringCodec());
    _debugLogChannel =
        BasicMessageChannel<String>(debugName, const StringCodec());

    // 关键：某些扩展通道（如示波器 scope）的 handler 在首帧构建时就已注册，
    // 那时 bootstrap 尚未拿到 namespace，通道会以空命名空间被缓存，
    // 与 C++ 发送端的命名空间通道永不匹配。namespace 就绪后必须重绑定一次。
    _rebindExtensionChannels();
  }

  /// bootstrap 完成后，用正确的命名空间重新解析并重绑所有已注册的扩展通道 handler。
  void _rebindExtensionChannels() {
    if (_extensionHandlers.isEmpty) {
      _extensionChannels.clear();
      return;
    }
    final handlers = Map<String, ExtensionHandler>.from(_extensionHandlers);
    // 解除旧的（命名空间可能为空的）缓存通道 handler，并清空缓存以便重新解析通道名。
    for (final channel in _extensionChannels.values) {
      channel.setMessageHandler(null);
    }
    _extensionChannels.clear();
    _extensionHandlers.clear();
    // 用当前 namespace 重新注册。
    handlers.forEach(setExtensionHandler);
  }

  String _resolveChannel(String legacyBase) {
    const prefix = 'audio_bridge/';
    if (!legacyBase.startsWith(prefix)) return legacyBase;
    final leaf = legacyBase.substring(prefix.length);
    return 'audio_bridge/v2/$_channelNamespace/$leaf';
  }

  /// 请求 C++ 端发送历史日志快照（getLogs）
  Future<void> _fetchInitialLogs() async {
    try {
      final response = await _debugLogChannel.send(
        jsonEncode({'method': 'getLogs', 'args': {}}),
      );
      if (response == null || response.isEmpty) return;
      final decoded = jsonDecode(response);
      if (decoded is! List) return;
      for (final item in decoded) {
        final line = item?.toString() ?? '';
        if (line.isNotEmpty) {
          _debugLogs.add(line);
          if (_debugLogs.length > _kMaxDebugLogs) _debugLogs.removeAt(0);
        }
      }
      if (decoded.isNotEmpty) notifyListeners();
    } catch (e) {
      debugPrint('[AudioBridge] _fetchInitialLogs failed: $e');
    }
  }

  /// 处理 C++ 实时推送的调试日志（appendLog / clearLogs）
  Future<String> _handleDebugLogMessage(String? message) async {
    final payload = _decodeEnvelope(message);
    if (payload == null) return '{}';

    switch (payload.method) {
      case 'appendLog':
        final line = payload.args['line']?.toString() ?? '';
        if (line.isNotEmpty) {
          _debugLogs.add(line);
          if (_debugLogs.length > _kMaxDebugLogs) _debugLogs.removeAt(0);
          notifyListeners();
        }
      case 'clearLogs':
        _debugLogs.clear();
        notifyListeners();
    }
    return '{}';
  }

  void _handleFrameTimings(List<FrameTiming> timings) {
    if (timings.isEmpty) return;
    final latest = timings.last;
    // 单位纳秒（1 微秒 = 1000 纳秒），与 DSP 读数统一
    frameTimings.value = (
      latest.buildDuration.inMicroseconds * 1000.0,
      latest.rasterDuration.inMicroseconds * 1000.0,
      latest.totalSpan.inMicroseconds * 1000.0,
    );
    // 不调用 notifyListeners()：每帧触发全局重建会导致重复渲染
  }

  // -------------------------------------------------------
  // 处理来自 JUCE 的 param_schema 消息（仅触发一次）
  // -------------------------------------------------------
  Future<String> _handleParamSchemaMessage(String? message) async {
    final payload = _decodeEnvelope(message);
    if (payload == null || payload.method != 'paramSchema') return '';

    final list = payload.args['schema'];
    if (list is! List) return '';

    bool changed = false;
    for (final item in list) {
      if (item is! Map<String, dynamic>) continue;
      try {
        final def = ParamDef.fromJson(item);
        _schema[def.id] = def;
        // 确保 _params 包含该 id（新增参数时自动初始化）
        _params.putIfAbsent(def.id, () => def.defaultValue);
        changed = true;
      } catch (_) {}
    }

    if (changed) notifyListeners();
    return '';
  }

  // -------------------------------------------------------
  // 处理来自 JUCE 的参数值变化消息
  // -------------------------------------------------------
  Future<String> _handleParamUpdateMessage(String? message) async {
    final payload = _decodeEnvelope(message);
    if (payload == null || payload.method != 'paramChanged') return '';

    final id = payload.args['id']?.toString();
    final value = payload.args['value'] as num?;
    if (id != null && value != null) {
      _params[id] = value.toDouble();
      notifyListeners();
    }
    return '';
  }

  Future<String> _handleMeterUpdateMessage(String? message) async {
    final payload = _decodeEnvelope(message);
    if (payload == null || payload.method != 'meterChanged') return '';

    final left = payload.args['left'] as num?;
    final right = payload.args['right'] as num?;
    if (left != null && right != null) {
      _meterLeft = left.toDouble().clamp(0.0, 1.0);
      _meterRight = right.toDouble().clamp(0.0, 1.0);
      // 直接更新 ValueNotifier，不触发全局 notifyListeners()
      meterLevels.value = (_meterLeft, _meterRight);
    }
    return '';
  }

  // C++ perf_update 通道：DSP 耗时 / 每帧大小 / CPU 负载。
  // 与 meter 同理，仅更新独立 ValueNotifier，不触发全局重建。
  Future<String> _handlePerfUpdateMessage(String? message) async {
    final payload = _decodeEnvelope(message);
    if (payload == null || payload.method != 'perfChanged') return '';

    final dspNs = payload.args['dspNs'] as num?;
    final block = payload.args['block'] as num?;
    final load = payload.args['load'] as num?;
    if (dspNs != null && block != null && load != null) {
      perfStats.value = (dspNs.toDouble(), block.toInt(), load.toDouble());
    }
    return '';
  }

  _Envelope? _decodeEnvelope(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map) return null;
      final map = Map<String, dynamic>.from(decoded);
      final method = map['method']?.toString();
      final argsRaw = map['args'];

      if (method == null) return null;

      // param_schema 的 args 是 JSON 数组，其他是 Map
      if (argsRaw is List) {
        return _Envelope(method: method, args: {'schema': argsRaw});
      }
      if (argsRaw is! Map) return null;
      return _Envelope(
        method: method,
        args: Map<String, dynamic>.from(argsRaw),
      );
    } catch (_) {
      return null;
    }
  }

  // -------------------------------------------------------
  // 设置参数（Flutter → JUCE），使用 schema 范围做 clamp
  // -------------------------------------------------------
  Future<void> setParam(String id, double value) async {
    final def = _schema[id];
    final clamped = def != null ? value.clamp(def.min, def.max) : value;

    // 乐观更新本地状态，使 UI 立即响应
    _params[id] = clamped;
    notifyListeners();

    try {
      final payload = jsonEncode({
        'method': 'setParam',
        'args': {'id': id, 'value': clamped},
      });
      await _paramSetChannel.send(payload);
    } catch (e) {
      debugPrint('[AudioBridge] setParam("$id") 失败: $e');
    }
  }

  // 保留便捷方法（用 setParam 统一实现，clamp 由 schema 驱动）
  Future<void> setGain(double db) => setParam('gain', db);
  Future<void> setCutoff(double hz) => setParam('cutoff', hz);
  Future<void> setResonance(double q) => setParam('resonance', q);
  Future<void> setBypass(bool on) => setParam('bypass', on ? 1.0 : 0.0);

  // -------------------------------------------------------
  // 扩展通道 API（供后续 UI 功能快速接入）
  // -------------------------------------------------------

  /// 创建一个实例隔离的扩展通道（leaf 示例："spectrum"、"preset_browser"）。
  BasicMessageChannel<String> extensionChannel(String leaf) {
    return _extensionChannels.putIfAbsent(leaf, () {
      final name = _resolveChannel('audio_bridge/$leaf');
      return BasicMessageChannel<String>(name, const StringCodec());
    });
  }

  /// 向扩展通道发送标准信封消息，返回原始字符串响应。
  Future<String?> sendExtensionMessage(
    String leaf,
    String method,
    Map<String, dynamic> args,
  ) {
    final channel = extensionChannel(leaf);
    return channel.send(jsonEncode({'method': method, 'args': args}));
  }

  /// 注册扩展通道 handler（C++ -> Dart）。
  ///
  /// 使用统一信封格式：{"method":"...","args":{...}}。
  /// 返回值会被封装为 JSON 字符串回给 C++。
  void setExtensionHandler(String leaf, ExtensionHandler? handler) {
    final channel = extensionChannel(leaf);

    if (handler == null) {
      _extensionHandlers.remove(leaf);
      channel.setMessageHandler(null);
      return;
    }

    _extensionHandlers[leaf] = handler;
    channel.setMessageHandler((String? message) async {
      final payload = _decodeEnvelope(message);
      if (payload == null) {
        return jsonEncode({'status': 'error', 'message': 'invalid envelope'});
      }

      try {
        final result = await handler(payload.method, payload.args);
        return jsonEncode({'status': 'ok', 'result': result ?? {}});
      } catch (e) {
        return jsonEncode({'status': 'error', 'message': e.toString()});
      }
    });
  }

  // -------------------------------------------------------
  // 归一化辅助（供外部仍需要手动计算的场景使用）
  // -------------------------------------------------------
  static double normalizeLinear(double v, double min, double max) =>
      ((v - min) / (max - min)).clamp(0.0, 1.0);

  static double denormalizeLinear(double norm, double min, double max) =>
      min + norm.clamp(0.0, 1.0) * (max - min);

  static double normalizeCutoff(double hz) {
    const min = 20.0;
    const max = 20000.0;
    if (hz <= min) return 0.0;
    if (hz >= max) return 1.0;
    return (math.log(hz) - math.log(min)) / (math.log(max) - math.log(min));
  }

  static double denormalizeCutoff(double norm) {
    const min = 20.0;
    const max = 20000.0;
    return min * math.pow(max / min, norm.clamp(0.0, 1.0));
  }
}

class _Envelope {
  _Envelope({required this.method, required this.args});
  final String method;
  final Map<String, dynamic> args;
}
