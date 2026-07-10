cmake_minimum_required(VERSION 3.13)

project(pico_pet_node LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(pet_node_core
    src/pet_node.cpp
)
target_include_directories(pet_node_core PUBLIC include)

add_executable(pet_node_demo
    src/main.cpp
)
target_link_libraries(pet_node_demo PRIVATE pet_node_core)

enable_testing()

add_executable(test_pet_node_core
    tests/test_pet_node_core.cpp
)
target_link_libraries(test_pet_node_core PRIVATE pet_node_core)
add_test(NAME pet_node_core COMMAND test_pet_node_core)
