# ============================================================
# FlutterEngine.cmake
# 查找 Flutter SDK 并配置 Flutter Engine 嵌入库
# ============================================================

set(FLUTTER_ENGINE_FOUND FALSE)

# ----------------------------------------------------------------
# 1. 查找 flutter 可执行文件
# ----------------------------------------------------------------
find_program(FLUTTER_EXECUTABLE
    NAMES flutter flutter.bat
    HINTS
        "$ENV{FLUTTER_ROOT}/bin"
        "$ENV{HOME}/flutter/bin"
        "C:/flutter/bin"
        "/usr/local/flutter/bin"
        "/opt/flutter/bin"
    DOC "Flutter SDK 可执行文件路径"
)

if(NOT FLUTTER_EXECUTABLE)
    message(WARNING "[Flutter] 未在 PATH 中找到 flutter 命令。"
                    "请安装 Flutter SDK 并将其 bin 目录添加到 PATH，"
                    "或设置 FLUTTER_ROOT 环境变量。"
                    "将使用 JUCE 原生 UI 作为回退。")
    return()
endif()

message(STATUS "[Flutter] 找到 flutter: ${FLUTTER_EXECUTABLE}")

# ----------------------------------------------------------------
# 2. 获取 Flutter SDK 根目录
# ----------------------------------------------------------------
# 先用 `flutter --flutter-root` 直接查询 SDK 真实路径（最可靠，支持 Homebrew shim 等场景）
execute_process(
    COMMAND "${FLUTTER_EXECUTABLE}" --flutter-root
    OUTPUT_VARIABLE _flutter_root_out
    RESULT_VARIABLE _flutter_root_result
    ERROR_QUIET
    OUTPUT_STRIP_TRAILING_WHITESPACE
)

if(_flutter_root_result EQUAL 0 AND _flutter_root_out)
    set(FLUTTER_SDK_DIR "${_flutter_root_out}" CACHE PATH "Flutter SDK 根目录")
