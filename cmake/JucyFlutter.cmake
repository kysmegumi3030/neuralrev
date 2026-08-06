# ============================================================
# JucyFlutter.cmake —— 一站式引入模块（骨架即依赖）
# ------------------------------------------------------------
# 下游插件工程只需两步即可获得完整的 JUCE8 + Flutter 骨架，
# 无需在自己仓库里复制 juce_add_plugin / BuildFlutterUI / 安装规则：
#
#   add_subdirectory(deps/JucyFlutter)        # 引入并 bootstrap
#   juce_flutter_add_plugin(MyPlugin ...)     # 一行定义插件
#
# 或（等价、更不受作用域影响的方式）：
#
#   include(deps/JucyFlutter/cmake/JucyFlutter.cmake)
#   juce_flutter_add_plugin(MyPlugin ...)
#
# 本文件负责：
#   1) bootstrap：拉取 JUCE、探测 Flutter Engine、载入链接函数
#   2) 以 CACHE FORCE 统一设置编译优化标志（跨作用域、保留“替换”语义，
#      确保与竞品对拍的波形/频谱保真度不因作用域变化而漂移）
#   3) 定义高层函数 juce_flutter_add_plugin()，封装骨架全部样板
# ============================================================

# 已引入则跳过（命令存在即视为已 bootstrap，避免重复执行 precache 等）
if(COMMAND juce_flutter_add_plugin)
    return()
endif()

# 记录 JucyFlutter 自身位置（供函数在任意作用域/文件中引用其 src、cmake 脚本）
get_filename_component(_jf_cmake_dir "${CMAKE_CURRENT_LIST_DIR}" ABSOLUTE)
get_filename_component(_jf_root "${_jf_cmake_dir}/.." ABSOLUTE)
set(JUCYFLUTTER_CMAKE_DIR "${_jf_cmake_dir}" CACHE INTERNAL "JucyFlutter cmake 目录")
set(JUCYFLUTTER_DIR       "${_jf_root}"      CACHE INTERNAL "JucyFlutter 根目录")

list(APPEND CMAKE_MODULE_PATH "${JUCYFLUTTER_CMAKE_DIR}")

# ------------------------------------------------------------
# bootstrap：JUCE / Flutter Engine / 链接函数
# ------------------------------------------------------------
include(FetchJUCE)          # 拉取并 MakeAvailable JUCE8（imported target 全局可见）
include(FlutterEngine)      # 探测 Flutter Engine（GLOBAL imported target + CACHE INTERNAL 导出）
include(JuceFlutterEngineLink)  # juce_flutter_configure_target / link_engine / platform_config

# ------------------------------------------------------------
# 编译优化标志（CACHE FORCE：保留“替换默认”语义并跨作用域生效）
# ------------------------------------------------------------
# 以 add_subdirectory 引入时本文件在子目录作用域执行，普通 set() 无法影响
# 父作用域中创建的 JUCE SharedCode / 插件目标；用 CACHE ... FORCE 使之全局生效。
# 注意：FORCE 会覆盖命令行 -DCMAKE_CXX_FLAGS_RELEASE=... （原骨架亦为硬编码，语义等价）。
# /fp:fast、/arch:AVX2、-ffp-contract=fast 直接影响与竞品对拍的波形/频谱，勿轻改。
if(CMAKE_CXX_COMPILER_ID STREQUAL "Clang" OR CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang")
    set(CMAKE_CXX_FLAGS_DEBUG          "-g -Og" CACHE STRING "" FORCE)
    set(CMAKE_C_FLAGS_RELWITHDEBINFO   "-O3 -g -DNDEBUG -ffp-contract=fast -ftime-trace" CACHE STRING "" FORCE)
    set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "-O3 -g -DNDEBUG -ffp-contract=fast -ftime-trace" CACHE STRING "" FORCE)
    set(CMAKE_CXX_FLAGS_RELEASE        "-O3 -DNDEBUG -ffp-contract=fast" CACHE STRING "" FORCE)
elseif(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    set(CMAKE_C_FLAGS_RELWITHDEBINFO   "-O3 -g -ffp-contract=fast" CACHE STRING "" FORCE)
    set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "-O3 -g -ffp-contract=fast" CACHE STRING "" FORCE)
    set(CMAKE_CXX_FLAGS_RELEASE        "-O3 -DNDEBUG -ffp-contract=fast" CACHE STRING "" FORCE)
