# ============================================================
# JuceFlutterEngineLink.cmake
# ------------------------------------------------------------
# 本文件提供三个可复用的 CMake 函数，封装「JUCE 插件 + Flutter
# 引擎」组合的通用构建逻辑（编译宏 / 头文件搜索路径 / JUCE 模块链接 /
# 各平台 Flutter Engine 拷贝与打包 / 平台特定链接选项）。
#
# 这些逻辑与具体插件的名称、DSP 算法无关，因此被抽取到本模板仓库
# （JucyFlutter）中，由下游插件工程以 git submodule 方式引入并调用，
# 从而避免各插件各自维护一份逐渐分叉的拷贝。
#
# 使用方式（下游工程的根 CMakeLists.txt）：
#
#   include(vendor/JucyFlutter/cmake/JuceFlutterEngineLink.cmake)
#
#   juce_flutter_configure_target(${PROJECT_NAME}
#       UI_SOURCE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/vendor/JucyFlutter/src/ui
#       EXTRA_INCLUDE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}/src/common
#   )
#   juce_flutter_link_engine()
#   juce_flutter_platform_config()
#
# 注意：juce_flutter_link_engine() 与 juce_flutter_platform_config()
# 依赖调用者作用域中已经存在的 PROJECT_NAME / FLUTTER_ENGINE_FOUND 等
# 变量（由 project() 与 cmake/FlutterEngine.cmake 设置），因此必须在
# juce_add_plugin(${PROJECT_NAME} ...) 之后调用。
# ============================================================

# ------------------------------------------------------------
# juce_flutter_configure_target
#   通用编译宏 / 头文件目录 / JUCE 模块链接
# ------------------------------------------------------------
function(juce_flutter_configure_target TGT)
    set(oneValueArgs UI_SOURCE_DIR)
    set(multiValueArgs EXTRA_INCLUDE_DIRS)
    cmake_parse_arguments(ARG "" "${oneValueArgs}" "${multiValueArgs}" ${ARGN})

    if(NOT ARG_UI_SOURCE_DIR)
        message(FATAL_ERROR "juce_flutter_configure_target: 缺少 UI_SOURCE_DIR 参数")
    endif()

    # macOS：编译「插件 image 常驻」支持，防止宿主反复 load/unload 时崩溃
    # （Flutter 引擎后台线程回调进已卸载的插件代码 → 崩溃）。详见其源文件。
    # 由本共享 cmake 统一加入，下游工程无需在各自 CMakeLists 手动列出。
    target_sources(${TGT} PRIVATE
        $<$<PLATFORM_ID:Darwin>:${ARG_UI_SOURCE_DIR}/FlutterPluginImagePin_mac.cpp>
    )

    target_compile_definitions(${TGT}
        PUBLIC
            # 禁用不需要的 JUCE 模块以减少编译时间
            JUCE_WEB_BROWSER=0
            JUCE_USE_CURL=0
            JUCE_VST3_CAN_REPLACE_VST2=0
            JUCE_DISPLAY_SPLASH_SCREEN=0
            JUCE_REPORT_APP_USAGE=0
            # Flutter 引擎宏
            FLUTTER_ENGINE_ENABLED=1
            # 构建模式宏（C++ 运行时用于决定初始化方式）
            # 三选一：FLUTTER_BUILD_MODE_DEBUG / FLUTTER_BUILD_MODE_PROFILE / FLUTTER_BUILD_MODE_RELEASE
            FLUTTER_BUILD_MODE_${FLUTTER_BUILD_MODE}=1
            $<$<BOOL:${FLUTTER_ENGINE_RELEASE}>:FLUTTER_USING_RELEASE_ENGINE=1>
    )

    target_include_directories(${TGT}
        PRIVATE
            ${CMAKE_CURRENT_SOURCE_DIR}/src
            ${ARG_UI_SOURCE_DIR}
            ${ARG_EXTRA_INCLUDE_DIRS}
            ${FLUTTER_ENGINE_INCLUDE_DIR}
    )

    target_link_libraries(${TGT}
        PRIVATE
            juce::juce_audio_utils
            juce::juce_audio_processors
            juce::juce_audio_plugin_client
            juce::juce_dsp
            juce::juce_gui_basics
            juce::juce_gui_extra
            juce::juce_core
            juce::juce_data_structures
            juce::juce_events
        PUBLIC
            juce::juce_recommended_config_flags
            juce::juce_recommended_lto_flags
            juce::juce_recommended_warning_flags
    )
endfunction()