else()
    # 回退：从可执行文件路径推断（dirname/dirname 方式），并 resolve symlink
    execute_process(
        COMMAND readlink -f "${FLUTTER_EXECUTABLE}"
        OUTPUT_VARIABLE _flutter_real_exe
        RESULT_VARIABLE _readlink_result
        ERROR_QUIET
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    if(_readlink_result EQUAL 0 AND _flutter_real_exe)
        get_filename_component(FLUTTER_BIN_DIR "${_flutter_real_exe}" DIRECTORY)
    else()
        get_filename_component(FLUTTER_BIN_DIR "${FLUTTER_EXECUTABLE}" DIRECTORY)
    endif()
    get_filename_component(FLUTTER_SDK_DIR "${FLUTTER_BIN_DIR}" DIRECTORY)
    set(FLUTTER_SDK_DIR "${FLUTTER_SDK_DIR}" CACHE PATH "Flutter SDK 根目录")
endif()

# 如果还是找不到 artifacts（例如 Homebrew 将 flutter 安装到 share/ 下），
# 再尝试若干个 Homebrew 及常见安装路径
if(NOT EXISTS "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine")
    set(_fallback_candidates
        "/opt/homebrew/share/flutter"
        "/usr/local/share/flutter"
        "$ENV{HOME}/flutter"
        "$ENV{FLUTTER_ROOT}"
    )
    foreach(_fb IN LISTS _fallback_candidates)
        if(EXISTS "${_fb}/bin/cache/artifacts/engine")
            set(FLUTTER_SDK_DIR "${_fb}" CACHE PATH "Flutter SDK 根目录" FORCE)
            message(STATUS "[Flutter] 从备用路径找到 SDK: ${FLUTTER_SDK_DIR}")
            break()
        endif()
    endforeach()
endif()

# ----------------------------------------------------------------
# 2.6 规范化 SDK 路径（Windows / Git-Bash）
# ------------------------------------------------------------
# 在 Windows 上通过 Git-Bash 运行时，`readlink -f` / MSYS 会把 junction
# （如 scoop 的 flutter/current）解析成 POSIX 形式 "/c/Users/..."，
# 原生 Windows CMake 无法用它做 find_library / find_path。
# 这里把 "/<drive>/rest" 还原为 "<DRIVE>:/rest"。
# ----------------------------------------------------------------
if(WIN32 AND FLUTTER_SDK_DIR MATCHES "^/([A-Za-z])/(.*)$")
    string(TOUPPER "${CMAKE_MATCH_1}" _drive)
    set(_native_sdk "${_drive}:/${CMAKE_MATCH_2}")
    # 同时更新普通变量（当前作用域可能存在普通变量遮蔽 CACHE 变量）与 CACHE。
    set(FLUTTER_SDK_DIR "${_native_sdk}")
    set(FLUTTER_SDK_DIR "${_native_sdk}" CACHE PATH "Flutter SDK 根目录" FORCE)
    message(STATUS "[Flutter] SDK 路径已规范化为原生 Windows 形式: ${FLUTTER_SDK_DIR}")
endif()

message(STATUS "[Flutter] Flutter SDK 目录: ${FLUTTER_SDK_DIR}")

# ----------------------------------------------------------------
# 2.5 Flutter 构建模式（Debug / Profile / Release）
# ----------------------------------------------------------------
# 自动跟随 CMAKE_BUILD_TYPE：
#   Debug              → Flutter Debug   （JIT，含断言，kernel_blob.bin）
#   RelWithDebInfo     → Flutter Profile  （JIT，含 profiling，kernel_blob.bin）
#   Release/MinSizeRel → Flutter Release  （AOT，最快，app.dll / App.framework / libapp.so）
#
# 在 include(cmake/FlutterEngine.cmake) 之前手动设置（最高优先级）：
#   set(FLUTTER_BUILD_MODE "Profile")
# 或命令行覆盖（持久化到 CMake 缓存）：
#   -DFLUTTER_BUILD_MODE_OVERRIDE=Profile
#
# Release AOT 模式不需要手动配置 runner：
# cmake/FlutterAOTBuild.cmake 会在 BUILD 目录自动创建临时 runner，不污染 git 仓库。
# 优先级：FLUTTER_BUILD_MODE_OVERRIDE > 本次 include 前用户显式设置的普通变量 >
# 按 CMAKE_BUILD_TYPE 自动推导。
#
# 注意：本模块末尾把 FLUTTER_BUILD_MODE 以 CACHE INTERNAL 导出（供 add_subdirectory
# 方式下父作用域的 juce_flutter_add_plugin() 读取）。这会使其在下次 configure 时
# 已 DEFINED，故不能用 `NOT DEFINED` 判断“用户是否显式设置”——否则切换
# CMAKE_BUILD_TYPE 后仍固着旧值。这里改为：仅当普通变量值与我们导出的 CACHE 值
# 不同才视为“用户显式设置”，否则一律按 BUILD_TYPE 重新推导，消除固着。
if(DEFINED FLUTTER_BUILD_MODE_OVERRIDE)
    set(FLUTTER_BUILD_MODE "${FLUTTER_BUILD_MODE_OVERRIDE}")
    message(STATUS "[Flutter] FLUTTER_BUILD_MODE = ${FLUTTER_BUILD_MODE} (命令行覆盖)")
elseif(DEFINED FLUTTER_BUILD_MODE AND NOT "${FLUTTER_BUILD_MODE}" STREQUAL "$CACHE{FLUTTER_BUILD_MODE}")
    message(STATUS "[Flutter] FLUTTER_BUILD_MODE = ${FLUTTER_BUILD_MODE} (CMakeLists.txt 设置)")
else()
    if(CMAKE_BUILD_TYPE STREQUAL "Release" OR CMAKE_BUILD_TYPE STREQUAL "MinSizeRel")
        set(FLUTTER_BUILD_MODE "Release")
    elseif(CMAKE_BUILD_TYPE STREQUAL "RelWithDebInfo")
        set(FLUTTER_BUILD_MODE "Profile")
    else()
        set(FLUTTER_BUILD_MODE "Debug")
    endif()
    message(STATUS "[Flutter] FLUTTER_BUILD_MODE = ${FLUTTER_BUILD_MODE} (自动，CMAKE_BUILD_TYPE=${CMAKE_BUILD_TYPE})")
endif()

# 向后兼容：FLUTTER_ENGINE_RELEASE 在 Profile 和 Release AOT 模式时均为 ON
# （两者都使用 AOT 引擎，需要 app.dll）
if(FLUTTER_BUILD_MODE STREQUAL "Release" OR FLUTTER_BUILD_MODE STREQUAL "Profile")
    set(FLUTTER_ENGINE_RELEASE ON)
else()
    set(FLUTTER_ENGINE_RELEASE OFF)
endif()

# ----------------------------------------------------------------
# 3. 确定目标平台标识
# ----------------------------------------------------------------
if(WIN32)
    set(FLUTTER_TARGET_PLATFORM "windows-x64"      CACHE STRING "Flutter 目标平台")
    set(FLUTTER_ENGINE_LIB_NAME "flutter_windows"  CACHE STRING "Flutter Engine 库名")
    set(FLUTTER_ENGINE_LIB_FILE "flutter_windows.dll")
    set(FLUTTER_ENGINE_IMPORT_FILE "flutter_windows.dll.lib")
    if(FLUTTER_BUILD_MODE STREQUAL "Release")
        set(FLUTTER_ENGINE_SEARCH_PATHS
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/windows-x64-release"
        )
    elseif(FLUTTER_BUILD_MODE STREQUAL "Profile")
        set(FLUTTER_ENGINE_SEARCH_PATHS
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/windows-x64-profile"
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/windows-x64"
        )
    else() # Debug
        set(FLUTTER_ENGINE_SEARCH_PATHS
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/windows-x64"
        )
    endif()

elseif(APPLE)
    if(CMAKE_HOST_SYSTEM_PROCESSOR MATCHES "arm64")
        set(FLUTTER_ENGINE_SEARCH_ARCHS "darwin-arm64;darwin-x64")
    else()
        set(FLUTTER_ENGINE_SEARCH_ARCHS "darwin-x64;darwin-arm64")
    endif()

    set(FLUTTER_TARGET_PLATFORM "macos" CACHE STRING "Flutter 目标平台")
    set(FLUTTER_ENGINE_LIB_NAME "FlutterMacOS" CACHE STRING "Flutter Engine 库名")
    set(FLUTTER_ENGINE_LIB_FILE "FlutterMacOS.framework")

    set(FLUTTER_ENGINE_SEARCH_PATHS "")
    foreach(_arch IN LISTS FLUTTER_ENGINE_SEARCH_ARCHS)
        if(FLUTTER_BUILD_MODE STREQUAL "Release")
            list(APPEND FLUTTER_ENGINE_SEARCH_PATHS
                "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/${_arch}-release"
                "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/${_arch}"
            )
        elseif(FLUTTER_BUILD_MODE STREQUAL "Profile")
            list(APPEND FLUTTER_ENGINE_SEARCH_PATHS
                "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/${_arch}-profile"
                "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/${_arch}"
            )
        else() # Debug
            list(APPEND FLUTTER_ENGINE_SEARCH_PATHS
                "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/${_arch}"
                "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/${_arch}-debug"
            )
        endif()
    endforeach()

elseif(UNIX)
    set(FLUTTER_TARGET_PLATFORM "linux-x64"        CACHE STRING "Flutter 目标平台")
    set(FLUTTER_ENGINE_LIB_NAME "flutter_linux_gtk" CACHE STRING "Flutter Engine 库名")
    set(FLUTTER_ENGINE_LIB_FILE "libflutter_linux_gtk.so")
    if(FLUTTER_BUILD_MODE STREQUAL "Release")
        set(FLUTTER_ENGINE_SEARCH_PATHS
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/linux-x64-release"
        )
    elseif(FLUTTER_BUILD_MODE STREQUAL "Profile")
        set(FLUTTER_ENGINE_SEARCH_PATHS
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/linux-x64-profile"
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/linux-x64"
        )
    else() # Debug
        set(FLUTTER_ENGINE_SEARCH_PATHS
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/linux-x64"
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/linux-x64-debug"
        )
    endif()
endif()

# ----------------------------------------------------------------
# 4. 预缓存 Flutter Engine（确保工件已下载）
#    更稳健地映射到 flutter precache 支持的参数（--macos/--linux/--windows）
# ----------------------------------------------------------------
if(APPLE)
    set(FLUTTER_PRECACHE_ARG "--macos")
elseif(WIN32)
    set(FLUTTER_PRECACHE_ARG "--windows")
elseif(UNIX)
    set(FLUTTER_PRECACHE_ARG "--linux")
else()
    set(FLUTTER_PRECACHE_ARG "")
endif()

execute_process(
    COMMAND "${FLUTTER_EXECUTABLE}" precache ${FLUTTER_PRECACHE_ARG}
    RESULT_VARIABLE FLUTTER_PRECACHE_RESULT
    OUTPUT_QUIET
    ERROR_QUIET
)

if(NOT FLUTTER_PRECACHE_RESULT EQUAL 0)
    message(STATUS "[Flutter] flutter precache 跳过或失败（可能已缓存或网络问题）")
endif()

# ----------------------------------------------------------------
# 5. 查找 Flutter Engine 库文件
# ----------------------------------------------------------------
if(WIN32)
    # 直接遍历 FLUTTER_ENGINE_SEARCH_PATHS 查找，不写入 CMake 缓存。
    # 这样每次 configure 都能根据 FLUTTER_ENGINE_RELEASE 自动选择正确的引擎目录，
    # 切换 CMAKE_BUILD_TYPE 后无需手动 -U 清除缓存变量。
    set(FLUTTER_ENGINE_LIBRARY "")
    set(FLUTTER_ENGINE_IMPLIB  "")
    foreach(_search_dir IN LISTS FLUTTER_ENGINE_SEARCH_PATHS)
        if(NOT FLUTTER_ENGINE_LIBRARY AND EXISTS "${_search_dir}/flutter_windows.dll")
            set(FLUTTER_ENGINE_LIBRARY "${_search_dir}/flutter_windows.dll")
        endif()
        if(NOT FLUTTER_ENGINE_IMPLIB AND EXISTS "${_search_dir}/flutter_windows.dll.lib")
            set(FLUTTER_ENGINE_IMPLIB "${_search_dir}/flutter_windows.dll.lib")
        endif()
        if(FLUTTER_ENGINE_LIBRARY AND FLUTTER_ENGINE_IMPLIB)
            break()
        endif()
    endforeach()
else()
    # 在 macOS 上，优先查找 FlutterMacOS.xcframework（现代 Flutter SDK）
    # 每次 configure 都重新搜索（不写缓存），确保切换 CMAKE_BUILD_TYPE 后
    # 自动选择正确的引擎目录（Release→product, Profile→release, Debug→debug）。
    if(APPLE)
        # 将内部变量设为空，确保从零开始搜索
        set(FLUTTER_ENGINE_LIBRARY          "")
        set(FLUTTER_ENGINE_INCLUDE_DIR       "")
        set(FLUTTER_ENGINE_FRAMEWORK_DIR     "")
        set(FLUTTER_ENGINE_XCFRAMEWORK_PATH  "")

        # Step A: 查找 .xcframework 目录（不缓存）
        set(_xcfw_path "")
        foreach(_search_dir IN LISTS FLUTTER_ENGINE_SEARCH_PATHS)
            if(NOT _xcfw_path AND EXISTS "${_search_dir}/FlutterMacOS.xcframework")
                set(_xcfw_path "${_search_dir}/FlutterMacOS.xcframework")
            endif()
        endforeach()

        if(_xcfw_path)
            # xcframework 内含 universal fat framework，路径为:
            #   FlutterMacOS.xcframework/macos-arm64_x86_64/FlutterMacOS.framework
            set(_xcfw_inner "${_xcfw_path}/macos-arm64_x86_64/FlutterMacOS.framework")
            if(NOT EXISTS "${_xcfw_inner}")
                # 如果目录名不同（例如只有 x86_64 或只有 arm64），做模糊查找
                file(GLOB _xcfw_inner_candidates
                    "${_xcfw_path}/macos-*/FlutterMacOS.framework")
                if(_xcfw_inner_candidates)
                    list(GET _xcfw_inner_candidates 0 _xcfw_inner)
                endif()
            endif()

            if(EXISTS "${_xcfw_inner}")
                # 实际动态库位于 framework 内（遵循 Versions/A 或直接）
                if(EXISTS "${_xcfw_inner}/Versions/A/FlutterMacOS")
                    set(FLUTTER_ENGINE_LIBRARY "${_xcfw_inner}/Versions/A/FlutterMacOS")
                elseif(EXISTS "${_xcfw_inner}/FlutterMacOS")
                    set(FLUTTER_ENGINE_LIBRARY "${_xcfw_inner}/FlutterMacOS")
                endif()

                if(EXISTS "${_xcfw_inner}/Versions/A/Headers")
                    get_filename_component(_xcfw_inner_parent "${_xcfw_inner}" DIRECTORY)
                    set(FLUTTER_ENGINE_FRAMEWORK_DIR "${_xcfw_inner_parent}")
                    set(FLUTTER_ENGINE_INCLUDE_DIR   "${_xcfw_inner}/Versions/A/Headers")
                elseif(EXISTS "${_xcfw_inner}/Headers")
                    get_filename_component(_xcfw_inner_parent "${_xcfw_inner}" DIRECTORY)
                    set(FLUTTER_ENGINE_FRAMEWORK_DIR "${_xcfw_inner_parent}")
                    set(FLUTTER_ENGINE_INCLUDE_DIR   "${_xcfw_inner}/Headers")
                endif()

                set(FLUTTER_ENGINE_XCFRAMEWORK_PATH "${_xcfw_path}")
            endif()
        endif()

        # Step B: 回退到旧式 FlutterMacOS.framework（非 xcframework，不缓存）
        if(NOT FLUTTER_ENGINE_LIBRARY)
            set(_legacy_fw "")
            foreach(_search_dir IN LISTS FLUTTER_ENGINE_SEARCH_PATHS)
                if(NOT _legacy_fw AND EXISTS "${_search_dir}/FlutterMacOS.framework")
                    set(_legacy_fw "${_search_dir}/FlutterMacOS.framework")
                endif()
            endforeach()
            if(_legacy_fw)
                if(EXISTS "${_legacy_fw}/Versions/A/FlutterMacOS")
                    set(FLUTTER_ENGINE_LIBRARY "${_legacy_fw}/Versions/A/FlutterMacOS")
                elseif(EXISTS "${_legacy_fw}/FlutterMacOS")
                    set(FLUTTER_ENGINE_LIBRARY "${_legacy_fw}/FlutterMacOS")
                endif()
                if(EXISTS "${_legacy_fw}/Headers")
                    set(FLUTTER_ENGINE_INCLUDE_DIR "${_legacy_fw}/Headers")
                endif()
                get_filename_component(_legacy_fw_dir "${_legacy_fw}" DIRECTORY)
                set(FLUTTER_ENGINE_FRAMEWORK_DIR "${_legacy_fw_dir}")
            endif()
        endif()
    endif()

    # Step C: 通用回退：find_library（Linux 等平台，或上面均失败时）
    if(NOT FLUTTER_ENGINE_LIBRARY)
        find_library(FLUTTER_ENGINE_LIBRARY
            NAMES
                ${FLUTTER_ENGINE_LIB_NAME}
                flutter_engine
                FlutterEngine
            PATHS
                ${FLUTTER_ENGINE_SEARCH_PATHS}
            NO_DEFAULT_PATH
            DOC "Flutter Engine 共享库"
        )
    endif()
endif()

# ----------------------------------------------------------------
# 6. 查找 Flutter Engine 头文件
# ----------------------------------------------------------------
find_path(FLUTTER_ENGINE_INCLUDE_DIR
    NAMES
        flutter_embedder.h
        flutter/flutter_view_controller.h
    PATHS
        ${FLUTTER_ENGINE_SEARCH_PATHS}
        "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine"
    PATH_SUFFIXES
        include
        cpp_client_wrapper/include
    DOC "Flutter Engine 头文件目录"
)

if(WIN32)
    find_path(FLUTTER_WINDOWS_INCLUDE_DIR
        NAMES
            flutter_windows.h
        PATHS
            ${FLUTTER_ENGINE_SEARCH_PATHS}
            "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine"
        PATH_SUFFIXES
            windows-x64
            windows-x64-debug
            include
            cpp_client_wrapper/include
        NO_DEFAULT_PATH
        DOC "Flutter Windows 头文件目录"
    )
endif()

# 如果找不到头文件目录，尝试在 SDK 内全局搜索
if(NOT FLUTTER_ENGINE_INCLUDE_DIR)
    file(GLOB_RECURSE FLUTTER_EMBEDDER_H_CANDIDATES
         "${FLUTTER_SDK_DIR}" "flutter_embedder.h")
    if(FLUTTER_EMBEDDER_H_CANDIDATES)
        list(GET FLUTTER_EMBEDDER_H_CANDIDATES 0 _first)
        get_filename_component(FLUTTER_ENGINE_INCLUDE_DIR "${_first}" DIRECTORY)
    endif()
endif()

# ----------------------------------------------------------------
# 7. 创建导入目标
# ----------------------------------------------------------------
if((WIN32 AND FLUTTER_ENGINE_LIBRARY AND FLUTTER_ENGINE_IMPLIB)
    OR (NOT WIN32 AND FLUTTER_ENGINE_LIBRARY))
    set(FLUTTER_ENGINE_FOUND TRUE)
    message(STATUS "[Flutter] Flutter Engine 库: ${FLUTTER_ENGINE_LIBRARY}")
    if(WIN32)
        message(STATUS "[Flutter] Flutter Engine 导入库: ${FLUTTER_ENGINE_IMPLIB}")
    endif()
    message(STATUS "[Flutter] Flutter Engine 头文件: ${FLUTTER_ENGINE_INCLUDE_DIR}")

    if(NOT TARGET FlutterEngine::FlutterEngine)
        # GLOBAL：以 add_subdirectory(JucyFlutter) 方式引入时，本模块在子目录
        # 作用域执行，若不加 GLOBAL 则该 imported target 仅在子目录可见，父工程
        # 的 juce_flutter_add_plugin() 链接阶段找不到它。GLOBAL 使其全局可见。
        add_library(FlutterEngine::FlutterEngine SHARED IMPORTED GLOBAL)

        set_target_properties(FlutterEngine::FlutterEngine PROPERTIES
            IMPORTED_LOCATION "${FLUTTER_ENGINE_LIBRARY}"
        )

        # macOS: 需要额外传递 -framework 链接标志以及 rpath，
        # 并通过 -F/-iframework 使 #include <FlutterMacOS/FlutterMacOS.h> 可解析
        if(APPLE AND FLUTTER_ENGINE_XCFRAMEWORK_PATH)
            set(_fwdir "${FLUTTER_ENGINE_FRAMEWORK_DIR}")
            if(NOT _fwdir)
                # 回退：取 library 路径的 "framework/Versions/A" 上推三级
                get_filename_component(_fwdir "${FLUTTER_ENGINE_LIBRARY}" DIRECTORY) # Versions/A
                get_filename_component(_fwdir "${_fwdir}" DIRECTORY)                 # Versions
                get_filename_component(_fwdir "${_fwdir}" DIRECTORY)                 # FlutterMacOS.framework
                get_filename_component(_fwdir "${_fwdir}" DIRECTORY)                 # macos-arm64_x86_64/
            endif()

            set_property(TARGET FlutterEngine::FlutterEngine APPEND PROPERTY
                INTERFACE_LINK_OPTIONS
                    "-F${_fwdir}"
                    "-framework" "FlutterMacOS"
                    "-Wl,-rpath,${_fwdir}"
            )
            # 使 #include <FlutterMacOS/FlutterMacOS.h> 可解析（-iframework 把目录作为系统框架搜索路径）
            set_property(TARGET FlutterEngine::FlutterEngine APPEND PROPERTY
                INTERFACE_COMPILE_OPTIONS
                    "-F${_fwdir}"
                    "-iframework${_fwdir}"
            )
        endif()

        if(WIN32 AND FLUTTER_ENGINE_IMPLIB)
            set_target_properties(FlutterEngine::FlutterEngine PROPERTIES
                IMPORTED_IMPLIB "${FLUTTER_ENGINE_IMPLIB}"
            )
        endif()

        if(FLUTTER_ENGINE_INCLUDE_DIR)
            target_include_directories(FlutterEngine::FlutterEngine
                INTERFACE "${FLUTTER_ENGINE_INCLUDE_DIR}"
            )
        endif()

        if(WIN32 AND FLUTTER_WINDOWS_INCLUDE_DIR)
            target_include_directories(FlutterEngine::FlutterEngine
                INTERFACE "${FLUTTER_WINDOWS_INCLUDE_DIR}"
            )
        endif()

        # Linux：添加 flutter_linux 头文件目录
        # flutter_linux_gtk 的公共头文件位于 linux-x64/ 目录下
        # 包含方式：#include <flutter_linux/flutter_linux.h>
        if(UNIX AND NOT APPLE)
            # 主头文件搜索路径：SDK 的 linux-x64 工件目录
            set(_linux_engine_dirs
                "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/linux-x64"
                "${FLUTTER_SDK_DIR}/bin/cache/artifacts/engine/linux-x64-debug"
            )
            foreach(_linux_dir IN LISTS _linux_engine_dirs)
                # 头文件通常在 linux-x64/flutter_linux/ 子目录
                if(EXISTS "${_linux_dir}/flutter_linux")
                    target_include_directories(FlutterEngine::FlutterEngine
                        INTERFACE "${_linux_dir}"
                    )
                    message(STATUS "[Flutter] Linux flutter_linux 头文件目录: ${_linux_dir}")
                    break()
                endif()
            endforeach()

            # cpp_client_wrapper 头文件（如有）
            foreach(_linux_dir IN LISTS _linux_engine_dirs)
                if(EXISTS "${_linux_dir}/cpp_client_wrapper/include")
                    target_include_directories(FlutterEngine::FlutterEngine
                        INTERFACE "${_linux_dir}/cpp_client_wrapper/include"
                    )
                    break()
                endif()
            endforeach()
        endif()
    endif()

else()
    message(WARNING "[Flutter] 未找到 Flutter Engine 库 (${FLUTTER_ENGINE_LIB_NAME})。"
                    "已搜索路径: ${FLUTTER_ENGINE_SEARCH_PATHS}")
    message(WARNING "[Flutter] 将使用 JUCE 原生 UI 作为回退实现。")
endif()

# ----------------------------------------------------------------
# 8. 导出 Flutter 版本信息
# ----------------------------------------------------------------
execute_process(
    COMMAND "${FLUTTER_EXECUTABLE}" --version
    OUTPUT_VARIABLE FLUTTER_VERSION_STRING
    ERROR_QUIET
    OUTPUT_STRIP_TRAILING_WHITESPACE
)
string(REGEX MATCH "Flutter ([0-9]+\\.[0-9]+\\.[0-9]+)" _ "${FLUTTER_VERSION_STRING}")
set(FLUTTER_VERSION "${CMAKE_MATCH_1}" CACHE STRING "Flutter 版本" FORCE)
message(STATUS "[Flutter] Flutter 版本: ${FLUTTER_VERSION}")

# ----------------------------------------------------------------
# 9. 跨作用域导出（供 add_subdirectory(JucyFlutter) 方式使用）
# ----------------------------------------------------------------
# 以上多数 set() 未带 CACHE，属子目录局部变量。当本模块经 add_subdirectory
# 在子目录作用域执行时，父工程调用 juce_flutter_add_plugin() 需要这些值，
# 故统一以 CACHE INTERNAL 导出（INTERNAL 不出现在 GUI，语义等同全局普通变量）。
# 已带 CACHE 的变量（FLUTTER_SDK_DIR / FLUTTER_ENGINE_LIB_NAME / FLUTTER_VERSION 等）无需重复导出。
set(FLUTTER_ENGINE_FOUND        "${FLUTTER_ENGINE_FOUND}"        CACHE INTERNAL "Flutter Engine 是否找到")
set(FLUTTER_ENGINE_LIBRARY      "${FLUTTER_ENGINE_LIBRARY}"      CACHE INTERNAL "Flutter Engine 库路径")
set(FLUTTER_ENGINE_IMPLIB       "${FLUTTER_ENGINE_IMPLIB}"       CACHE INTERNAL "Flutter Engine 导入库(Win)")
set(FLUTTER_ENGINE_INCLUDE_DIR  "${FLUTTER_ENGINE_INCLUDE_DIR}"  CACHE INTERNAL "Flutter Engine 头文件目录")
set(FLUTTER_ENGINE_LIB_FILE     "${FLUTTER_ENGINE_LIB_FILE}"     CACHE INTERNAL "Flutter Engine 库文件名")
set(FLUTTER_ENGINE_RELEASE      "${FLUTTER_ENGINE_RELEASE}"      CACHE INTERNAL "是否使用 Release/Profile AOT 引擎")
set(FLUTTER_BUILD_MODE          "${FLUTTER_BUILD_MODE}"          CACHE INTERNAL "Flutter 构建模式")