cmake_minimum_required(VERSION 3.13)

include(pico_sdk_import.cmake)
project(petcare_pico C CXX ASM)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

pico_sdk_init()

function(add_petcare_node target profile_definition)
    add_executable(${target}
        src/main.cpp
        src/mqtt_publisher.cpp
        src/provisioning_store.cpp
        src/sensors.cpp
        ../src/pet_node.cpp
        ../src/provisioning.cpp
    )
    target_include_directories(${target} PRIVATE include ../include)
    target_compile_definitions(${target} PRIVATE PICO_ON_DEVICE=1 ${profile_definition}=1)
    target_link_libraries(${target} PRIVATE
        pico_stdlib
        pico_cyw43_arch_lwip_threadsafe_background
        pico_lwip_mqtt
        pico_lwip_sntp
        hardware_adc
        hardware_flash
        hardware_i2c
        hardware_sync
        hardware_uart
        hardware_watchdog
    )
    pico_enable_stdio_usb(${target} 1)
    pico_enable_stdio_uart(${target} 0)
    pico_add_extra_outputs(${target})
endfunction()

add_petcare_node(entrance-01 PETCARE_PROFILE_ENTRANCE)
add_petcare_node(petzone-01 PETCARE_PROFILE_PETZONE)