# ------------------------------------------------------------
# juce_flutter_link_engine
#   链接 Flutter Engine，并为 Windows / Linux / macOS 生成
#   POST_BUILD 拷贝与「唯一化」处理（防止多插件共存时 UI 串台）。
#   依赖调用者作用域中的 PROJECT_NAME、FLUTTER_ENGINE_FOUND、
#   FLUTTER_ENGINE_DLL_NAME、FLUTTER_ENGINE_SO_NAME 等变量。
# ------------------------------------------------------------
function(juce_flutter_link_engine)
    # 兼容两种调用：
    #   juce_flutter_link_engine()          # 旧式：作用于 ${PROJECT_NAME}
    #   juce_flutter_link_engine(<TARGET>)  # 新式：作用于指定目标
    # 传入目标名时，将其绑定到函数局部 PROJECT_NAME，下方所有 ${PROJECT_NAME}/
    # ${PROJECT_NAME}_VST3 等引用自动指向该目标（函数局部作用域，不影响外部）。
    if(ARGC GREATER 0 AND NOT "${ARGV0}" STREQUAL "")
        set(PROJECT_NAME "${ARGV0}")
    endif()

    if(NOT FLUTTER_ENGINE_FOUND)
        message(WARNING "[Flutter] 未找到 Flutter Engine，插件将使用 JUCE 原生 UI 作为回退")
        target_compile_definitions(${PROJECT_NAME} PUBLIC FLUTTER_ENGINE_ENABLED=0)
        return()
    endif()

    target_link_libraries(${PROJECT_NAME} PRIVATE FlutterEngine::FlutterEngine)
    message(STATUS "[Flutter] Flutter Engine 已链接到插件目标")

    if(WIN32)
        set(_flutter_copy_targets
            ${PROJECT_NAME}
            ${PROJECT_NAME}_Standalone
            ${PROJECT_NAME}_VST3
        )

        foreach(_tgt IN LISTS _flutter_copy_targets)
            if(TARGET ${_tgt})
                add_custom_command(TARGET ${_tgt} POST_BUILD
                    COMMAND ${CMAKE_COMMAND} -E copy_if_different
                        "${FLUTTER_ENGINE_LIBRARY}"
                        "$<TARGET_FILE_DIR:${_tgt}>/${FLUTTER_ENGINE_DLL_NAME}"
                    COMMENT "复制 Flutter Engine DLL 到输出目录（唯一命名: ${FLUTTER_ENGINE_DLL_NAME}）(${_tgt})"
                    VERBATIM
                )

                add_custom_command(TARGET ${_tgt} POST_BUILD
                    COMMAND ${CMAKE_COMMAND} -E copy_directory
                        "${CMAKE_BINARY_DIR}/flutter_assets"
                        "$<TARGET_FILE_DIR:${_tgt}>/flutter_assets"
                    COMMENT "复制 Flutter UI assets 到输出目录 (${_tgt})"
                    VERBATIM
                )

                add_custom_command(TARGET ${_tgt} POST_BUILD
                    COMMAND ${CMAKE_COMMAND} -E copy_if_different
                        "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/windows-x64/icudtl.dat"
                        "$<TARGET_FILE_DIR:${_tgt}>"
                    COMMENT "复制 Flutter ICU 数据到输出目录 (${_tgt})"
                    VERBATIM
                )

                # Release 引擎：复制 AOT 库（app.so，Flutter 3.22+ 在 Windows 上的命名）到输出目录
                # 路径由 BuildFlutterUI 中的 flutter build windows --profile/--release 生成，
                # 不依赖 configure 阶段是否存在该文件。
                if(FLUTTER_ENGINE_RELEASE)
                    # AOT 快照由 cmake/FlutterAOTBuild.cmake 编译后暂存于 CMAKE_BINARY_DIR
                    add_custom_command(TARGET ${_tgt} POST_BUILD
                        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                            "${CMAKE_BINARY_DIR}/app.so"
                            "$<TARGET_FILE_DIR:${_tgt}>/app.so"
                        COMMENT "复制 AOT 快照 (app.so) 到输出目录 (${_tgt})"
                        VERBATIM
                    )
                endif()
            endif()
        endforeach()

        if(TARGET juce_vst3_helper)
            add_custom_command(TARGET juce_vst3_helper POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${FLUTTER_ENGINE_LIBRARY}"
                    "$<TARGET_FILE_DIR:juce_vst3_helper>/${FLUTTER_ENGINE_DLL_NAME}"
                COMMENT "复制 Flutter Engine DLL 到 juce_vst3_helper 目录（唯一命名）"
                VERBATIM
            )
            # icudtl.dat 也需要在 helper 目录，否则引擎无法初始化 ICU
            add_custom_command(TARGET juce_vst3_helper POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/windows-x64/icudtl.dat"
                    "$<TARGET_FILE_DIR:juce_vst3_helper>"
                COMMENT "复制 Flutter ICU 数据到 juce_vst3_helper 目录"
                VERBATIM
            )
        endif()
    endif()

    # Linux：复制 libflutter_linux_gtk.so 到输出目录（唯一 soname）
    if(UNIX AND NOT APPLE)
        set(_flutter_copy_targets
            ${PROJECT_NAME}
            ${PROJECT_NAME}_Standalone
            ${PROJECT_NAME}_VST3
        )

        # patchelf 用于唯一化 soname 并改写插件二进制的 DT_NEEDED。
        # 缺失时退化为「同名复制」（多插件共存仍会串台，但单插件可用）。
        find_program(PATCHELF_EXECUTABLE patchelf)
        if(NOT PATCHELF_EXECUTABLE)
            message(WARNING
                "[Flutter] 未找到 patchelf，Linux 上无法唯一化引擎 soname。"
                "多个基于本模板的插件在同一宿主进程内会共享同一引擎实例而 UI 串台。"
                "请安装 patchelf（apt install patchelf / dnf install patchelf）。")
        endif()

        foreach(_tgt IN LISTS _flutter_copy_targets)
            if(TARGET ${_tgt})
                # 只有可加载产物（.so 模块 / 可执行文件）才需要唯一化 soname 与
                # 改写 DT_NEEDED；SharedCode 是静态库(.a)，patchelf 对其操作会失败，
                # 且静态库本身也不产出可加载 bundle，直接跳过。
                get_target_property(_tgt_type ${_tgt} TYPE)
                if(_tgt_type STREQUAL "STATIC_LIBRARY")
                    continue()
                endif()

                if(PATCHELF_EXECUTABLE)
                    # 1) 复制引擎 .so 为唯一文件名  2) 把副本的 soname 改为唯一名
                    add_custom_command(TARGET ${_tgt} POST_BUILD
                        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                            "${FLUTTER_ENGINE_LIBRARY}"
                            "$<TARGET_FILE_DIR:${_tgt}>/${FLUTTER_ENGINE_SO_NAME}"
                        COMMAND "${PATCHELF_EXECUTABLE}" --set-soname
                            "${FLUTTER_ENGINE_SO_NAME}"
                            "$<TARGET_FILE_DIR:${_tgt}>/${FLUTTER_ENGINE_SO_NAME}"
                        COMMENT "复制并唯一化 Flutter Engine .so (soname=${FLUTTER_ENGINE_SO_NAME}) (${_tgt})"
                        VERBATIM
                    )

                    # 3) 改写插件二进制的 DT_NEEDED
                    add_custom_command(TARGET ${_tgt} POST_BUILD
                        COMMAND "${PATCHELF_EXECUTABLE}" --replace-needed
                            "${FLUTTER_ENGINE_LIB_FILE}"
                            "${FLUTTER_ENGINE_SO_NAME}"
                            "$<TARGET_FILE:${_tgt}>"
                        COMMENT "改写 DT_NEEDED: ${FLUTTER_ENGINE_LIB_FILE} -> ${FLUTTER_ENGINE_SO_NAME} (${_tgt})"
                        VERBATIM
                    )
                else()
                    add_custom_command(TARGET ${_tgt} POST_BUILD
                        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                            "${FLUTTER_ENGINE_LIBRARY}"
                            "$<TARGET_FILE_DIR:${_tgt}>"
                        COMMENT "复制 Flutter Engine .so 到输出目录 (${_tgt})"
                        VERBATIM
                    )
                endif()

                add_custom_command(TARGET ${_tgt} POST_BUILD
                    COMMAND ${CMAKE_COMMAND} -E copy_directory
                        "${CMAKE_BINARY_DIR}/flutter_assets"
                        "$<TARGET_FILE_DIR:${_tgt}>/flutter_assets"
                    COMMENT "复制 Flutter UI assets 到输出目录 (${_tgt})"
                    VERBATIM
                )

                if(FLUTTER_BUILD_MODE STREQUAL "Release" OR FLUTTER_BUILD_MODE STREQUAL "Profile")
                    # Linux AOT 快照由 cmake/FlutterAOTBuild.cmake 编译后暂存于 CMAKE_BINARY_DIR/lib
                    add_custom_command(TARGET ${_tgt} POST_BUILD
                        COMMAND ${CMAKE_COMMAND} -E copy_if_different
                            "${CMAKE_BINARY_DIR}/lib/libapp.so"
                            "$<TARGET_FILE_DIR:${_tgt}>/libapp.so"
                        COMMENT "复制 AOT 快照 libapp.so 到输出目录 (${_tgt})"
                        VERBATIM
                    )
                endif()
            endif()
        endforeach()
    endif()

    # macOS：将 FlutterMacOS.framework 放入各产物 bundle 的 Frameworks 目录
    #
    # dyld 按 install name(LC_ID_DYLIB) 字符串去重：多个插件若共用 SDK 默认的
    # install name @rpath/FlutterMacOS.framework/... 只会加载一份，后加载的插件
    # 复用先加载插件的引擎与已初始化的 Dart VM → UI 串台（与 Windows 基名、
    # Linux soname 同构）。故为每个插件唯一化 framework 目录名与 install name，
    # 并改写插件二进制的引用，最后重新 ad-hoc 签名（改 Mach-O 后旧签名失效，
    # Apple Silicon 上必须重签才能加载）。
    if(APPLE)
        # 使用系统 codesign，避免 PATH 命中 cctools-port 同名工具导致 framework
        # 识别/签名失败（如 "bundle format is ambiguous"）。
        set(_codesign_tool "/usr/bin/codesign")
        if(NOT EXISTS "${_codesign_tool}")
            set(_codesign_tool codesign)
        endif()
        set(_install_name_tool "/usr/bin/install_name_tool")
        if(NOT EXISTS "${_install_name_tool}")
            set(_install_name_tool install_name_tool)
        endif()
        set(_otool "/usr/bin/otool")
        if(NOT EXISTS "${_otool}")
            set(_otool otool)
        endif()

        # ----------------------------------------------------
        # Dart 快照符号「每插件唯一化」所需工具
        # ----------------------------------------------------
        # 见 cmake/rename_dart_snapshot.py：把本插件的四个 Dart 快照符号名里的
        # 共享前缀 "kDart" 换成一个由 PROJECT_NAME 派生的 5 字符 base-62 唯一
        # tag（62^5 ≈ 9.16 亿种，实际零碰撞），并同步修改本插件自带的
        # FlutterMacOS_<name> 引擎里请求这些符号的字符串，从根本上消除多个
        # AOT 插件在同一宿主进程内因 dlsym(RTLD_DEFAULT) 先到先得而导致的
        # 「UI 串台」。tag 由脚本内确定性派生（app/engine 两次调用一致）。
        find_program(_jf_python NAMES python3 python)
        if(NOT _jf_python)
            set(_jf_python python3)
        endif()
        set(_jf_rename_script "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/rename_dart_snapshot.py")

        get_filename_component(_flutter_framework_dir "${FLUTTER_ENGINE_LIBRARY}" DIRECTORY)
        get_filename_component(_flutter_framework_dir "${_flutter_framework_dir}" DIRECTORY)
        get_filename_component(_flutter_framework_dir "${_flutter_framework_dir}" DIRECTORY)

        # 唯一化后的 framework 名与 install name
        set(_fl_uniq_fw "FlutterMacOS_${PROJECT_NAME}")
        set(_fl_new_id  "@rpath/${_fl_uniq_fw}.framework/Versions/A/FlutterMacOS")

        # 读取 SDK 版 FlutterMacOS 的真实 install name（-change 需精确匹配）
        execute_process(
            COMMAND "${_otool}" -D "${FLUTTER_ENGINE_LIBRARY}"
            OUTPUT_VARIABLE _fl_otool_out
            ERROR_QUIET
        )
        string(REGEX MATCH "@rpath/[^\n]*FlutterMacOS" _fl_orig_id "${_fl_otool_out}")
        if(NOT _fl_orig_id)
            set(_fl_orig_id "@rpath/FlutterMacOS.framework/Versions/A/FlutterMacOS")
        endif()

        if(EXISTS "${_flutter_framework_dir}")
            set(_flutter_copy_targets
                ${PROJECT_NAME}
                ${PROJECT_NAME}_Standalone
                ${PROJECT_NAME}_VST3
                ${PROJECT_NAME}_AU
                ${PROJECT_NAME}_AUv3
            )

            foreach(_tgt IN LISTS _flutter_copy_targets)
                if(TARGET ${_tgt})
                    # 静态库没有可加载 bundle，不需要复制/改写/签名 framework，
                    # 也没有 LC_LOAD_DYLIB 需要改写（下面同一 continue 一并跳过）。
                    get_target_property(_tgt_type ${_tgt} TYPE)
                    if(_tgt_type STREQUAL "STATIC_LIBRARY")
                        continue()
                    endif()

                    set(_bundle_fw_dir "$<TARGET_BUNDLE_CONTENT_DIR:${_tgt}>/Frameworks")
                    set(_fw_dst "${_bundle_fw_dir}/${_fl_uniq_fw}.framework")
                    # 1) 清掉旧的(含非唯一名的)副本  2) 复制为唯一目录名
                    # 3) 改 install name(-id)  4) 重新 ad-hoc 签名
                    add_custom_command(TARGET ${_tgt} POST_BUILD
                        COMMAND ${CMAKE_COMMAND} -E make_directory
                            "${_bundle_fw_dir}"
                        COMMAND ${CMAKE_COMMAND} -E rm -rf
                            "${_bundle_fw_dir}/FlutterMacOS.framework"
                            "${_fw_dst}"
                        COMMAND ${CMAKE_COMMAND} -E copy_directory
                            "${_flutter_framework_dir}"
                            "${_fw_dst}"
                        # 关键修复：CMake 的 copy_directory 会「解引用」符号链接，
                        # 使顶层 FlutterMacOS / Headers / Modules / Resources /
                        # Versions/Current 全变成真实副本。于是 framework 里出现
                        # **两份** FlutterMacOS 真实二进制（顶层 + Versions/A），
                        # dyld 按两条路径各加载一次 → 所有 Flutter ObjC 类重复注册
                        # （auval: "implemented in both ... mysterious crashes"）→
                        # DartVM 状态错乱 / AU 在宿主中随机闪退（0x538 崩溃栈）。
                        # 这里重建标准 framework 符号链接结构，使整个 framework 只有
                        # 「Versions/A/FlutterMacOS」一个真实二进制（与 App.framework
                        # 的修复一致）。必须在 -id / 签名之前重建软链。
                        COMMAND ${CMAKE_COMMAND} -E rm -rf
                            "${_fw_dst}/FlutterMacOS"
                            "${_fw_dst}/Headers"
                            "${_fw_dst}/Modules"
                            "${_fw_dst}/Resources"
                            "${_fw_dst}/Versions/Current"
                        COMMAND ${CMAKE_COMMAND} -E create_symlink
                            "A" "${_fw_dst}/Versions/Current"
                        COMMAND ${CMAKE_COMMAND} -E create_symlink
                            "Versions/Current/FlutterMacOS" "${_fw_dst}/FlutterMacOS"
                        COMMAND ${CMAKE_COMMAND} -E create_symlink
                            "Versions/Current/Headers" "${_fw_dst}/Headers"
                        COMMAND ${CMAKE_COMMAND} -E create_symlink
                            "Versions/Current/Modules" "${_fw_dst}/Modules"
                        COMMAND ${CMAKE_COMMAND} -E create_symlink
                            "Versions/Current/Resources" "${_fw_dst}/Resources"
                        COMMAND "${_install_name_tool}" -id "${_fl_new_id}"
                            "${_fw_dst}/Versions/A/FlutterMacOS"
                        # 改 Mach-O 后旧签名失效，随符号链接结构一起对整个 framework
                        # 重新 ad-hoc 签名（Apple Silicon 上必须重签才能加载）。
                        COMMAND "${_codesign_tool}" --force --sign -
                            "${_fw_dst}"
                        COMMENT "复制并唯一化 FlutterMacOS.framework（含顶层符号链接修复）(id=${_fl_new_id}) (${_tgt})"
                        VERBATIM
                    )

                    # 改写插件二进制对 FlutterMacOS 的引用并重签名
                    add_custom_command(TARGET ${_tgt} POST_BUILD
                        COMMAND "${_install_name_tool}" -change
                            "${_fl_orig_id}" "${_fl_new_id}" "$<TARGET_FILE:${_tgt}>"
                        COMMAND "${_codesign_tool}" --force --sign - "$<TARGET_FILE:${_tgt}>"
                        COMMENT "改写 FlutterMacOS 引用并重签名 (${_tgt})"
                        VERBATIM
                    )

                    if(FLUTTER_BUILD_MODE STREQUAL "Release" OR FLUTTER_BUILD_MODE STREQUAL "Profile")
                        # App.framework 由引擎按固定名 "App.framework" 查找，故保留目录/文件名，
                        # 仅唯一化其 install name，防止 dyld 跨插件复用同一份 AOT 快照。
                        set(_app_fw "${_bundle_fw_dir}/App.framework")
                        add_custom_command(TARGET ${_tgt} POST_BUILD
                            COMMAND ${CMAKE_COMMAND} -E rm -rf "${_app_fw}"
                            COMMAND ${CMAKE_COMMAND} -E copy_directory
                                "${CMAKE_BINARY_DIR}/App.framework"
                                "${_app_fw}"
                            # CMake 的 copy_directory 会「解引用」符号链接，导致顶层
                            # App / Resources / Versions/Current 变成真实副本，其中
                            # 顶层 App 仍保留旧的 @rpath/App.framework/App install name
                            # （未被下面的 -id 唯一化）。若任何代码路径 dlopen 了顶层
                            # App，dyld 会按相同 install name 跨插件去重 → AOT 快照串台。
                            # 这里重建标准 framework 符号链接结构，使整个 framework 只有
                            # 「Versions/A/App」一个真实二进制，其 install name 被唯一化。
                            COMMAND ${CMAKE_COMMAND} -E rm -rf
                                "${_app_fw}/App"
                                "${_app_fw}/Resources"
                                "${_app_fw}/Versions/Current"
                            COMMAND ${CMAKE_COMMAND} -E create_symlink
                                "A" "${_app_fw}/Versions/Current"
                            COMMAND ${CMAKE_COMMAND} -E create_symlink
                                "Versions/Current/App" "${_app_fw}/App"
                            COMMAND ${CMAKE_COMMAND} -E create_symlink
                                "Versions/Current/Resources" "${_app_fw}/Resources"
                            COMMAND "${_install_name_tool}" -id
                                "@rpath/App_${PROJECT_NAME}.framework/Versions/A/App"
                                "${_app_fw}/Versions/A/App"
                            # 改 Mach-O 后旧签名失效，重新 ad-hoc 签名整个 framework
                            # （随符号链接结构一起签，Apple Silicon 上必须重签才能加载）。
                            COMMAND "${_codesign_tool}" --force --sign -
                                "${_app_fw}"
                            COMMENT "复制并唯一化 AOT App.framework install name（含顶层符号链接修复）(${_tgt})"
                            VERBATIM
                        )

                        # --------------------------------------------
                        # 关键修复：Dart 快照符号「每插件唯一化」
                        # --------------------------------------------
                        # 上面的 install name 唯一化解决不了「UI 串台」——引擎用
                        # dlsym(RTLD_DEFAULT, "kDart...") 按符号名做进程级扁平查找，
                        # 与 install name 无关，先加载的插件符号胜出。这里把本插件
                        # App.framework 的四个快照符号改成唯一名（改共享前缀
                        # kDart → 5 字符 tag），并同步修改本插件 FlutterMacOS_<name>
                        # 引擎里请求这些符号的字符串，使两个插件符号名不再相同 →
                        # RTLD_DEFAULT 不再串台。改后重新签名。
                        add_custom_command(TARGET ${_tgt} POST_BUILD
                            COMMAND "${_jf_python}" "${_jf_rename_script}"
                                --role app --project-name "${PROJECT_NAME}"
                                "${_app_fw}/Versions/A/App"
                            COMMAND "${_jf_python}" "${_jf_rename_script}"
                                --role engine --project-name "${PROJECT_NAME}"
                                "${_fw_dst}/Versions/A/FlutterMacOS"
                            COMMAND "${_codesign_tool}" --force --sign - "${_app_fw}"
                            # 对整个 framework 重签（覆盖符号链接结构，与顶层修复一致）
                            COMMAND "${_codesign_tool}" --force --sign -
                                "${_fw_dst}"
                            COMMENT "唯一化 Dart 快照符号防止多插件 UI 串台 (${_tgt})"
                            VERBATIM
                        )
                    endif()

                    # ------------------------------------------------
                    # 收尾：重签「外层 bundle」封条（必须是本目标最后一步）
                    # ------------------------------------------------
                    # 上面所有 POST_BUILD 只重签了 *内嵌* framework
                    # （FlutterMacOS_<name> / App.framework）与插件二进制，
                    # 但改写 Contents/Frameworks 之后，JUCE 在链接期对外层
                    # .vst3/.component/.app 生成的 Contents/_CodeSignature
                    # 封条已失效——seal 描述的是改写前的旧内容。于是
                    #   codesign --verify --strict → "nested code is modified or invalid"
                    # Ableton 等 VST3 宿主在扫描期严格校验，判定 not-a-plugin →
                    # 插件不出现在列表里；AU 宿主(AudioComponent)容忍陈旧 ad-hoc
                    # 封条仍能加载，故此前「只有 AU 能扫到、VST3 扫不到」。
                    #
                    # 这里在所有 framework 改写完成之后，对整个外层 bundle 重签。
                    # 内嵌 framework 已被上面各步正确签名，故外层 --force --sign -
                    # （不带 --deep）只需重建外层封条即可通过 --verify --strict。
                    # 因是 foreach 内本目标注册的最后一条 POST_BUILD，保证在
                    # 拷贝/改写/内层重签全部完成后才执行。
                    add_custom_command(TARGET ${_tgt} POST_BUILD
                        COMMAND "${_codesign_tool}" --force --sign -
                            "$<TARGET_BUNDLE_DIR:${_tgt}>"
                        COMMENT "重签外层 bundle 封条（修 VST3 扫描失败 / --strict）(${_tgt})"
                        VERBATIM
                    )
                endif()
            endforeach()
        endif()
    endif()
