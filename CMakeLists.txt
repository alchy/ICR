cmake_minimum_required(VERSION 3.16)
project(ICR VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

# ── Build options ─────────────────────────────────────────────────────────────

option(ICR_BUILD_GUI     "Build icrgui (ImGui + GLFW + OpenGL3)" ON)
option(ICR_BUILD_CLI     "Build icr (headless CLI)"              ON)
option(ICR_USE_AVX2      "Enable AVX2/FMA on x86_64"            ON)

# ── FetchContent: GLFW + Dear ImGui ──────────────────────────────────────────

include(FetchContent)

if (ICR_BUILD_GUI)
    FetchContent_Declare(glfw
        GIT_REPOSITORY https://github.com/glfw/glfw.git
        GIT_TAG        3.4
        GIT_SHALLOW    TRUE
    )
    set(GLFW_BUILD_DOCS     OFF CACHE BOOL "" FORCE)
    set(GLFW_BUILD_TESTS    OFF CACHE BOOL "" FORCE)
    set(GLFW_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(glfw)

    FetchContent_Declare(imgui
        GIT_REPOSITORY https://github.com/ocornut/imgui.git
        GIT_TAG        v1.91.9
        GIT_SHALLOW    TRUE
    )
    FetchContent_MakeAvailable(imgui)

    find_package(OpenGL REQUIRED)
endif()

# ── Engine sources ────────────────────────────────────────────────────────────

set(ENGINE_SOURCES
    engine/synth_core_registry.cpp
    engine/engine.cpp
    engine/app_config.cpp
    engine/engine_config.cpp
    engine/batch_renderer.cpp
    engine/sysex_handler.cpp
    engine/audio_device.cpp
    engine/miniaudio_impl.cpp
    engine/midi_input.cpp
    third_party/RtMidi.cpp
)

# ── Synth cores ───────────────────────────────────────────────────────────────

set(CORE_SOURCES
    cores/sine/sine_core.cpp
    cores/additive_synthesis_piano/additive_synthesis_piano_core.cpp
    cores/physical_modeling_piano/physical_modeling_piano_core.cpp
    cores/sampler/sampler_core.cpp
)

# ── DSP chain ─────────────────────────────────────────────────────────────────

set(DSP_SOURCES
    dsp/dsp_chain.cpp
    dsp/limiter/limiter.cpp
    dsp/bbe/bbe.cpp
    dsp/convolver/convolver.cpp
)

# ── Common include directories ────────────────────────────────────────────────

set(COMMON_INCLUDES
    ${CMAKE_SOURCE_DIR}
    ${CMAKE_SOURCE_DIR}/engine
    ${CMAKE_SOURCE_DIR}/third_party
)

# ── Platform setup macro (shared by all targets) ──────────────────────────────
#
# Handles: audio backend (miniaudio), MIDI backend (RtMidi),
#          SIMD flags, compiler optimizations.
#
# Supported platforms:
#   - Windows (x86_64)     : WASAPI + Windows MM MIDI
#   - macOS   (x86_64/ARM) : CoreAudio + CoreMIDI
#   - Linux   (x86_64/ARM) : ALSA or JACK + ALSA MIDI
#   - Linux   (aarch64/RPi): ALSA (no AVX2)
#

macro(icr_platform_setup target)
    # ── Audio + MIDI backend ──────────────────────────────────────────────
    if (WIN32)
        target_compile_definitions(${target} PRIVATE
            __WINDOWS_MM__ _CRT_SECURE_NO_WARNINGS WIN32_LEAN_AND_MEAN)
        target_link_libraries(${target} PRIVATE ole32 winmm)
    elseif (APPLE)
        target_compile_definitions(${target} PRIVATE __MACOSX_CORE__)
        target_link_libraries(${target} PRIVATE
            "-framework CoreAudio" "-framework AudioToolbox"
            "-framework CoreFoundation" "-framework CoreMIDI")
    else()
        # Linux: prefer JACK if available, fall back to ALSA
        find_package(PkgConfig QUIET)
        if (PkgConfig_FOUND)
            pkg_check_modules(JACK QUIET jack)
        endif()
        if (JACK_FOUND)
            target_compile_definitions(${target} PRIVATE __UNIX_JACK__)
            target_link_libraries(${target} PRIVATE ${JACK_LIBRARIES})
        else()
            target_compile_definitions(${target} PRIVATE __LINUX_ALSA__)
            target_link_libraries(${target} PRIVATE asound)
        endif()
        target_link_libraries(${target} PRIVATE pthread m dl)
    endif()

    # ── SIMD (x86_64 only, skip on ARM/RPi) ───────────────────────────────
    if (ICR_USE_AVX2 AND CMAKE_SYSTEM_PROCESSOR MATCHES "AMD64|x86_64|X86_64")
        if (MSVC)
            target_compile_options(${target} PRIVATE /arch:AVX2)
        else()
            target_compile_options(${target} PRIVATE -mavx2 -mfma)
        endif()
    endif()

    # ── Compiler optimization flags ───────────────────────────────────────
    if (MSVC)
        target_compile_options(${target} PRIVATE
            $<$<CONFIG:Debug>:/Od /Zi>
            $<$<CONFIG:Release>:/O2 /DNDEBUG>)
    else()
        target_compile_options(${target} PRIVATE
            $<$<CONFIG:Debug>:-g -O0>
            $<$<CONFIG:Release>:-O3 -DNDEBUG>)
    endif()
endmacro()

# ── icr — headless real-time CLI ──────────────────────────────────────────────

if (ICR_BUILD_CLI)
    add_executable(icr
        main.cpp
        ${ENGINE_SOURCES}
        ${CORE_SOURCES}
        ${DSP_SOURCES}
    )
    target_include_directories(icr PRIVATE ${COMMON_INCLUDES})
    icr_platform_setup(icr)
endif()

# ── icrgui — GUI application ─────────────────────────────────────────────────

if (ICR_BUILD_GUI)
    set(IMGUI_DIR ${imgui_SOURCE_DIR})
    set(IMGUI_SOURCES
        ${IMGUI_DIR}/imgui.cpp
        ${IMGUI_DIR}/imgui_draw.cpp
        ${IMGUI_DIR}/imgui_tables.cpp
        ${IMGUI_DIR}/imgui_widgets.cpp
        ${IMGUI_DIR}/backends/imgui_impl_glfw.cpp
        ${IMGUI_DIR}/backends/imgui_impl_opengl3.cpp
    )

    add_executable(icrgui
        main_gui.cpp
        gui/engine_gui.cpp
        ${ENGINE_SOURCES}
        ${CORE_SOURCES}
        ${DSP_SOURCES}
        ${IMGUI_SOURCES}
    )

    target_include_directories(icrgui PRIVATE
        ${COMMON_INCLUDES}
        ${IMGUI_DIR}
        ${IMGUI_DIR}/backends
    )

    icr_platform_setup(icrgui)
    target_link_libraries(icrgui PRIVATE glfw OpenGL::GL)
endif()

# ── Copy icr-config.json next to each executable ─────────────────────────────

foreach(_target icr icrgui)
    if (TARGET ${_target})
        add_custom_command(TARGET ${_target} POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                ${CMAKE_SOURCE_DIR}/icr-config.json
                $<TARGET_FILE_DIR:${_target}>/icr-config.json
            COMMENT "Copying icr-config.json -> ${_target}/"
        )
    endif()
endforeach()

# ── Convenience targets ───────────────────────────────────────────────────────

if (TARGET icr)
    add_custom_target(run
        COMMAND $<TARGET_FILE:icr> --core SineCore
        DEPENDS icr
        WORKING_DIRECTORY $<TARGET_FILE_DIR:icr>
    )
endif()

add_custom_target(clean-exports
    COMMAND ${CMAKE_COMMAND} -E remove_directory ${CMAKE_SOURCE_DIR}/exports
    COMMENT "Removing exported WAV files"
)