elseif(CMAKE_CXX_COMPILER_ID STREQUAL "MSVC")
    set(CMAKE_C_FLAGS_RELWITHDEBINFO   "/O2 /DNDEBUG /fp:fast /arch:AVX2 /d2cgsummary" CACHE STRING "" FORCE)
    set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "/O2 /DNDEBUG /fp:fast /arch:AVX2 /d2cgsummary" CACHE STRING "" FORCE)
    set(CMAKE_CXX_FLAGS_RELEASE        "/O2 /DNDEBUG /fp:fast /arch:AVX2" CACHE STRING "" FORCE)
endif()

# ============================================================
# _juce_flutter_install_rules(<TGT>)
#   install(TARGETS) + 系统 VST/AU/VST3 目录安装（InstallSystemPlugins 目标）。
#   系统目录默认值可用 JUCE_FLUTTER_SYSTEM_*_DIR 缓存变量覆盖。
# ============================================================
function(_juce_flutter_install_rules TGT PRODUCT_NAME)
    install(TARGETS ${TGT} LIBRARY DESTINATION lib RUNTIME DESTINATION bin)

    if(APPLE)
        set(JUCE_FLUTTER_SYSTEM_VST3_DIR "$ENV{HOME}/Library/Audio/Plug-Ins/VST3" CACHE PATH "System VST3 dir")
        set(JUCE_FLUTTER_SYSTEM_VST_DIR  "$ENV{HOME}/Library/Audio/Plug-Ins/VST"  CACHE PATH "System VST dir")
        set(JUCE_FLUTTER_SYSTEM_AU_DIR   "$ENV{HOME}/Library/Audio/Plug-Ins/Components" CACHE PATH "System AU dir")
    elseif(WIN32)
        if(DEFINED ENV{ProgramFiles} AND NOT "$ENV{ProgramFiles}" STREQUAL "")
            set(_pf "$ENV{ProgramFiles}")
        else()
            set(_pf "C:/Program Files")
        endif()
        set(JUCE_FLUTTER_SYSTEM_VST3_DIR "${_pf}/Common Files/VST3" CACHE PATH "System VST3 dir")
        set(JUCE_FLUTTER_SYSTEM_VST_DIR  "${_pf}/Steinberg/VstPlugins" CACHE PATH "System VST dir")
    elseif(UNIX)
        set(JUCE_FLUTTER_SYSTEM_VST3_DIR "$ENV{HOME}/.vst3" CACHE PATH "System VST3 dir")
        set(JUCE_FLUTTER_SYSTEM_VST_DIR  "$ENV{HOME}/.vst"  CACHE PATH "System VST dir")
    endif()

    set(_script "${JUCYFLUTTER_CMAKE_DIR}/InstallPluginToSystem.cmake")

    if(NOT TARGET InstallSystemPlugins)
        add_custom_target(InstallSystemPlugins
            COMMENT "Install plugin bundles to system VST/AU/VST3 directories")
    endif()

    foreach(_dep IN ITEMS ${TGT}_VST3 ${TGT}_AU ${TGT}_VST)
        if(TARGET ${_dep})
            add_dependencies(InstallSystemPlugins ${_dep})
        endif()
    endforeach()

    set(_art "${CMAKE_BINARY_DIR}/${TGT}_artefacts/$<CONFIG>")
    if(TARGET ${TGT}_VST3)
        add_custom_command(TARGET InstallSystemPlugins POST_BUILD
            COMMAND ${CMAKE_COMMAND} "-DSOURCE=${_art}/VST3/${PRODUCT_NAME}.vst3"
                "-DDESTINATION=${JUCE_FLUTTER_SYSTEM_VST3_DIR}" "-DNAME=VST3"
                "-P" "${_script}"
            COMMENT "Install VST3 -> ${JUCE_FLUTTER_SYSTEM_VST3_DIR}" VERBATIM)
    endif()
    if(TARGET ${TGT}_AU AND APPLE)
        add_custom_command(TARGET InstallSystemPlugins POST_BUILD
            COMMAND ${CMAKE_COMMAND} "-DSOURCE=${_art}/AU/${PRODUCT_NAME}.component"
                "-DDESTINATION=${JUCE_FLUTTER_SYSTEM_AU_DIR}" "-DNAME=AU"
                "-P" "${_script}"
            COMMENT "Install AU -> ${JUCE_FLUTTER_SYSTEM_AU_DIR}" VERBATIM)
    endif()
    if(TARGET ${TGT}_VST)
        if(APPLE)
            set(_vst_src "${_art}/VST/${PRODUCT_NAME}.vst")
        elseif(WIN32)
            set(_vst_src "${_art}/VST/${PRODUCT_NAME}.dll")
        else()
            set(_vst_src "${_art}/VST/${PRODUCT_NAME}.so")
        endif()
        add_custom_command(TARGET InstallSystemPlugins POST_BUILD
            COMMAND ${CMAKE_COMMAND} "-DSOURCE=${_vst_src}"
                "-DDESTINATION=${JUCE_FLUTTER_SYSTEM_VST_DIR}" "-DNAME=VST"
                "-P" "${_script}"
            COMMENT "Install VST -> ${JUCE_FLUTTER_SYSTEM_VST_DIR}" VERBATIM)
    endif()