endfunction()

# ------------------------------------------------------------
# juce_flutter_platform_config
#   平台特定链接选项（Windows 延迟加载 / macOS rpath+ARC / Linux GTK3+rpath）
#   依赖调用者作用域中的 PROJECT_NAME、FLUTTER_ENGINE_DLL_NAME 变量。
# ------------------------------------------------------------
function(juce_flutter_platform_config)
    # 兼容 juce_flutter_platform_config() 与 juce_flutter_platform_config(<TARGET>)，
    # 见 juce_flutter_link_engine 中同样的说明。
    if(ARGC GREATER 0 AND NOT "${ARGV0}" STREQUAL "")
        set(PROJECT_NAME "${ARGV0}")
    endif()

    if(WIN32)
        # 某些宿主使用受限 DLL 搜索路径，导致插件装载阶段无法解析 flutter_windows.dll。
        # 使用延迟加载并在运行时按插件模块目录手动加载，可避免实例化失败。
        set(_flutter_delay_load_targets
            ${PROJECT_NAME}
            ${PROJECT_NAME}_Standalone
            ${PROJECT_NAME}_VST3
        )

        foreach(_tgt IN LISTS _flutter_delay_load_targets)
            if(TARGET ${_tgt})
                if(CMAKE_CXX_COMPILER_ID STREQUAL "MSVC")
                    target_link_options(${_tgt} PRIVATE "/DELAYLOAD:flutter_windows.dll")
                else()
                    target_link_options(${_tgt} PRIVATE "-Xlinker" "/delayload:flutter_windows.dll")
                endif()
                target_link_libraries(${_tgt} PRIVATE delayimp)
            endif()
        endforeach()

        target_compile_definitions(${PROJECT_NAME} PUBLIC
            JUCE_WINDOWS=1
            _CRT_SECURE_NO_WARNINGS
            # 引擎 DLL 的唯一物理文件名（供 FlutterEmbedder_win.cpp 加载）。
            # 延迟加载导入名仍是 flutter_windows.dll，钩子据此重定向到该唯一文件。
            FLUTTER_ENGINE_DLL_NAME="${FLUTTER_ENGINE_DLL_NAME}"
        )
    elseif(APPLE)
        target_compile_definitions(${PROJECT_NAME} PUBLIC JUCE_MAC=1)
        set_target_properties(${PROJECT_NAME} PROPERTIES
            XCODE_ATTRIBUTE_CLANG_ENABLE_OBJC_ARC YES
        )
        # FlutterEmbedder_mac.mm 已是 .mm 扩展名，Xcode/clang 自动以 ObjC++ 编译
        # 无需手动设置 -x objective-c++ 标志

        # 确保宿主加载插件时，优先从 bundle 内 Frameworks 目录解析 FlutterMacOS.framework。
        set(_flutter_rpath_targets
            ${PROJECT_NAME}
            ${PROJECT_NAME}_Standalone
            ${PROJECT_NAME}_VST3
            ${PROJECT_NAME}_AU
            ${PROJECT_NAME}_AUv3
        )
        foreach(_tgt IN LISTS _flutter_rpath_targets)
            if(TARGET ${_tgt})
                target_link_options(${_tgt} PRIVATE
                    "-Wl,-rpath,@loader_path/../Frameworks"
                    "-Wl,-rpath,@loader_path"
                )
            endif()
        endforeach()
    elseif(UNIX)
        target_compile_definitions(${PROJECT_NAME} PUBLIC JUCE_LINUX=1)
        find_package(PkgConfig REQUIRED)
        pkg_check_modules(GTK3 REQUIRED gtk+-3.0)
        target_include_directories(${PROJECT_NAME} PRIVATE ${GTK3_INCLUDE_DIRS})
        target_link_libraries(${PROJECT_NAME} PRIVATE ${GTK3_LIBRARIES})

        # Linux 宿主通常不会搜索插件目录，给产物写入 $ORIGIN rpath 以解析同目录 .so。
        set(_flutter_rpath_targets
            ${PROJECT_NAME}
            ${PROJECT_NAME}_Standalone
            ${PROJECT_NAME}_VST3
        )
        foreach(_tgt IN LISTS _flutter_rpath_targets)
            if(TARGET ${_tgt})
                target_link_options(${_tgt} PRIVATE "-Wl,-rpath,\$ORIGIN")
            endif()
        endforeach()
    endif()

    # ---- 构建后自动安装到系统插件目录（失败仅告警，不中断构建）----
    # 所有工程（无论走 add_plugin 还是 configure_target+platform_config 手动三连）
    # 都会经过本函数，故在此统一挂载 POST_BUILD 自动安装。
    # 关闭方式：cmake 配置时传 -DJUCE_FLUTTER_AUTO_INSTALL=OFF。
    option(JUCE_FLUTTER_AUTO_INSTALL "构建后自动安装插件到系统目录（失败仅告警）" ON)
    if(JUCE_FLUTTER_AUTO_INSTALL)
        juce_flutter_try_install_plugins()
    endif()
