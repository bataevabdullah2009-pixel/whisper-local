cmake_minimum_required(VERSION 3.20)
project(WhisperLocalBridge LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "Static dependencies" FORCE)
set(GGML_BACKEND_DL OFF CACHE BOOL "No dynamic backend loading" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "Portable CPU code" FORCE)
set(GGML_RPC OFF CACHE BOOL "No network backend" FORCE)
set(GGML_CUDA OFF CACHE BOOL "Mac bridge" FORCE)
set(GGML_VULKAN OFF CACHE BOOL "Mac bridge" FORCE)
set(GGML_OPENCL OFF CACHE BOOL "Mac bridge" FORCE)
set(GGML_BLAS OFF CACHE BOOL "Accelerate handles Mac matrix operations" FORCE)
set(GGML_ACCELERATE ON CACHE BOOL "Apple CPU acceleration" FORCE)
set(GGML_METAL_EMBED_LIBRARY ON CACHE BOOL "Self contained Metal shaders" FORCE)
set(WHISPER_BUILD_TESTS OFF CACHE BOOL "Library only" FORCE)
set(WHISPER_BUILD_EXAMPLES OFF CACHE BOOL "Library only" FORCE)
set(WHISPER_BUILD_SERVER OFF CACHE BOOL "No server" FORCE)
set(WHISPER_BUILD_IS_DEV OFF CACHE BOOL "Pinned release" FORCE)
set(WHISPER_COREML OFF CACHE BOOL "Metal backend" FORCE)
add_subdirectory("${WHISPER_SOURCE_ROOT}" upstream EXCLUDE_FROM_ALL)

# The accessor is compiled alongside the pinned implementation; upstream files remain unmodified.
file(READ "${WHISPER_SOURCE_ROOT}/src/whisper.cpp" WHISPER_IMPL)
file(READ "${CMAKE_CURRENT_SOURCE_DIR}/context_backend.inc" BACKEND_ACCESSOR)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/whisper-local-context.cpp" "${WHISPER_IMPL}\n${BACKEND_ACCESSOR}")
set_property(TARGET whisper PROPERTY SOURCES
    "${WHISPER_SOURCE_ROOT}/include/whisper.h"
    "${WHISPER_SOURCE_ROOT}/src/whisper-arch.h"
    "${CMAKE_CURRENT_BINARY_DIR}/whisper-local-context.cpp")

add_library(whisperlocal SHARED bridge.cpp)
target_link_libraries(whisperlocal PRIVATE whisper)
set_target_properties(whisperlocal PROPERTIES
    CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN YES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/output"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/output")
