# cmake/FlutterAOTBuild.cmake
# cmake -P 脚本：在 BUILD 目录中执行 Flutter AOT 编译，不污染 git 仓库。
#
# 调用方式（由 CMakeLists.txt 中的 add_custom_target 调用）：
#   cmake -DFLUTTER_EXECUTABLE=<flutter>
#         -DSOURCE_DIR=<flutter_ui 绝对路径>
#         -DAOT_DIR=<build/_flutter_aot_runner 绝对路径>
#         -DPLATFORM=<windows|macos|linux>
#         -DBUILD_MODE=<release|profile>    # 默认 release
#         -DARTIFACTS_DIR=<cmake binary dir>
#         -P cmake/FlutterAOTBuild.cmake
cmake_minimum_required(VERSION 3.22)

if(NOT BUILD_MODE)
    set(BUILD_MODE "release")
endif()

message(STATUS "[Flutter AOT] 平台=${PLATFORM}  模式=${BUILD_MODE}")
message(STATUS "[Flutter AOT] 源码=${SOURCE_DIR}")
message(STATUS "[Flutter AOT] 构建目录=${AOT_DIR}")

# ---------------------------------------------------------------
# 辅助：Windows 的 execute_process 无法直接运行 .bat/.cmd 文件
# （CreateProcess 不处理 batch 文件），必须通过 cmd.exe /C 调用。
# 其他平台直接调用 flutter 可执行文件。
# ---------------------------------------------------------------
if(WIN32)
    set(_fl cmd.exe /C "${FLUTTER_EXECUTABLE}")
else()
    set(_fl "${FLUTTER_EXECUTABLE}")
endif()

# ---------------------------------------------------------------
# 1. 同步 Dart 源码到 AOT 构建目录（始终同步，捕获增量修改）
# ---------------------------------------------------------------
file(MAKE_DIRECTORY "${AOT_DIR}")

foreach(_item lib assets packages pubspec.yaml pubspec.lock devtools_options.yaml analysis_options.yaml)
    if(EXISTS "${SOURCE_DIR}/${_item}")
        file(COPY "${SOURCE_DIR}/${_item}" DESTINATION "${AOT_DIR}")
    endif()
endforeach()

# ---------------------------------------------------------------
# 1.1 同步 pubspec.yaml 中声明的 path 依赖目录
#
# 仅拷贝 SOURCE_DIR 内文件无法覆盖 "../vendor/..." 这类外部路径依赖。
# 这里解析所有 path: <relative-path> 条目，并将对应目录镜像到 AOT_DIR
# 的同一相对位置，确保 flutter pub get 在 runner 目录也能解依赖。
# ---------------------------------------------------------------
file(READ "${SOURCE_DIR}/pubspec.yaml" _pubspec_text)
string(REGEX MATCHALL "[\n\r][ \t]*path:[ \t]*[^\n\r]+" _path_dep_lines "${_pubspec_text}")
foreach(_line IN LISTS _path_dep_lines)
    string(REGEX REPLACE ".*path:[ \t]*" "" _dep_rel "${_line}")
    string(STRIP "${_dep_rel}" _dep_rel)
    string(REGEX REPLACE "^['\"]|['\"]$" "" _dep_rel "${_dep_rel}")

    # 只处理相对路径依赖（绝对路径依赖直接交给 flutter 处理）
    if(NOT IS_ABSOLUTE "${_dep_rel}")
        get_filename_component(_dep_src_abs "${SOURCE_DIR}/${_dep_rel}" ABSOLUTE)
        if(EXISTS "${_dep_src_abs}")
            get_filename_component(_dep_dst_abs "${AOT_DIR}/${_dep_rel}" ABSOLUTE)
            get_filename_component(_dep_dst_parent "${_dep_dst_abs}" DIRECTORY)
            file(MAKE_DIRECTORY "${_dep_dst_parent}")
            file(COPY "${_dep_src_abs}" DESTINATION "${_dep_dst_parent}")
            message(STATUS "[Flutter AOT] 同步 path 依赖: ${_dep_rel}")
        else()
            message(WARNING "[Flutter AOT] path 依赖目录不存在，跳过: ${_dep_rel}")
        endif()
    endif()
endforeach()

# ---------------------------------------------------------------
# 2. 首次运行：用 flutter create 在 AOT_DIR 内添加平台 runner
#    （生成 windows/ / macos/ / linux/ 目录，不影响源码仓库）
# ---------------------------------------------------------------
if(PLATFORM STREQUAL "windows")
    set(_runner_sentinel "${AOT_DIR}/windows/CMakeLists.txt")
    set(_flutter_platform "windows")
elseif(PLATFORM STREQUAL "macos")
    set(_runner_sentinel "${AOT_DIR}/macos/Runner.xcodeproj")
    set(_flutter_platform "macos")
