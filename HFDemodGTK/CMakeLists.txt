cmake_minimum_required(VERSION 3.16)

if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)
    set(CMAKE_INSTALL_PREFIX "$ENV{HOME}/.local" CACHE PATH "Install prefix" FORCE)
endif()

project(hfdemod-gtk VERSION 0.1.0 LANGUAGES C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

find_package(OpenGL REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(GTK4 REQUIRED gtk4)
pkg_check_modules(EPOXY REQUIRED epoxy)
pkg_check_modules(FFTW3F REQUIRED fftw3f)
pkg_check_modules(PULSE REQUIRED libpulse-simple)

add_executable(hfdemod-gtk
    src/main.c
    src/renderer.c
    src/spectrum.c
    src/waterfall.c
    src/fft.c
    src/iq_client.c
    src/cat_client.c
    src/text.c
    src/colormap.c
    src/dsp.c
    src/audio.c
    src/drm.c
    src/config.c
)

target_include_directories(hfdemod-gtk PRIVATE
    ${GTK4_INCLUDE_DIRS}
    ${EPOXY_INCLUDE_DIRS}
    ${FFTW3F_INCLUDE_DIRS}
    ${PULSE_INCLUDE_DIRS}
    ${OPENGL_INCLUDE_DIR}
)

target_link_libraries(hfdemod-gtk PRIVATE
    ${GTK4_LIBRARIES}
    ${EPOXY_LIBRARIES}
    ${FFTW3F_LIBRARIES}
    ${PULSE_LIBRARIES}
    OpenGL::GL
    m
    pthread
)

target_compile_definitions(hfdemod-gtk PRIVATE
    APP_VERSION="${PROJECT_VERSION}"
)

target_compile_options(hfdemod-gtk PRIVATE
    ${GTK4_CFLAGS_OTHER}
    ${EPOXY_CFLAGS_OTHER}
    -Wall -Wextra
)

# Copy shaders to build directory
file(GLOB SHADER_FILES ${CMAKE_SOURCE_DIR}/shaders/*)
add_custom_target(copy_shaders ALL
    COMMAND ${CMAKE_COMMAND} -E copy_directory
        ${CMAKE_SOURCE_DIR}/shaders ${CMAKE_BINARY_DIR}/shaders
    DEPENDS ${SHADER_FILES}
    COMMENT "Copying shaders to build directory"
)
add_dependencies(hfdemod-gtk copy_shaders)

# Install
include(GNUInstallDirs)
install(TARGETS hfdemod-gtk RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(DIRECTORY shaders/ DESTINATION ${CMAKE_INSTALL_DATADIR}/hfdemod-gtk/shaders)
