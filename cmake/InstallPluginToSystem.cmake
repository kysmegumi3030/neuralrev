# Usage:
#   cmake -DSOURCE=<path> -DDESTINATION=<path> -DNAME=<label> -DALLOW_FAILURE=ON -P InstallPluginToSystem.cmake
#
# ALLOW_FAILURE:
#   - ON : 任何错误仅输出 WARNING 并返回，不中断构建。
#   - OFF: 错误使用 FATAL_ERROR（默认）。

if(NOT DEFINED ALLOW_FAILURE)
    set(ALLOW_FAILURE OFF)
endif()

function(_install_plugin_emit_error MSG)
    if(ALLOW_FAILURE)
        message(WARNING "InstallPluginToSystem.cmake: ${MSG}")
        return()
    endif()

    message(FATAL_ERROR "InstallPluginToSystem.cmake: ${MSG}")
endfunction()

if(NOT DEFINED SOURCE OR SOURCE STREQUAL "")
    _install_plugin_emit_error("SOURCE is required")
    return()
endif()

if(NOT DEFINED DESTINATION OR DESTINATION STREQUAL "")
    _install_plugin_emit_error("DESTINATION is required")
    return()
endif()

if(NOT DEFINED NAME OR NAME STREQUAL "")
    set(NAME "plugin")
endif()

if(NOT EXISTS "${SOURCE}")
    _install_plugin_emit_error("source not found: ${SOURCE}")
    return()
endif()

execute_process(
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${DESTINATION}"
    RESULT_VARIABLE _mk_res
    ERROR_VARIABLE _mk_err
)
if(NOT _mk_res EQUAL 0)
    string(STRIP "${_mk_err}" _mk_err)
    _install_plugin_emit_error("cannot create destination '${DESTINATION}'. ${_mk_err}")
    return()
endif()

get_filename_component(_src_name "${SOURCE}" NAME)
set(_dst_path "${DESTINATION}/${_src_name}")

if(IS_DIRECTORY "${SOURCE}")
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -E rm -rf "${_dst_path}"
        RESULT_VARIABLE _rm_res
        ERROR_VARIABLE _rm_err
    )
    if(NOT _rm_res EQUAL 0)
        string(STRIP "${_rm_err}" _rm_err)
        _install_plugin_emit_error("cannot remove old bundle '${_dst_path}'. ${_rm_err}")
        if(ALLOW_FAILURE)
            return()
        endif()
    endif()

    # macOS：用 ditto 而非 cmake -E copy_directory。后者会「解引用」符号链接，
    # 把 .framework 内 Versions/Current 及顶层软链变成真实副本 → 同一 FlutterMacOS
    # 二进制出现两份 → dyld 双重加载 → ObjC 类重复注册 → 宿主随机闪退。
    # ditto 是 Apple 规范的 bundle 拷贝工具，完整保留符号链接结构。
    if(APPLE)
        execute_process(
            COMMAND /usr/bin/ditto "${SOURCE}" "${_dst_path}"
            RESULT_VARIABLE _cp_res
            ERROR_VARIABLE _cp_err
        )
    else()
        execute_process(
            COMMAND "${CMAKE_COMMAND}" -E copy_directory "${SOURCE}" "${_dst_path}"
            RESULT_VARIABLE _cp_res
            ERROR_VARIABLE _cp_err
        )
    endif()
    if(NOT _cp_res EQUAL 0)
        string(STRIP "${_cp_err}" _cp_err)
        _install_plugin_emit_error("copy failed '${SOURCE}' -> '${_dst_path}'. ${_cp_err}")
        return()
    endif()
else()
    execute_process(
        COMMAND "${CMAKE_COMMAND}" -E copy_if_different "${SOURCE}" "${DESTINATION}"
        RESULT_VARIABLE _cp_file_res
        ERROR_VARIABLE _cp_file_err
    )
    if(NOT _cp_file_res EQUAL 0)
        string(STRIP "${_cp_file_err}" _cp_file_err)
        _install_plugin_emit_error("copy failed '${SOURCE}' -> '${DESTINATION}'. ${_cp_file_err}")
        return()
    endif()
endif()

message(STATUS "[InstallSystemPlugins] Installed ${NAME}: ${_dst_path}")