else()
    set(_runner_sentinel "${AOT_DIR}/linux/CMakeLists.txt")
    set(_flutter_platform "linux")
endif()

if(NOT EXISTS "${_runner_sentinel}")
    # 读取 pubspec.yaml 中的项目名，传给 flutter create 以避免项目名冲突
    file(READ "${AOT_DIR}/pubspec.yaml" _pubspec)
    string(REGEX MATCH "name: *([^\n\r]+)" _m "${_pubspec}")
    if(CMAKE_MATCH_1)
        string(STRIP "${CMAKE_MATCH_1}" _project_name)
    else()
        set(_project_name "flutter_app")
    endif()

    message(STATUS "[Flutter AOT] 初始化 ${_flutter_platform} runner（flutter create --project-name ${_project_name}）...")
    # 若 runner 目录残留（例如上次构建中断），先删除再重建，避免交互式覆盖提示
    if(EXISTS "${AOT_DIR}/${_flutter_platform}")
        file(REMOVE_RECURSE "${AOT_DIR}/${_flutter_platform}")
    endif()
    execute_process(
        COMMAND ${_fl} create
                --platforms=${_flutter_platform}
                --project-name "${_project_name}"
                .
        WORKING_DIRECTORY "${AOT_DIR}"
        RESULT_VARIABLE _create_result
        OUTPUT_VARIABLE _create_out
        ERROR_VARIABLE  _create_err
    )
    if(NOT _create_result EQUAL 0)
        message(FATAL_ERROR
            "[Flutter AOT] flutter create 失败（exit=${_create_result}）：\n"
            "  stdout: ${_create_out}\n"
            "  stderr: ${_create_err}")
    endif()
    # flutter create 可能覆盖 pubspec.yaml，用原始版本还原
    file(COPY "${SOURCE_DIR}/pubspec.yaml" DESTINATION "${AOT_DIR}")
    if(EXISTS "${SOURCE_DIR}/pubspec.lock")
        file(COPY "${SOURCE_DIR}/pubspec.lock" DESTINATION "${AOT_DIR}")
    endif()
endif()

# ---------------------------------------------------------------
# 3. flutter pub get
# ---------------------------------------------------------------
execute_process(
    COMMAND ${_fl} pub get
    WORKING_DIRECTORY "${AOT_DIR}"
    RESULT_VARIABLE _pub_result
    OUTPUT_VARIABLE _pub_out
    ERROR_VARIABLE  _pub_err
)
if(NOT _pub_result EQUAL 0)
    message(FATAL_ERROR
        "[Flutter AOT] flutter pub get 失败（exit=${_pub_result}）：\n"
        "  stdout: ${_pub_out}\n"
        "  stderr: ${_pub_err}")
endif()

# ---------------------------------------------------------------
# 4. flutter build <platform> --<mode>
# ---------------------------------------------------------------
message(STATUS "[Flutter AOT] flutter build ${_flutter_platform} --${BUILD_MODE} ...")
execute_process(
    COMMAND ${_fl} build ${_flutter_platform} --${BUILD_MODE}
    WORKING_DIRECTORY "${AOT_DIR}"
    RESULT_VARIABLE _build_result
    OUTPUT_VARIABLE _build_out
    ERROR_VARIABLE  _build_err
)
if(NOT _build_result EQUAL 0)
    message(FATAL_ERROR
        "[Flutter AOT] flutter build ${_flutter_platform} --${BUILD_MODE} 失败：\n${_build_err}\n${_build_out}")
endif()

# ---------------------------------------------------------------
# 5. 将 AOT 产物复制到 ARTIFACTS_DIR 暂存区
# ---------------------------------------------------------------
# 小写模式名 -> Flutter 输出目录的首字母大写（Release / Profile）
string(SUBSTRING "${BUILD_MODE}" 0 1 _mode_first)
string(TOUPPER "${_mode_first}" _mode_first_upper)
string(SUBSTRING "${BUILD_MODE}" 1 -1 _mode_rest)
set(_mode_capitalized "${_mode_first_upper}${_mode_rest}")  # Release 或 Profile

