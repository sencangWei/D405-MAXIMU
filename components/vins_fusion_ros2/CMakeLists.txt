cmake_minimum_required(VERSION 3.5)
project(vins_fusion_ros2)

set(CMAKE_CXX_STANDARD 14)
add_compile_options(-Wextra -Wpedantic)

set(CMAKE_BUILD_TYPE Release)

# find dependencies
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(ament_index_cpp REQUIRED)
find_package(rcpputils REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(visualization_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(tf2 REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(cv_bridge REQUIRED)
find_package(image_transport REQUIRED)
find_package(message_filters REQUIRED)
find_package(rosbag2_cpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/LoopKeyFrame.msg"
  DEPENDENCIES sensor_msgs nav_msgs
)


include_directories(
  ${CMAKE_CURRENT_SOURCE_DIR}/include
)

add_subdirectory(vins)
add_subdirectory(loop_fusion)
add_executable(vins_fusion_ros2_node src/vins_estimator.cpp src/vins_estimator_node.cpp src/visualization.cpp)
add_dependencies(vins_fusion_ros2_node ${PROJECT_NAME}__cpp)
ament_target_dependencies(vins_fusion_ros2_node rclcpp rcpputils message_filters std_msgs visualization_msgs geometry_msgs nav_msgs tf2 tf2_ros cv_bridge image_transport)
target_link_libraries(vins_fusion_ros2_node vins_lib) 
rosidl_get_typesupport_target(cpp_typesupport_target ${PROJECT_NAME} "rosidl_typesupport_cpp")
target_link_libraries(vins_fusion_ros2_node "${cpp_typesupport_target}")

add_executable(db3_replay_cpp src/db3_replay_cpp.cpp)
ament_target_dependencies(db3_replay_cpp rclcpp rosbag2_cpp sensor_msgs std_msgs)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_frame_clock_mapping
    test/test_frame_clock_mapping.cpp
  )
  target_include_directories(test_frame_clock_mapping PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
  )
  ament_add_gtest(test_stationary_motion_gate
    test/test_stationary_motion_gate.cpp
  )
  target_include_directories(test_stationary_motion_gate PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/vins/include
  )
  ament_add_gtest(test_pose_integrity_guard
    test/test_pose_integrity_guard.cpp
  )
  target_include_directories(test_pose_integrity_guard PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_SOURCE_DIR}/vins/include
  )
  target_link_libraries(test_pose_integrity_guard vins_lib)
endif()

# Install launch files
install(
  DIRECTORY launch config support_files
  DESTINATION share/${PROJECT_NAME}
)

# Install nodes
install(
  TARGETS vins_fusion_ros2_node db3_replay_cpp
  DESTINATION lib/${PROJECT_NAME}
)
ament_package()
