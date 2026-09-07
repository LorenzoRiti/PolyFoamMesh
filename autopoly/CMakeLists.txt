cmake_minimum_required(VERSION 3.20)
project(autopoly VERSION 1.0.0 LANGUAGES CXX)

# C++20 required
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Build configuration
set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type")
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -Wextra -Wpedantic -Werror")

# Find dependencies
find_package(Eigen3 3.4 REQUIRED)
find_package(TBB REQUIRED)
find_package(VTK REQUIRED COMPONENTS vtkCommonCore vtkCommonDataModel vtkIOXML vtkIOGeometry vtkFiltersCore vtkFiltersGeometry)
find_package(GTest REQUIRED)

# CGAL - try to find via find_package or pkg-config
find_package(CGAL REQUIRED COMPONENTS CGAL_Core CGAL_ImageIO)
if(CGAL_FOUND)
    message(STATUS "CGAL found: ${CGAL_VERSION}")
else()
    message(WARNING "CGAL not found - some features will be disabled")
endif()

# pybind11 for Python bindings (optional)
option(BUILD_PYTHON_BINDINGS "Build Python bindings with pybind11" ON)
if(BUILD_PYTHON_BINDINGS)
    find_package(pybind11 REQUIRED)
    find_package(Python3 REQUIRED COMPONENTS Development)
endif()

# OpenCASCADE for STEP/IGES (optional)
option(BUILD_WITH_OCC "Build with OpenCASCADE for STEP/IGES support" OFF)
if(BUILD_WITH_OCC)
    find_package(OpenCASCADE REQUIRED COMPONENTS TKSTEP TKIGES TKTopAlgo TKGeomAlgo TKBool TKMesh TKPrim TKService TKMath TKernel)
endif()

# Include directories
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/include)
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/src)
include_directories(${CMAKE_CURRENT_SOURCE_DIR}/cgal)

# Library target
add_library(autopoly STATIC
    src/Types.cpp
    src/Geometry.cpp
    src/Feature.cpp
    src/SizeField.cpp
    src/SurfaceMesher.cpp
    src/VolumeMesher.cpp
    src/BoundaryLayer.cpp
    src/Quality.cpp
    src/Exporter.cpp
    src/Mesher.cpp
    src/Factory.cpp
    cgal/CgalKernel.cpp
)

target_link_libraries(autopoly PUBLIC
    Eigen3::Eigen
    TBB::tbb
    ${VTK_LIBRARIES}
    ${CGAL_LIBRARIES}
    CGAL::CGAL_Core
    CGAL::CGAL_ImageIO
)

if(BUILD_WITH_OCC)
    target_link_libraries(autopoly PUBLIC ${OpenCASCADE_LIBRARIES})
    target_compile_definitions(autopoly PRIVATE AUTPOLY_WITH_OCC)
endif()

# CLI application
add_executable(autopoly_cli apps/autopoly_cli.cpp)
target_link_libraries(autopoly_cli PRIVATE autopoly)

# Python bindings
if(BUILD_PYTHON_BINDINGS)
    pybind11_add_module(autopoly_python python/autopoly_pybind.cpp)
    target_link_libraries(autopoly_python PRIVATE autopoly)
    set_target_properties(autopoly_python PROPERTIES PREFIX "" SUFFIX ".pyd")
endif()

# Tests
enable_testing()
add_subdirectory(tests)

# Install
install(TARGETS autopoly
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
    RUNTIME DESTINATION bin
)
install(DIRECTORY include/autopoly/ DESTINATION include/autopoly)
install(TARGETS autopoly_cli RUNTIME DESTINATION bin)
if(BUILD_PYTHON_BINDINGS)
    install(TARGETS autopoly_python LIBRARY DESTINATION lib)
endif()