if(PLATFORM STREQUAL "windows")
    # Flutter 3.22+ 将 AOT 快照从 app.dll 改名为 app.so，并放在 data/ 子目录中
    # 标准路径：build/windows/x64/runner/<Mode>/data/app.so
    set(_out_default "${AOT_DIR}/build/windows/x64/runner/${_mode_capitalized}/data")

    if(EXISTS "${_out_default}/app.so")
        set(_aot_data_dir "${_out_default}")
    else()
        # 递归搜索（兼容不同 Flutter 版本输出路径）
        file(GLOB_RECURSE _app_so_hits
            "${AOT_DIR}/build/windows/app.so"
            "${AOT_DIR}/build/windows/*/app.so"
            "${AOT_DIR}/build/windows/*/*/app.so"
            "${AOT_DIR}/build/windows/*/*/*/app.so"
            "${AOT_DIR}/build/windows/*/*/*/*/app.so"
            "${AOT_DIR}/build/windows/*/*/*/*/*/app.so"
        )
        # 优先取路径中包含 runner 的（排除中间编译产物）
        set(_aot_data_dir "")
        foreach(_candidate ${_app_so_hits})
            if(_candidate MATCHES "runner")
                get_filename_component(_aot_data_dir "${_candidate}" DIRECTORY)
                message(STATUS "[Flutter AOT] app.so 非标准路径：${_candidate}")
                break()
            endif()
        endforeach()
        if(NOT _aot_data_dir AND _app_so_hits)
            list(GET _app_so_hits 0 _first_hit)
            get_filename_component(_aot_data_dir "${_first_hit}" DIRECTORY)
            message(STATUS "[Flutter AOT] app.so（备用路径）：${_first_hit}")
        endif()
        if(NOT _aot_data_dir)
            # 诊断：列出 .so / .dll 文件供排查
            file(GLOB_RECURSE _diag_files
                "${AOT_DIR}/build/windows/*.so"
                "${AOT_DIR}/build/windows/*/*.so"
                "${AOT_DIR}/build/windows/*/*/*.so"
                "${AOT_DIR}/build/windows/*/*/*/*.so"
                "${AOT_DIR}/build/windows/*/*/*/*/*.so"
                "${AOT_DIR}/build/windows/*.dll"
                "${AOT_DIR}/build/windows/*/*.dll"
                "${AOT_DIR}/build/windows/*/*/*.dll"
                "${AOT_DIR}/build/windows/*/*/*/*.dll"
                "${AOT_DIR}/build/windows/*/*/*/*/*.dll"
            )
            message(STATUS "[Flutter AOT] 构建树中的 AOT 产物（.so / .dll）：")
            foreach(_d ${_diag_files})
                message(STATUS "  ${_d}")
            endforeach()
            message(FATAL_ERROR
                "[Flutter AOT] 未找到 app.so（Flutter 3.22+）也未找到 app.dll\n"
                "  期望路径: ${_out_default}/app.so\n"
                "  请查看上方诊断输出")
        endif()
    endif()

    # app.so 与 flutter_assets 位于同一目录（data/）
    file(COPY "${_aot_data_dir}/app.so" DESTINATION "${ARTIFACTS_DIR}")
    if(EXISTS "${_aot_data_dir}/flutter_assets")
        file(COPY "${_aot_data_dir}/flutter_assets" DESTINATION "${ARTIFACTS_DIR}")
    else()
        message(WARNING "[Flutter AOT] 未在 ${_aot_data_dir} 找到 flutter_assets")
    endif()
    message(STATUS "[Flutter AOT] Windows: app.so + flutter_assets → ${ARTIFACTS_DIR}")

elseif(PLATFORM STREQUAL "macos")
    file(GLOB _app_bundles "${AOT_DIR}/build/macos/Build/Products/${_mode_capitalized}/*.app")
    if(NOT _app_bundles)
        message(FATAL_ERROR "[Flutter AOT] 未找到 macOS ${_mode_capitalized} .app bundle")
    endif()
    list(GET _app_bundles 0 _app_bundle)
    set(_app_fw "${_app_bundle}/Contents/Frameworks/App.framework")
    if(NOT EXISTS "${_app_fw}")
        message(FATAL_ERROR "[Flutter AOT] 未找到 App.framework：${_app_fw}")
    endif()
    file(COPY "${_app_fw}" DESTINATION "${ARTIFACTS_DIR}")
    message(STATUS "[Flutter AOT] macOS: App.framework → ${ARTIFACTS_DIR}")

    # 同时提取 flutter_assets 到构建目录，供 Profile/Debug 模式的分发步骤使用
    set(_flutter_assets_in_fw "${_app_fw}/Versions/A/Resources/flutter_assets")
    if(EXISTS "${_flutter_assets_in_fw}")
        file(COPY "${_flutter_assets_in_fw}" DESTINATION "${ARTIFACTS_DIR}")
        message(STATUS "[Flutter AOT] macOS: flutter_assets → ${ARTIFACTS_DIR}")
    endif()

else() # linux
    string(TOLOWER "${BUILD_MODE}" _mode_lower)
    set(_out "${AOT_DIR}/build/linux/x64/${_mode_lower}/bundle")
    file(MAKE_DIRECTORY "${ARTIFACTS_DIR}/lib")
    file(COPY "${_out}/lib/libapp.so"       DESTINATION "${ARTIFACTS_DIR}/lib")
    file(COPY "${_out}/data/flutter_assets" DESTINATION "${ARTIFACTS_DIR}")
    message(STATUS "[Flutter AOT] Linux: libapp.so + flutter_assets → ${ARTIFACTS_DIR}")
endif()

message(STATUS "[Flutter AOT] 完成")
