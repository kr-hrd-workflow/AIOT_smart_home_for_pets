if (NOT PICO_SDK_PATH)
    message(FATAL_ERROR "PICO_SDK_PATH must be provided by tools/build_pico.ps1")
endif()
include("${PICO_SDK_PATH}/external/pico_sdk_import.cmake")