endfunction()

# ============================================================
# _juce_flutter_distribute(<TGT> <PRODUCT_NAME> <BUILD_UI_TARGET>)
#   把 flutter_assets / icudtl.dat / AOT 快照分发到各格式输出目录。
# ============================================================
function(_juce_flutter_distribute TGT PRODUCT_NAME BUI)
    set(_art "${CMAKE_BINARY_DIR}/${TGT}_artefacts/$<CONFIG>")
    if(WIN32)
        set(_icudtl "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/windows-x64/icudtl.dat")
        add_custom_command(TARGET ${BUI} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_directory
                "${CMAKE_BINARY_DIR}/flutter_assets" "${_art}/Standalone/flutter_assets"
            COMMAND ${CMAKE_COMMAND} -E copy_directory
                "${CMAKE_BINARY_DIR}/flutter_assets"
                "${_art}/VST3/${PRODUCT_NAME}.vst3/Contents/x86_64-win/flutter_assets"
            COMMAND ${CMAKE_COMMAND} -E copy_if_different "${_icudtl}" "${_art}/Standalone"
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${_icudtl}" "${_art}/VST3/${PRODUCT_NAME}.vst3/Contents/x86_64-win"
            COMMENT "分发 flutter_assets + icudtl.dat (Windows, ${FLUTTER_BUILD_MODE})"
            VERBATIM)
        if(FLUTTER_BUILD_MODE STREQUAL "Release" OR FLUTTER_BUILD_MODE STREQUAL "Profile")
            add_custom_command(TARGET ${BUI} POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${CMAKE_BINARY_DIR}/app.so" "${_art}/Standalone/app.so"
                COMMAND ${CMAKE_COMMAND} -E copy_if_different
                    "${CMAKE_BINARY_DIR}/app.so"
                    "${_art}/VST3/${PRODUCT_NAME}.vst3/Contents/x86_64-win/app.so"
                COMMENT "分发 AOT 快照 app.so (Windows ${FLUTTER_BUILD_MODE})"
                VERBATIM)
        endif()
    elseif(APPLE)
        if(FLUTTER_BUILD_MODE STREQUAL "Release" OR FLUTTER_BUILD_MODE STREQUAL "Profile")
            add_custom_command(TARGET ${BUI} POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_BINARY_DIR}/App.framework"
                    "${_art}/Standalone/${PRODUCT_NAME}.app/Contents/Frameworks/App.framework"
                COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_BINARY_DIR}/App.framework"
                    "${_art}/VST3/${PRODUCT_NAME}.vst3/Contents/Frameworks/App.framework"
                COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_BINARY_DIR}/flutter_assets"
                    "${_art}/Standalone/${PRODUCT_NAME}.app/Contents/Resources/flutter_assets"
                COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_BINARY_DIR}/flutter_assets"
                    "${_art}/VST3/${PRODUCT_NAME}.vst3/Contents/Resources/flutter_assets"
                COMMENT "分发 AOT App.framework + flutter_assets (macOS ${FLUTTER_BUILD_MODE})"
                VERBATIM)
        else()
            add_custom_command(TARGET ${BUI} POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_BINARY_DIR}/flutter_assets"
                    "${_art}/Standalone/flutter_assets"
                COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_BINARY_DIR}/flutter_assets"
                    "${_art}/Standalone/${PRODUCT_NAME}.app/Contents/Resources/flutter_assets"
                COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_BINARY_DIR}/flutter_assets"
                    "${_art}/VST3/${PRODUCT_NAME}.vst3/Contents/Resources/flutter_assets"
                COMMENT "分发 flutter_assets (macOS, ${FLUTTER_BUILD_MODE})"
                VERBATIM)
        endif()
    elseif(UNIX)
        add_custom_command(TARGET ${BUI} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_BINARY_DIR}/flutter_assets"
                "${_art}/Standalone/flutter_assets"
            COMMAND ${CMAKE_COMMAND} -E copy_directory "${CMAKE_BINARY_DIR}/flutter_assets"
                "${_art}/VST3/${PRODUCT_NAME}.vst3/Contents/x86_64-linux/flutter_assets"
            COMMENT "分发 flutter_assets (Linux, ${FLUTTER_BUILD_MODE})"
            VERBATIM)
        if(FLUTTER_BUILD_MODE STREQUAL "Release" OR FLUTTER_BUILD_MODE STREQUAL "Profile")
            add_custom_command(TARGET ${BUI} POST_BUILD
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${CMAKE_BINARY_DIR}/lib/libapp.so"
                    "${_art}/Standalone/libapp.so"
                COMMAND ${CMAKE_COMMAND} -E copy_if_different "${CMAKE_BINARY_DIR}/lib/libapp.so"
                    "${_art}/VST3/${PRODUCT_NAME}.vst3/Contents/x86_64-linux/libapp.so"
                COMMENT "分发 AOT 快照 libapp.so (Linux ${FLUTTER_BUILD_MODE})"
                VERBATIM)
        endif()
    endif()
