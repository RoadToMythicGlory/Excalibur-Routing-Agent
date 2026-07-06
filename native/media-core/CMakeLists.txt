cmake_minimum_required(VERSION 3.20)
project(media_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

include(FetchContent)

# nlohmann/json is header-only; fetch a pinned release so the build is
# reproducible and does not depend on a system package. If you prefer a
# system/vendored copy, set -Dnlohmann_json_DIR=... and this block is skipped.
find_package(nlohmann_json 3.10 QUIET)
if(NOT nlohmann_json_FOUND)
  FetchContent_Declare(
    nlohmann_json
    URL https://github.com/nlohmann/json/releases/download/v3.11.3/json.tar.xz
  )
  set(JSON_BuildTests OFF CACHE INTERNAL "")
  FetchContent_MakeAvailable(nlohmann_json)
endif()

# Pure, GStreamer-free translation unit: pipeline strings + JSON protocol.
add_library(pipeline_spec STATIC src/pipeline_spec.cpp)
target_include_directories(pipeline_spec PUBLIC src)
target_link_libraries(pipeline_spec PUBLIC nlohmann_json::nlohmann_json)

# GStreamer discovery. On Linux/macOS/MSYS2 this uses pkg-config; on Windows the
# MSVC installer sets PKG_CONFIG_PATH / GSTREAMER_1_0_ROOT_MSVC_X86_64.
find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
  pkg_check_modules(GST QUIET IMPORTED_TARGET
    gstreamer-1.0
    gstreamer-app-1.0
    gstreamer-video-1.0)
endif()

if(GST_FOUND)
  add_executable(media-core src/main.cpp src/media_core.cpp)
  target_link_libraries(media-core
    PRIVATE pipeline_spec PkgConfig::GST)
  message(STATUS "media-core: GStreamer found, building the full binary.")
else()
  message(WARNING
    "media-core: GStreamer (gstreamer-1.0/app/video) not found via pkg-config. "
    "Skipping the media-core binary; only the pipeline_spec library and its "
    "tests will build. Install the GStreamer devel package and re-run cmake.")
endif()

# Unit tests for the pure layer (no GStreamer runtime required).
enable_testing()
add_executable(test_pipeline_spec tests/test_pipeline_spec.cpp)
target_link_libraries(test_pipeline_spec PRIVATE pipeline_spec)
add_test(NAME pipeline_spec COMMAND test_pipeline_spec)