endfunction()

# ------------------------------------------------------------
# juce_flutter_try_install_plugins
#   在构建后尝试安装插件到系统目录（跨平台）。
#   安装失败仅输出 WARNING，不中断构建。
#
#   可选参数：
#     VST3_DIR <path>        VST3 安装目录
#     VST_DIR <path>         VST 安装目录
#     COMPONENTS_DIR <path>  AU 安装目录（兼容旧参数名）
#     AU_DIR <path>          AU 安装目录（与 COMPONENTS_DIR 等价）
# ------------------------------------------------------------
function(juce_flutter_try_install_plugins)
    set(oneValueArgs COMPONENTS_DIR AU_DIR VST3_DIR VST_DIR)
    cmake_parse_arguments(ARG "" "${oneValueArgs}" "" ${ARGN})

    # 幂等保护：同一 PROJECT_NAME 只注册一次 POST_BUILD 安装命令，避免
    # platform_config 自动挂载与 add_plugin 的 INSTALL_MODE 分派重复注册
    # （否则会重复拷贝 + 重复告警）。
    get_property(_jf_installed GLOBAL PROPERTY _jf_try_installed_${PROJECT_NAME})
    if(_jf_installed)
        return()
    endif()
    set_property(GLOBAL PROPERTY _jf_try_installed_${PROJECT_NAME} ON)

    # 兼容参数别名：AU_DIR 与 COMPONENTS_DIR 等价
    if(NOT ARG_COMPONENTS_DIR AND ARG_AU_DIR)
        set(ARG_COMPONENTS_DIR "${ARG_AU_DIR}")
    endif()

    # 默认目录优先读取上游 CMake cache 变量，未定义时按平台给出默认值。
    if(NOT ARG_VST3_DIR)
        if(DEFINED JUCE_FLUTTER_SYSTEM_VST3_DIR AND NOT JUCE_FLUTTER_SYSTEM_VST3_DIR STREQUAL "")
            set(ARG_VST3_DIR "${JUCE_FLUTTER_SYSTEM_VST3_DIR}")
        elseif(APPLE)
            set(ARG_VST3_DIR "$ENV{HOME}/Library/Audio/Plug-Ins/VST3")
        elseif(WIN32)
            if(DEFINED ENV{CommonProgramFiles} AND NOT "$ENV{CommonProgramFiles}" STREQUAL "")
                set(_common_pf "$ENV{CommonProgramFiles}")
            elseif(DEFINED ENV{ProgramFiles} AND NOT "$ENV{ProgramFiles}" STREQUAL "")
                set(_common_pf "$ENV{ProgramFiles}/Common Files")
            else()
                set(_common_pf "C:/Program Files/Common Files")
            endif()
            set(ARG_VST3_DIR "${_common_pf}/VST3")
        elseif(UNIX)
            set(ARG_VST3_DIR "$ENV{HOME}/.vst3")
        endif()
    endif()

    if(NOT ARG_VST_DIR)
        if(DEFINED JUCE_FLUTTER_SYSTEM_VST_DIR AND NOT JUCE_FLUTTER_SYSTEM_VST_DIR STREQUAL "")
            set(ARG_VST_DIR "${JUCE_FLUTTER_SYSTEM_VST_DIR}")
        elseif(APPLE)
            set(ARG_VST_DIR "$ENV{HOME}/Library/Audio/Plug-Ins/VST")
        elseif(WIN32)
            if(DEFINED ENV{ProgramFiles} AND NOT "$ENV{ProgramFiles}" STREQUAL "")
                set(_pf "$ENV{ProgramFiles}")
            else()
                set(_pf "C:/Program Files")
            endif()
            set(ARG_VST_DIR "${_pf}/Steinberg/VstPlugins")
        elseif(UNIX)
            set(ARG_VST_DIR "$ENV{HOME}/.vst")
        endif()
    endif()

    if(NOT ARG_COMPONENTS_DIR)
        if(DEFINED JUCE_FLUTTER_SYSTEM_AU_DIR AND NOT JUCE_FLUTTER_SYSTEM_AU_DIR STREQUAL "")
            set(ARG_COMPONENTS_DIR "${JUCE_FLUTTER_SYSTEM_AU_DIR}")
        elseif(APPLE)
            set(ARG_COMPONENTS_DIR "$ENV{HOME}/Library/Audio/Plug-Ins/Components")
        endif()
    endif()

    set(_install_script "${CMAKE_CURRENT_FUNCTION_LIST_DIR}/InstallPluginToSystem.cmake")

    if(TARGET ${PROJECT_NAME}_VST3 AND ARG_VST3_DIR)
        set(_vst3_src "${CMAKE_BINARY_DIR}/${PROJECT_NAME}_artefacts/$<CONFIG>/VST3/${PROJECT_NAME}.vst3")
        add_custom_command(TARGET ${PROJECT_NAME}_VST3 POST_BUILD
            COMMAND ${CMAKE_COMMAND}
                "-DSOURCE=${_vst3_src}"
                "-DDESTINATION=${ARG_VST3_DIR}"
                "-DNAME=VST3"
                "-DALLOW_FAILURE=ON"
                "-P" "${_install_script}"
            COMMENT "尝试安装 VST3 到系统目录（失败仅告警）"
            VERBATIM
        )
    endif()

    if(TARGET ${PROJECT_NAME}_AU AND APPLE AND ARG_COMPONENTS_DIR)
        set(_au_src "${CMAKE_BINARY_DIR}/${PROJECT_NAME}_artefacts/$<CONFIG>/AU/${PROJECT_NAME}.component")
        add_custom_command(TARGET ${PROJECT_NAME}_AU POST_BUILD
            COMMAND ${CMAKE_COMMAND}
                "-DSOURCE=${_au_src}"
                "-DDESTINATION=${ARG_COMPONENTS_DIR}"
                "-DNAME=AU"
                "-DALLOW_FAILURE=ON"
                "-P" "${_install_script}"
            COMMENT "尝试安装 AU 到系统目录（失败仅告警）"
            VERBATIM
        )
    endif()

    if(TARGET ${PROJECT_NAME}_VST AND ARG_VST_DIR)
        if(APPLE)
            set(_vst_src "${CMAKE_BINARY_DIR}/${PROJECT_NAME}_artefacts/$<CONFIG>/VST/${PROJECT_NAME}.vst")
        elseif(WIN32)
            set(_vst_src "${CMAKE_BINARY_DIR}/${PROJECT_NAME}_artefacts/$<CONFIG>/VST/${PROJECT_NAME}.dll")
        else()
            set(_vst_src "${CMAKE_BINARY_DIR}/${PROJECT_NAME}_artefacts/$<CONFIG>/VST/${PROJECT_NAME}.so")
        endif()

        add_custom_command(TARGET ${PROJECT_NAME}_VST POST_BUILD
            COMMAND ${CMAKE_COMMAND}
                "-DSOURCE=${_vst_src}"
                "-DDESTINATION=${ARG_VST_DIR}"
                "-DNAME=VST"
                "-DALLOW_FAILURE=ON"
                "-P" "${_install_script}"
            COMMENT "尝试安装 VST 到系统目录（失败仅告警）"
            VERBATIM
        )
    endif()
endfunction()
