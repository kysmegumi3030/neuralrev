# ============================================================
# FetchJUCE.cmake
# 使用 CMake FetchContent 自动下载 JUCE8
# ============================================================

include(FetchContent)

# JUCE8 发布版本标签（可修改至最新版本）
set(JUCE_VERSION_TAG "8.0.4" CACHE STRING "JUCE 版本标签")

# ------------------------------------------------------------
# 本地 JUCE 源码树复用（可选，避免每个新工程都重新 clone 一遍 JUCE）
# ------------------------------------------------------------
# 传 -DJUCE_LOCAL_SOURCE_DIR=<path> 即用本地目录，不联网。
# 未传时自动探测同机上已有的 JUCE 树（本机首次 clone 需数分钟，
# 而 JUCE 是逐版本固定的，复用同一份完全等价）。
# 探测到的目录必须版本号匹配 JUCE_VERSION_TAG，否则忽略并回退到下载。
if(NOT JUCE_LOCAL_SOURCE_DIR)
    foreach(_cand IN ITEMS
        "$ENV{HOME}/Documents/coding/JucyPWA1/build/_deps/juce-src"
        "$ENV{HOME}/Documents/coding/JucyFlutter/build/_deps/juce-src")
        if(EXISTS "${_cand}/CMakeLists.txt" AND IS_DIRECTORY "${_cand}/modules")
            file(STRINGS "${_cand}/CMakeLists.txt" _ver REGEX "^project\\(JUCE VERSION")
            if(_ver MATCHES "${JUCE_VERSION_TAG}")
                set(JUCE_LOCAL_SOURCE_DIR "${_cand}")
                break()
            endif()
        endif()
    endforeach()
endif()

if(JUCE_LOCAL_SOURCE_DIR AND EXISTS "${JUCE_LOCAL_SOURCE_DIR}/CMakeLists.txt")
    message(STATUS "[JUCE] 复用本地 JUCE ${JUCE_VERSION_TAG}: ${JUCE_LOCAL_SOURCE_DIR}")
    FetchContent_Declare(JUCE SOURCE_DIR "${JUCE_LOCAL_SOURCE_DIR}")
else()
    message(STATUS "[JUCE] 正在获取 JUCE ${JUCE_VERSION_TAG}...")
    FetchContent_Declare(
        JUCE
        GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
        GIT_TAG        ${JUCE_VERSION_TAG}
        GIT_SHALLOW    TRUE   # 只拉取最新提交，减少下载量
        GIT_PROGRESS   TRUE
    )
endif()

# 关闭 JUCE 自带的示例和测试，加快配置速度
set(JUCE_BUILD_EXTRAS   OFF CACHE BOOL "" FORCE)
set(JUCE_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)

FetchContent_MakeAvailable(JUCE)

# JUCE 8.0.4 在单配置生成器 + RelWithDebInfo 下，juceaide 导出目标只带 Debug
# 位置，后续查询特定配置的 IMPORTED_LOCATION 时会产生噪音。
foreach(_juceaide_target IN ITEMS juce_tools::juceaide juce::juceaide)
    if(TARGET ${_juceaide_target})
        get_target_property(_juceaide_aliased_target ${_juceaide_target} ALIASED_TARGET)
        if(_juceaide_aliased_target)
            set(_juceaide_real_target ${_juceaide_aliased_target})
        else()
            set(_juceaide_real_target ${_juceaide_target})
        endif()

        get_target_property(_juceaide_debug_location ${_juceaide_real_target} IMPORTED_LOCATION_DEBUG)
        get_target_property(_juceaide_generic_location ${_juceaide_real_target} IMPORTED_LOCATION)

        if(NOT _juceaide_debug_location AND _juceaide_generic_location)
            set(_juceaide_debug_location "${_juceaide_generic_location}")
        endif()

        if(_juceaide_debug_location)
            set_target_properties(${_juceaide_real_target} PROPERTIES
                IMPORTED_LOCATION_DEBUG "${_juceaide_debug_location}"
                IMPORTED_LOCATION_RELEASE "${_juceaide_debug_location}"
                IMPORTED_LOCATION_RELWITHDEBINFO "${_juceaide_debug_location}"
                IMPORTED_LOCATION_MINSIZEREL "${_juceaide_debug_location}"
                MAP_IMPORTED_CONFIG_RELEASE Debug
                MAP_IMPORTED_CONFIG_RELWITHDEBINFO Debug
                MAP_IMPORTED_CONFIG_MINSIZEREL Debug
            )
        endif()
    endif()
endforeach()

# 获取 JUCE 版本信息
if(EXISTS "${juce_SOURCE_DIR}/CMakeLists.txt")
    file(STRINGS "${juce_SOURCE_DIR}/CMakeLists.txt" JUCE_VERSION_LINE
         REGEX "^project\\(JUCE VERSION")
    if(JUCE_VERSION_LINE)
        string(REGEX MATCH "[0-9]+\\.[0-9]+\\.[0-9]+" JUCE_VERSION "${JUCE_VERSION_LINE}")
    else()
        set(JUCE_VERSION "${JUCE_VERSION_TAG}")
    endif()
else()
    set(JUCE_VERSION "${JUCE_VERSION_TAG}")
endif()

message(STATUS "[JUCE] JUCE ${JUCE_VERSION} 已就绪，源码位于: ${juce_SOURCE_DIR}")