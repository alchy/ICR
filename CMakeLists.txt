cmake_minimum_required(VERSION 3.16)
project(ICR VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/bin)

# ── FetchContent: GLFW + Dear ImGui ──────────────────────────────────────────

include(FetchContent)

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

# ── Engine sources ────────────────────────────────────────────────────────────

set(ENGINE_SOURCES
    engine/synth_core_registry.cpp
    engine/core_engine.cpp          # contains MINIAUDIO_IMPLEMENTATION
    engine/midi_input.cpp
    third_party/RtMidi.cpp
)

# ── Synth cores ───────────────────────────────────────────────────────────────
# Each core self-registers via REGISTER_SYNTH_CORE macro.

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

# ── Sample bank directory (cross-platform, overridable) ──────────────────────

set(ICR_SAMPLE_DIR "" CACHE STRING "Default sample bank directory for SamplerCore")
if (ICR_SAMPLE_DIR)
    add_compile_definitions(ICR_SAMPLE_DIR="${ICR_SAMPLE_DIR}")
endif()

# ── Common include directories ────────────────────────────────────────────────

set(COMMON_INCLUDES
    ${CMAKE_SOURCE_DIR}             # project root (engine/, cores/, dsp/, ...)
    ${CMAKE_SOURCE_DIR}/engine      # for miniaudio.h, internal engine headers
    ${CMAKE_SOURCE_DIR}/third_party # json.hpp, RtMidi.h
)

# ── Platform helpers ──────────────────────────────────────────────────────────

macro(icr_platform_setup target)
    if (WIN32)
        target_compile_definitions(${target} PRIVATE __WINDOWS_MM__ _CRT_SECURE_NO_WARNINGS WIN32_LEAN_AND_MEAN)
        target_link_libraries(${target} PRIVATE ole32 winmm)
    elseif (APPLE)
        target_compile_definitions(${target} PRIVATE __MACOSX_CORE__)
        target_link_libraries(${target} PRIVATE
            "-framework CoreAudio" "-framework AudioToolbox"
            "-framework CoreFoundation" "-framework CoreMIDI")
    else()
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

    if (CMAKE_SYSTEM_PROCESSOR MATCHES "AMD64|x86_64|X86_64")
        if (MSVC)
            target_compile_options(${target} PRIVATE /arch:AVX2)
        else()
            target_compile_options(${target} PRIVATE -mavx2 -mfma)
        endif()
    endif()

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

add_executable(icr
    main.cpp
    ${ENGINE_SOURCES}
    ${CORE_SOURCES}
    ${DSP_SOURCES}
)

target_include_directories(icr PRIVATE ${COMMON_INCLUDES})
icr_platform_setup(icr)

# ── ICRGUI — GUI application ──────────────────────────────────────────────────

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
    gui/resonator_gui.cpp
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

if (WIN32)
    target_compile_definitions(icrgui PRIVATE __WINDOWS_MM__ _CRT_SECURE_NO_WARNINGS WIN32_LEAN_AND_MEAN)
    target_link_libraries(icrgui PRIVATE ole32 winmm glfw OpenGL::GL)
elseif (APPLE)
    target_compile_definitions(icrgui PRIVATE __MACOSX_CORE__)
    target_link_libraries(icrgui PRIVATE glfw OpenGL::GL
        "-framework CoreAudio" "-framework AudioToolbox"
        "-framework CoreFoundation" "-framework CoreMIDI")
else()
    find_package(PkgConfig QUIET)
    if (PkgConfig_FOUND)
        pkg_check_modules(JACK QUIET jack)
    endif()
    if (JACK_FOUND)
        target_compile_definitions(icrgui PRIVATE __UNIX_JACK__)
        target_link_libraries(icrgui PRIVATE ${JACK_LIBRARIES})
    else()
        target_compile_definitions(icrgui PRIVATE __LINUX_ALSA__)
        target_link_libraries(icrgui PRIVATE asound)
    endif()
    target_link_libraries(icrgui PRIVATE glfw OpenGL::GL pthread m dl)
endif()

if (CMAKE_SYSTEM_PROCESSOR MATCHES "AMD64|x86_64|X86_64")
    if (MSVC)
        target_compile_options(icrgui PRIVATE /arch:AVX2)
    else()
        target_compile_options(icrgui PRIVATE -mavx2 -mfma)
    endif()
endif()

if (MSVC)
    target_compile_options(icrgui PRIVATE
        $<$<CONFIG:Debug>:/Od /Zi>
        $<$<CONFIG:Release>:/O2 /DNDEBUG>)
else()
    target_compile_options(icrgui PRIVATE
        $<$<CONFIG:Debug>:-g -O0>
        $<$<CONFIG:Release>:-O3 -DNDEBUG>)
endif()

# ── Copy soundbanks/ next to each executable ──────────────────────────────────

foreach(_target icr icrgui)
    add_custom_command(TARGET ${_target} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_directory
            ${CMAKE_SOURCE_DIR}/soundbanks
            $<TARGET_FILE_DIR:${_target}>/soundbanks
        COMMENT "Copying soundbanks/ -> ${_target}/soundbanks"
    )
endforeach()

# ── Custom targets ────────────────────────────────────────────────────────────

add_custom_target(run
    COMMAND $<TARGET_FILE:icr> --core SineCore
    DEPENDS icr
    WORKING_DIRECTORY $<TARGET_FILE_DIR:icr>
)

add_custom_target(clean-exports
    COMMAND ${CMAKE_COMMAND} -E remove_directory ${CMAKE_SOURCE_DIR}/exports
    COMMENT "Removing exported WAV files"
)
