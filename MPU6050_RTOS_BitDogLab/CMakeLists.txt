cmake_minimum_required(VERSION 3.13)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(WIN32)
    set(USERHOME $ENV{USERPROFILE})
else()
    set(USERHOME $ENV{HOME})
endif()
set(sdkVersion 2.2.0)
set(toolchainVersion 14_2_Rel1)
set(picotoolVersion 2.2.0-a4)
set(picoVscode ${USERHOME}/.pico-sdk/cmake/pico-vscode.cmake)
if (EXISTS ${picoVscode})
    include(${picoVscode})
endif()

set(PICO_BOARD pico_w CACHE STRING "Board type")
include(pico_sdk_import.cmake)

project(blink C CXX ASM)

set(FREERTOS_KERNEL_PATH ${USERHOME}/.pico-sdk/sdk/2.2.0/lib/freertos)
include(${FREERTOS_KERNEL_PATH}/portable/ThirdParty/GCC/RP2040/FreeRTOS_Kernel_import.cmake)

pico_sdk_init()

include_directories(${CMAKE_CURRENT_LIST_DIR})

add_executable(blink blink.c)

target_link_libraries(blink
    pico_stdlib
    hardware_i2c
    FreeRTOS-Kernel
    FreeRTOS-Kernel-Heap4
    m
)

pico_add_extra_outputs(blink)
pico_enable_stdio_usb(blink 1)
pico_enable_stdio_uart(blink 0)