endfunction()

# ============================================================
# _juce_flutter_build_ui(<TGT> <PRODUCT_NAME> <FLUTTER_UI_DIR>)
#   构建后编译 Flutter UI（Debug=JIT bundle / Profile,Release=AOT），
#   并把产物分发到各平台/格式输出目录。内部目标名 ${TGT}_BuildFlutterUI
#   （每插件独立，支持同一构建树多插件）。
# ============================================================
function(_juce_flutter_build_ui TGT PRODUCT_NAME FLUTTER_UI_DIR)
    if(APPLE)
        set(_tp "darwin")
    elseif(WIN32)
        set(_tp "windows-x64")
    elseif(UNIX)
        set(_tp "linux-x64")
    else()
        set(_tp "darwin")
    endif()

    set(_bui "${TGT}_BuildFlutterUI")

    if(NOT FLUTTER_BUILD_MODE STREQUAL "Debug")
        if(WIN32)
            set(_aot_platform "windows")
        elseif(APPLE)
            set(_aot_platform "macos")
        else()
            set(_aot_platform "linux")
        endif()
        string(TOLOWER "${FLUTTER_BUILD_MODE}" _aot_build_mode)
        set(_aot_runner_dir "${CMAKE_BINARY_DIR}/_flutter_aot_runner")

        add_custom_target(${_bui}
            COMMAND ${CMAKE_COMMAND}
                -DFLUTTER_EXECUTABLE=${FLUTTER_EXECUTABLE}
                -DSOURCE_DIR=${FLUTTER_UI_DIR}
                -DAOT_DIR=${_aot_runner_dir}
                -DPLATFORM=${_aot_platform}
                -DBUILD_MODE=${_aot_build_mode}
                -DARTIFACTS_DIR=${CMAKE_BINARY_DIR}
                -P "${JUCYFLUTTER_CMAKE_DIR}/FlutterAOTBuild.cmake"
            COMMENT "Flutter AOT 编译（${_aot_platform}, ${FLUTTER_BUILD_MODE}）"
            VERBATIM
        )
    else()
        add_custom_target(${_bui}
            COMMAND ${CMAKE_COMMAND} -E echo "[Flutter] pub get (${FLUTTER_BUILD_MODE})..."
            COMMAND "${FLUTTER_EXECUTABLE}" pub get
            COMMAND ${CMAKE_COMMAND} -E echo "[Flutter] build bundle (${FLUTTER_BUILD_MODE})..."
            COMMAND "${FLUTTER_EXECUTABLE}" build bundle
                    --target-platform ${_tp}
                    --asset-dir "${CMAKE_BINARY_DIR}/flutter_assets"
            WORKING_DIRECTORY "${FLUTTER_UI_DIR}"
            COMMENT "Flutter JIT bundle（${FLUTTER_BUILD_MODE}）"
            VERBATIM
        )
    endif()

    _juce_flutter_distribute(${TGT} "${PRODUCT_NAME}" ${_bui})

    foreach(_t IN ITEMS ${TGT} ${TGT}_Standalone ${TGT}_VST3)
        if(TARGET ${_t})
            add_dependencies(${_t} ${_bui})
        endif()
    endforeach()
