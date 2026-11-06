cmake_minimum_required(VERSION 3.24)

# Platform detection and defaults
if(NOT DEFINED CMAKE_TARGET_OS)
    if(CMAKE_HOST_UNIX AND NOT CMAKE_HOST_APPLE)
        set(CMAKE_TARGET_OS "linux")
    elseif(CMAKE_HOST_WIN32)
        set(CMAKE_TARGET_OS "windows")
    elseif(CMAKE_HOST_APPLE)
        set(CMAKE_TARGET_OS "macosx")
    endif()
endif()

if(CMAKE_TARGET_OS STREQUAL "macosx" AND (NOT DEFINED CMAKE_OSX_DEPLOYMENT_TARGET OR CMAKE_OSX_DEPLOYMENT_TARGET STREQUAL ""))
    unset(CMAKE_OSX_DEPLOYMENT_TARGET CACHE)
    set(CMAKE_OSX_DEPLOYMENT_TARGET "12.1" CACHE STRING "Minimum macOS deployment target")
endif()

project(osmand_render_helper_native LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(OSMAND_VENDOR_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../vendor/osmand" CACHE PATH "Directory that contains the copied OsmAnd 'core' and 'core-legacy' source trees")

# Set platform-specific defaults
if(CMAKE_TARGET_OS STREQUAL "linux")
    set(QT_ROOT_DEFAULT "$ENV{HOME}/.local/Qt/6.10.1/gcc_64")
    set(MINGW_ROOT_DEFAULT "")
    set(DIST_DIR_DEFAULT "${CMAKE_CURRENT_SOURCE_DIR}/dist-linux")
elseif(CMAKE_TARGET_OS STREQUAL "windows")
    set(QT_ROOT_DEFAULT "C:/Qt/6.10.1/mingw_64")
    set(MINGW_ROOT_DEFAULT "C:/Qt/Tools/mingw1310_64")
    set(DIST_DIR_DEFAULT "${CMAKE_CURRENT_SOURCE_DIR}/dist")
elseif(CMAKE_TARGET_OS STREQUAL "macosx")
    set(QT_ROOT_DEFAULT "$ENV{HOME}/Qt/6.10.1/macos")
    set(MINGW_ROOT_DEFAULT "")
    set(DIST_DIR_DEFAULT "${CMAKE_CURRENT_SOURCE_DIR}/dist-macosx")
endif()

set(QT_ROOT "${QT_ROOT_DEFAULT}" CACHE PATH "Qt 6 SDK root used to build the helper")
set(MINGW_ROOT "${MINGW_ROOT_DEFAULT}" CACHE PATH "MinGW toolchain root used to build the helper (Windows only)")
set(DIST_DIR "${DIST_DIR_DEFAULT}" CACHE PATH "Output directory for built binaries")

if(NOT EXISTS "${OSMAND_VENDOR_ROOT}/core/CMakeLists.txt")
    message(FATAL_ERROR "Missing '${OSMAND_VENDOR_ROOT}/core/CMakeLists.txt'. Copy the vendored OsmAnd sources into '../../vendor/osmand'.")
endif()
if(NOT EXISTS "${OSMAND_VENDOR_ROOT}/core-legacy/CMakeLists.txt")
    message(FATAL_ERROR "Missing '${OSMAND_VENDOR_ROOT}/core-legacy/CMakeLists.txt'. Copy the vendored OsmAnd sources into '../../vendor/osmand'.")
endif()

list(PREPEND CMAKE_PREFIX_PATH "${QT_ROOT}")
set(Qt5Core_DIR "${CMAKE_CURRENT_SOURCE_DIR}/cmake/Qt5Core" CACHE PATH "Qt5Core compatibility shim")
set(Qt5Network_DIR "${CMAKE_CURRENT_SOURCE_DIR}/cmake/Qt5Network" CACHE PATH "Qt5Network compatibility shim")

find_package(Qt6 REQUIRED COMPONENTS Core Network Gui Widgets OpenGL OpenGLWidgets)

set(OSMAND_ROOT "${OSMAND_VENDOR_ROOT}" CACHE PATH "Synthetic OsmAnd root that exposes core and core-legacy side by side")
set(OSMAND_OWN_SHIPPED_QT OFF CACHE BOOL "Use system Qt instead of OsmAnd-shipped Qt")

# Platform-specific OsmAnd build settings
if(CMAKE_TARGET_OS STREQUAL "linux")
    set(CMAKE_TARGET_CPU_ARCH "amd64" CACHE STRING "OsmAnd CPU architecture")
    set(CMAKE_TARGET_CPU_ARCH_FAMILY "x86" CACHE STRING "OsmAnd CPU architecture family")
    set(CMAKE_TARGET_CPU_NAME "amd64" CACHE STRING "OsmAnd CPU name")
    set(CMAKE_SHARED_LIBS_ALLOWED_ON_TARGET ON CACHE BOOL "Build shared libraries when supported")
    set(CMAKE_STATIC_LIBS_ALLOWED_ON_TARGET ON CACHE BOOL "Build static libraries when supported")
    if(NOT DEFINED CMAKE_COMPILER_FAMILY)
        if(CMAKE_CXX_COMPILER_ID STREQUAL "Clang" OR CMAKE_CXX_COMPILER_ID STREQUAL "AppleClang")
            set(OSMAND_COMPILER_FAMILY "clang")
        elseif(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
            set(OSMAND_COMPILER_FAMILY "gcc")
        else()
            set(OSMAND_COMPILER_FAMILY "${CMAKE_CXX_COMPILER_ID}")
        endif()
        set(CMAKE_COMPILER_FAMILY "${OSMAND_COMPILER_FAMILY}" CACHE STRING "OsmAnd compiler family")
    endif()

    # Force -fPIC for all code on Linux (required for shared libraries)
    set(CMAKE_POSITION_INDEPENDENT_CODE ON CACHE BOOL "Compile with -fPIC" FORCE)
elseif(CMAKE_TARGET_OS STREQUAL "windows")
    set(CMAKE_TARGET_CPU_ARCH "amd64" CACHE STRING "OsmAnd CPU architecture")
    set(CMAKE_TARGET_CPU_ARCH_FAMILY "x86" CACHE STRING "OsmAnd CPU architecture family")
    set(CMAKE_TARGET_CPU_NAME "amd64" CACHE STRING "OsmAnd CPU name")
    set(CMAKE_SHARED_LIBS_ALLOWED_ON_TARGET ON CACHE BOOL "Build shared libraries when supported" FORCE)
    set(CMAKE_STATIC_LIBS_ALLOWED_ON_TARGET ON CACHE BOOL "Build static libraries when supported")
    set(CMAKE_COMPILER_FAMILY "gcc" CACHE STRING "OsmAnd compiler family")
    set(ws2_32_LIBRARY ws2_32 CACHE STRING "Winsock library used by MinGW builds")
elseif(CMAKE_TARGET_OS STREQUAL "macosx")
    if(NOT DEFINED CMAKE_TARGET_CPU_ARCH)
        if(CMAKE_OSX_ARCHITECTURES)
            list(GET CMAKE_OSX_ARCHITECTURES 0 OSMAND_MACOS_ARCH)
        elseif(CMAKE_SYSTEM_PROCESSOR STREQUAL "arm64")
            set(OSMAND_MACOS_ARCH "arm64")
        else()
            set(OSMAND_MACOS_ARCH "x86_64")
        endif()
        set(CMAKE_TARGET_CPU_ARCH "${OSMAND_MACOS_ARCH}" CACHE STRING "OsmAnd CPU architecture")
    endif()

    if(CMAKE_TARGET_CPU_ARCH STREQUAL "arm64")
        set(CMAKE_TARGET_CPU_ARCH_FAMILY "arm64" CACHE STRING "OsmAnd CPU architecture family")
        set(CMAKE_TARGET_CPU_NAME "arm64" CACHE STRING "OsmAnd CPU name")
    else()
        set(CMAKE_TARGET_CPU_ARCH_FAMILY "x86" CACHE STRING "OsmAnd CPU architecture family")
        set(CMAKE_TARGET_CPU_NAME "x86_64" CACHE STRING "OsmAnd CPU name")
    endif()

    set(CMAKE_SHARED_LIBS_ALLOWED_ON_TARGET ON CACHE BOOL "Build shared libraries when supported")
    set(CMAKE_STATIC_LIBS_ALLOWED_ON_TARGET ON CACHE BOOL "Build static libraries when supported")
    set(CMAKE_COMPILER_FAMILY "clang" CACHE STRING "OsmAnd compiler family")
    if(NOT DEFINED CMAKE_OSX_ARCHITECTURES OR CMAKE_OSX_ARCHITECTURES STREQUAL "")
        unset(CMAKE_OSX_ARCHITECTURES CACHE)
        set(CMAKE_OSX_ARCHITECTURES "${CMAKE_TARGET_CPU_ARCH}" CACHE STRING "macOS architectures")
    endif()
    set(CMAKE_MACOSX_RPATH ON)
    set(CMAKE_POSITION_INDEPENDENT_CODE ON CACHE BOOL "Compile with -fPIC" FORCE)
    add_compile_definitions(GL_SILENCE_DEPRECATION)
endif()

string(REGEX REPLACE "[^A-Za-z0-9_]" "_" purified_CMAKE_TARGET_OS "${CMAKE_TARGET_OS}")
string(REGEX REPLACE "[^A-Za-z0-9_]" "_" purified_CMAKE_TARGET_CPU_ARCH "${CMAKE_TARGET_CPU_ARCH}")
string(REGEX REPLACE "[^A-Za-z0-9_]" "_" purified_CMAKE_TARGET_CPU_ARCH_FAMILY "${CMAKE_TARGET_CPU_ARCH_FAMILY}")
string(REGEX REPLACE "[^A-Za-z0-9_]" "_" purified_CMAKE_COMPILER_FAMILY "${CMAKE_COMPILER_FAMILY}")
add_compile_definitions(
    OSMAND_TARGET_OS_${purified_CMAKE_TARGET_OS}
    OSMAND_TARGET_CPU_ARCH_${purified_CMAKE_TARGET_CPU_ARCH}
    OSMAND_TARGET_CPU_ARCH_FAMILY_${purified_CMAKE_TARGET_CPU_ARCH_FAMILY}
    OSMAND_COMPILER_FAMILY_${purified_CMAKE_COMPILER_FAMILY}
)

# Include OsmAnd build utilities (required for prepare_upstream macro)
include("${OSMAND_VENDOR_ROOT}/build/utils/common.cmake")

# Skip embedded Qt build - we use system/PySide6 Qt instead
set(OSMAND_SYSTEM_QT ON CACHE BOOL "Use system Qt instead of OsmAnd-shipped Qt")
set(ENV{OSMAND_SYSTEM_QT} "1")

# Include Qt configuration (will skip embedded Qt due to OSMAND_SYSTEM_QT)
include("${OSMAND_VENDOR_ROOT}/build/qt.cmake")

include("${OSMAND_VENDOR_ROOT}/core/core.cmake")

set(OSMAND_COMMON_SOURCES
    src/file_system_core_resources_provider.cpp
    src/file_system_core_resources_provider.h
    src/osmand_core_runtime.cpp
    src/osmand_core_runtime.h
)

add_executable(osmand_render_helper
    ${OSMAND_COMMON_SOURCES}
    src/main.cpp
)

add_library(osmand_native_widget SHARED
    ${OSMAND_COMMON_SOURCES}
    src/osmand_native_map_widget.cpp
    src/osmand_native_map_widget.h
    src/osmand_search_service.cpp
    src/osmand_search_service.h
    src/osmand_native_widget_bridge.cpp
)

target_link_libraries(osmand_render_helper
    PRIVATE
        OsmAndCoreTools_static
        Qt6::Core
        Qt6::Network
        Qt6::Gui
        Qt6::Widgets
        dwrite
)

target_link_options(osmand_render_helper
    PRIVATE
        "-Wl,-u,DWriteCreateFactory"
)

target_link_libraries(osmand_native_widget
    PRIVATE
        OsmAndCoreTools_static
        Qt6::Core
        Qt6::Gui
        Qt6::Widgets
        Qt6::OpenGL
        dwrite
)

target_link_libraries(osmand_native_widget
    PRIVATE
        Qt6::OpenGLWidgets
)

target_link_options(osmand_native_widget
    PRIVATE
        "-Wl,-u,DWriteCreateFactory"
)

target_include_directories(osmand_render_helper
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src"
)

target_include_directories(osmand_native_widget
    PRIVATE
        "${CMAKE_CURRENT_SOURCE_DIR}/src"
)

set_target_properties(osmand_render_helper PROPERTIES
    OUTPUT_NAME "osmand_render_helper"
    RUNTIME_OUTPUT_DIRECTORY "${DIST_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${DIST_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${DIST_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${DIST_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL "${DIST_DIR}"
)

set_target_properties(osmand_native_widget PROPERTIES
    PREFIX ""
    OUTPUT_NAME "osmand_native_widget"
    RUNTIME_OUTPUT_DIRECTORY "${DIST_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_DEBUG "${DIST_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_RELEASE "${DIST_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_RELWITHDEBINFO "${DIST_DIR}"
    RUNTIME_OUTPUT_DIRECTORY_MINSIZEREL "${DIST_DIR}"
    LIBRARY_OUTPUT_DIRECTORY "${DIST_DIR}"
    LIBRARY_OUTPUT_DIRECTORY_DEBUG "${DIST_DIR}"
    LIBRARY_OUTPUT_DIRECTORY_RELEASE "${DIST_DIR}"
    LIBRARY_OUTPUT_DIRECTORY_RELWITHDEBINFO "${DIST_DIR}"
    LIBRARY_OUTPUT_DIRECTORY_MINSIZEREL "${DIST_DIR}"
)

# Platform-specific deployment
if(CMAKE_TARGET_OS STREQUAL "windows")
    set(_helper_dist_dir "${DIST_DIR}")
    set(_windeployqt "${QT_ROOT}/bin/windeployqt.exe")

    if(EXISTS "${_windeployqt}")
        add_custom_command(TARGET osmand_render_helper POST_BUILD
            COMMAND "${_windeployqt}"
                --no-translations
                --no-system-d3d-compiler
                --no-opengl-sw
                "$<TARGET_FILE:osmand_render_helper>"
            COMMAND_EXPAND_LISTS
            VERBATIM
        )
        add_custom_command(TARGET osmand_native_widget POST_BUILD
            COMMAND "${_windeployqt}"
                --no-translations
                --no-system-d3d-compiler
                --no-opengl-sw
                "$<TARGET_FILE:osmand_native_widget>"
            COMMAND_EXPAND_LISTS
            VERBATIM
        )
    endif()

    foreach(_mingw_runtime IN ITEMS libgcc_s_seh-1.dll libstdc++-6.dll libwinpthread-1.dll)
        if(EXISTS "${MINGW_ROOT}/bin/${_mingw_runtime}")
            add_custom_command(TARGET osmand_render_helper POST_BUILD
                COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                    "${MINGW_ROOT}/bin/${_mingw_runtime}"
                    "${_helper_dist_dir}/${_mingw_runtime}"
                VERBATIM
            )
            add_custom_command(TARGET osmand_native_widget POST_BUILD
                COMMAND "${CMAKE_COMMAND}" -E copy_if_different
                    "${MINGW_ROOT}/bin/${_mingw_runtime}"
                    "${_helper_dist_dir}/${_mingw_runtime}"
                VERBATIM
            )
        endif()
    endforeach()
elseif(CMAKE_TARGET_OS STREQUAL "linux")
    # Set RPATH for Linux to find shared libraries relative to executable
    set_target_properties(osmand_render_helper PROPERTIES
        BUILD_RPATH "$ORIGIN"
        INSTALL_RPATH "$ORIGIN"
    )
    set_target_properties(osmand_native_widget PROPERTIES
        BUILD_RPATH "$ORIGIN"
        INSTALL_RPATH "$ORIGIN"
    )
elseif(CMAKE_TARGET_OS STREQUAL "macosx")
    set(_macos_rpath "@loader_path;@executable_path;${QT_ROOT}/lib")
    set_target_properties(osmand_render_helper PROPERTIES
        BUILD_WITH_INSTALL_RPATH ON
        BUILD_RPATH "${_macos_rpath}"
        INSTALL_RPATH "${_macos_rpath}"
    )
    set_target_properties(osmand_native_widget PROPERTIES
        BUILD_WITH_INSTALL_RPATH ON
        BUILD_RPATH "${_macos_rpath}"
        INSTALL_RPATH "${_macos_rpath}"
        INSTALL_NAME_DIR "@rpath"
    )
endif()
