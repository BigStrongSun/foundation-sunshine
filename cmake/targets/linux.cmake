# linux specific target definitions
if(CUDA_FOUND)
    # Keep the CUDA kernel on C++17. Shared host libraries export C++23 compile
    # features, which otherwise propagate to nvcc through the mixed target.
    add_library(sunshine_cuda OBJECT "${CMAKE_SOURCE_DIR}/src/platform/linux/cuda.cu")
    set_target_properties(sunshine_cuda PROPERTIES CUDA_STANDARD 17 CUDA_STANDARD_REQUIRED ON
            POSITION_INDEPENDENT_CODE ON)
    target_compile_definitions(sunshine_cuda PRIVATE ${SUNSHINE_DEFINITIONS})
    target_include_directories(sunshine_cuda PRIVATE $<TARGET_PROPERTY:sunshine,INCLUDE_DIRECTORIES>)
    target_link_libraries(sunshine PRIVATE sunshine_cuda)
endif()