endfunction()

# ============================================================
# juce_flutter_add_plugin(<TARGET> ...)
#   一行封装：juce_add_plugin + 共享 UI/embedder 源 + DSP 源 +
#   编译宏/头文件/JUCE 模块链接 + 引擎链接/平台配置 + BuildFlutterUI +
#   各平台产物分发 + 系统安装规则。
#
# 必填：
#   TARGET（位置参数）             插件目标名（同时作为默认 PRODUCT_NAME）
# 单值可选：
#   PLUGIN_CODE / MANUFACTURER_CODE  4 字符标识码（默认 Jfp1 / MyCo）
#   COMPANY_NAME / COMPANY_WEBSITE / COMPANY_EMAIL
#   PRODUCT_NAME / DESCRIPTION / VERSION
#   FLUTTER_UI_DIR                 flutter_ui 源目录（默认 ${CMAKE_SOURCE_DIR}/flutter_ui）
#   EDITOR_WIDTH / EDITOR_HEIGHT / EDITOR_MIN_WIDTH / EDITOR_MIN_HEIGHT /
#   EDITOR_MAX_WIDTH / EDITOR_MAX_HEIGHT   编辑器窗口尺寸（有默认）
#   EDITOR_FIXED_ASPECT            固定纵横比字符串（如 "960.0/596.0"）。仅在显式
#                                  传入时才 emit JUCE_FLUTTER_EDITOR_FIXED_ASPECT
#                                  宏（PluginEditor 用 #ifdef 消费）；不传则不锁定
#                                  纵横比（保持模板 editor 家族的既有行为）。
#   INSTALL_MODE                   system(默认) | try | try-only
#                                    system  : install(TARGETS) + InstallSystemPlugins 目标
#                                    try     : install(TARGETS) + juce_flutter_try_install_plugins()
#                                    try-only: 仅 juce_flutter_try_install_plugins()
# 零值开关（出现即启用）：
#   NO_TEMPLATE_EDITOR             不加入模板自带 src/ui/PluginEditor.cpp/.h
#                                  （用于自带 PluginEditor 的工程，避免类重定义）
#   NO_EDITOR_SIZE_DEFINES         不 emit 6 个 JUCE_FLUTTER_EDITOR_* 尺寸宏
#                                  （自带 editor 且自行硬编码尺寸的工程用）
#   NO_PERF_READOUT                关闭共享桥接层 perf_update 性能读数块
#                                  （定义 JUCE_FLUTTER_ENABLE_PERF_READOUT=0）。
#                                  Processor 未实现 getDspTimeNs()/getLastBlockSize()
#                                  的工程需传此项，方可在不改动自身代码下编译通过。
# 多值可选：
#   FORMATS                        导出格式（默认 VST3 Standalone AU AUv3）
#   DSP_SOURCES                    本插件 DSP/处理器源文件（可含自带 PluginEditor）
#   EXTRA_INCLUDE_DIRS             额外头文件目录（PRIVATE，追加在标准目录之后）
#   BEFORE_INCLUDE_DIRS            需优先于模板目录解析的头文件目录（BEFORE PRIVATE）
#                                  ——自带 PluginEditor.h 屏蔽模板同名头时使用
# ============================================================
function(juce_flutter_add_plugin TGT)
    set(_opts NO_TEMPLATE_EDITOR NO_EDITOR_SIZE_DEFINES NO_PERF_READOUT)
    set(_one
        PLUGIN_CODE MANUFACTURER_CODE COMPANY_NAME COMPANY_WEBSITE COMPANY_EMAIL
        PRODUCT_NAME DESCRIPTION VERSION FLUTTER_UI_DIR
        EDITOR_WIDTH EDITOR_HEIGHT EDITOR_MIN_WIDTH EDITOR_MIN_HEIGHT
        EDITOR_MAX_WIDTH EDITOR_MAX_HEIGHT EDITOR_FIXED_ASPECT INSTALL_MODE)
    set(_multi FORMATS DSP_SOURCES EXTRA_INCLUDE_DIRS BEFORE_INCLUDE_DIRS)
    cmake_parse_arguments(P "${_opts}" "${_one}" "${_multi}" ${ARGN})

    if(NOT P_INSTALL_MODE)
        set(P_INSTALL_MODE "system")
    endif()

    # ---- 默认值 ----
    if(NOT P_PLUGIN_CODE)
        set(P_PLUGIN_CODE "Jfp1")
    endif()
    if(NOT P_MANUFACTURER_CODE)
        set(P_MANUFACTURER_CODE "MyCo")
    endif()
    if(NOT P_COMPANY_NAME)
        set(P_COMPANY_NAME "AshunSoundMachines")
    endif()
    if(NOT P_COMPANY_WEBSITE)
        set(P_COMPANY_WEBSITE "https://www.ashunsoundmachines.com/")
    endif()
    if(NOT P_COMPANY_EMAIL)
        set(P_COMPANY_EMAIL "Sales@ashunsoundmachines.com")
    endif()
    if(NOT P_PRODUCT_NAME)
        set(P_PRODUCT_NAME "${TGT}")
    endif()
    if(NOT P_DESCRIPTION)
        set(P_DESCRIPTION "An audio plugin with Flutter UI powered by JUCE 8")
    endif()
    if(NOT P_VERSION)
        set(P_VERSION "1.0.0")
    endif()
    if(NOT P_FORMATS)
        set(P_FORMATS VST3 Standalone AU AUv3)
    endif()
    if(NOT P_FLUTTER_UI_DIR)
        set(P_FLUTTER_UI_DIR "${CMAKE_SOURCE_DIR}/flutter_ui")
    endif()
    if(NOT P_EDITOR_WIDTH)
        set(P_EDITOR_WIDTH 960)
    endif()
    if(NOT P_EDITOR_HEIGHT)
        set(P_EDITOR_HEIGHT 596)
    endif()
    if(NOT P_EDITOR_MIN_WIDTH)
        set(P_EDITOR_MIN_WIDTH 480)
    endif()
    if(NOT P_EDITOR_MIN_HEIGHT)
        set(P_EDITOR_MIN_HEIGHT 298)
    endif()
    if(NOT P_EDITOR_MAX_WIDTH)
        set(P_EDITOR_MAX_WIDTH 1920)
    endif()
    if(NOT P_EDITOR_MAX_HEIGHT)
        set(P_EDITOR_MAX_HEIGHT 1192)
    endif()

    set(_ui "${JUCYFLUTTER_DIR}/src/ui")

    juce_add_plugin(${TGT}
        COMPANY_NAME                "${P_COMPANY_NAME}"
        COMPANY_WEBSITE             "${P_COMPANY_WEBSITE}"
        COMPANY_EMAIL               "${P_COMPANY_EMAIL}"
        IS_SYNTH                    FALSE
        NEEDS_MIDI_INPUT            FALSE
        NEEDS_MIDI_OUTPUT           FALSE
        IS_MIDI_EFFECT              FALSE
        EDITOR_WANTS_KEYBOARD_FOCUS TRUE
        COPY_PLUGIN_AFTER_BUILD     FALSE
        PLUGIN_MANUFACTURER_CODE    "${P_MANUFACTURER_CODE}"
        PLUGIN_CODE                 "${P_PLUGIN_CODE}"
        FORMATS                     ${P_FORMATS}
        PRODUCT_NAME                "${P_PRODUCT_NAME}"
        DESCRIPTION                 "${P_DESCRIPTION}"
        VERSION                     "${P_VERSION}"
    )

    # ---- 共享 UI/embedder 源（来自 JucyFlutter）+ 本插件 DSP 源 ----
    # 模板 PluginEditor 仅在未声明 NO_TEMPLATE_EDITOR 时加入；自带 editor 的工程
    # 传 NO_TEMPLATE_EDITOR 并把自己的 PluginEditor.cpp/.h 放进 DSP_SOURCES。
    if(NOT P_NO_TEMPLATE_EDITOR)
        target_sources(${TGT} PRIVATE
            ${_ui}/PluginEditor.cpp
            ${_ui}/PluginEditor.h
        )
    endif()
    target_sources(${TGT} PRIVATE
        ${_ui}/FlutterEmbedder.cpp
        ${_ui}/FlutterEmbedder.h
        ${_ui}/FlutterEnginePrewarmer.h
        $<$<PLATFORM_ID:Darwin>:${_ui}/FlutterEmbedder_mac.mm>
        $<$<PLATFORM_ID:Darwin>:${_ui}/FlutterEnginePrewarmer_mac.mm>
        $<$<PLATFORM_ID:Windows>:${_ui}/FlutterEmbedder_win.cpp>
        $<$<PLATFORM_ID:Linux>:${_ui}/FlutterEmbedder_linux.cpp>
        ${_ui}/AudioParameterBridge.cpp
        ${_ui}/AudioParameterBridge.h
        ${P_DSP_SOURCES}
    )

    # ---- macOS ObjC++ 源强制启用 ARC（生成器无关）----
    # 说明：XCODE_ATTRIBUTE_CLANG_ENABLE_OBJC_ARC 仅对 Xcode 生成器生效，
    # Ninja/Makefile 生成器下会被静默忽略，导致 .mm 以 MRC 编译 →
    # 这些文件全程用 = nil / CFBridgingRelease（ARC 语义），MRC 下会
    # 泄漏 FlutterEngine/VC 等对象。此处对具体源文件加 -fobjc-arc，
    # 保证所有生成器下 ARC 一致生效。
    if(APPLE)
        set_source_files_properties(
            ${_ui}/FlutterEmbedder_mac.mm
            ${_ui}/FlutterEnginePrewarmer_mac.mm
            TARGET_DIRECTORY ${TGT}
            PROPERTIES COMPILE_OPTIONS "-fobjc-arc"
        )
    endif()

    # ---- 通用编译宏 / 头文件目录 / JUCE 模块链接 ----
    # 不强加模板 src/dsp/common：各工程通过 EXTRA_INCLUDE_DIRS 显式声明自身所需的
    # 公共头目录（避免把模板版本头文件意外置于下游同名头之前）。
    juce_flutter_configure_target(${TGT}
        UI_SOURCE_DIR ${_ui}
        EXTRA_INCLUDE_DIRS ${P_EXTRA_INCLUDE_DIRS}
    )

    # ---- 优先解析的头文件目录（自带 PluginEditor.h 屏蔽模板同名头）----
    if(P_BEFORE_INCLUDE_DIRS)
        target_include_directories(${TGT} BEFORE PRIVATE ${P_BEFORE_INCLUDE_DIRS})
    endif()

    # ---- 编辑器窗口尺寸宏（自带 editor 硬编码尺寸的工程用 NO_EDITOR_SIZE_DEFINES 跳过）----
    # _USE_MATH_DEFINES 始终 emit（MSVC 下启用 M_PI 等数学常量，纯宏、无副作用）。
    target_compile_definitions(${TGT} PUBLIC _USE_MATH_DEFINES)
    if(NOT P_NO_EDITOR_SIZE_DEFINES)
        target_compile_definitions(${TGT} PUBLIC
            JUCE_FLUTTER_EDITOR_DEFAULT_WIDTH=${P_EDITOR_WIDTH}
            JUCE_FLUTTER_EDITOR_DEFAULT_HEIGHT=${P_EDITOR_HEIGHT}
            JUCE_FLUTTER_EDITOR_MIN_WIDTH=${P_EDITOR_MIN_WIDTH}
            JUCE_FLUTTER_EDITOR_MIN_HEIGHT=${P_EDITOR_MIN_HEIGHT}
            JUCE_FLUTTER_EDITOR_MAX_WIDTH=${P_EDITOR_MAX_WIDTH}
            JUCE_FLUTTER_EDITOR_MAX_HEIGHT=${P_EDITOR_MAX_HEIGHT}
        )
    endif()
    # 固定纵横比：仅在显式传入时锁定（PluginEditor 用 #ifdef 消费）。
    if(P_EDITOR_FIXED_ASPECT)
        target_compile_definitions(${TGT} PUBLIC
            JUCE_FLUTTER_EDITOR_FIXED_ASPECT=${P_EDITOR_FIXED_ASPECT})
    endif()

    # 性能读数开关：Processor 未实现 getDspTimeNs()/getLastBlockSize() 的工程传
    # NO_PERF_READOUT 关闭共享桥接层 perf 块（PUBLIC 以作用于 SharedCode 编译单元）。
    if(P_NO_PERF_READOUT)
        target_compile_definitions(${TGT} PUBLIC JUCE_FLUTTER_ENABLE_PERF_READOUT=0)
    endif()

    # ---- Cortex-A53 目标优化（仅 aarch64/arm64；target 作用域以跨越 add_subdirectory）----
    # SharedCode 承载 DSP 与 JUCE 模块编译，需一并施加；主目标同施。
    if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64|ARM64"
       AND (CMAKE_CXX_COMPILER_ID STREQUAL "GNU"
            OR CMAKE_CXX_COMPILER_ID STREQUAL "Clang"
            OR CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang"))
        set(_arm_opts
            $<$<CONFIG:Release>:-mcpu=cortex-a53>
            $<$<CONFIG:Release>:-fno-math-errno>
            $<$<CONFIG:Release>:-fno-trapping-math>
            $<$<CONFIG:RelWithDebInfo>:-mcpu=cortex-a53>
            $<$<CONFIG:RelWithDebInfo>:-fno-math-errno>
            $<$<CONFIG:RelWithDebInfo>:-fno-trapping-math>)
        target_compile_options(${TGT} PRIVATE ${_arm_opts})
        if(TARGET ${TGT})
            get_target_property(_shared ${TGT} JUCE_SHARED_CODE_TARGET)
        endif()
        if(TARGET ${TGT}_SharedCode)
            target_compile_options(${TGT}_SharedCode PRIVATE ${_arm_opts})
        endif()
    endif()

    # ---- 引擎 DLL/SO 唯一命名（基于目标名，防多插件同进程 UI 串台）----
    set(FLUTTER_ENGINE_DLL_NAME "flutter_windows_${TGT}.dll")
    set(FLUTTER_ENGINE_SO_NAME  "libflutter_linux_gtk_${TGT}.so")

    juce_flutter_link_engine(${TGT})
    juce_flutter_platform_config(${TGT})

    _juce_flutter_build_ui(${TGT} "${P_PRODUCT_NAME}" "${P_FLUTTER_UI_DIR}")

    # ---- 安装规则（按 INSTALL_MODE 分派）----
    if(P_INSTALL_MODE STREQUAL "system")
        # install(TARGETS) + InstallSystemPlugins 目标（手动触发系统目录安装）
        _juce_flutter_install_rules(${TGT} "${P_PRODUCT_NAME}")
    elseif(P_INSTALL_MODE STREQUAL "try")
        # install(TARGETS) + 构建后自动尝试安装（失败仅告警）
        install(TARGETS ${TGT} LIBRARY DESTINATION lib RUNTIME DESTINATION bin)
        juce_flutter_try_install_plugins()
    elseif(P_INSTALL_MODE STREQUAL "try-only")
        # 仅构建后自动尝试安装，不生成 install(TARGETS) 规则
        juce_flutter_try_install_plugins()
    else()
        message(FATAL_ERROR "[JucyFlutter] 未知 INSTALL_MODE: ${P_INSTALL_MODE}"
                            "（应为 system / try / try-only）")
    endif()
endfunction()
