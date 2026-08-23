find_package(OpenCV REQUIRED)
find_package(Ceres REQUIRED)
find_package(Eigen3 REQUIRED)

add_executable(loop_fusion_node
    src/pose_graph_node.cpp
    src/pose_graph.cpp
    src/keyframe.cpp
    src/utility/CameraPoseVisualization.cpp
    src/ThirdParty/DBoW/BowVector.cpp
    src/ThirdParty/DBoW/FBrief.cpp
    src/ThirdParty/DBoW/FeatureVector.cpp
    src/ThirdParty/DBoW/QueryResults.cpp
    src/ThirdParty/DBoW/ScoringObject.cpp
    src/ThirdParty/DUtils/Random.cpp
    src/ThirdParty/DUtils/Timestamp.cpp
    src/ThirdParty/DVision/BRIEF.cpp
    src/ThirdParty/VocabularyBinary.cpp
)
add_dependencies(loop_fusion_node ${CMAKE_PROJECT_NAME}__cpp)

target_include_directories(loop_fusion_node PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/src
  ${CMAKE_BINARY_DIR}/rosidl_generator_cpp
  ${CERES_INCLUDE_DIRS}
  ${OpenCV_INCLUDE_DIRS}
)
ament_target_dependencies(loop_fusion_node
  rclcpp ament_index_cpp std_msgs sensor_msgs visualization_msgs nav_msgs cv_bridge
)
target_link_libraries(loop_fusion_node vins_lib ${OpenCV_LIBS} ${CERES_LIBRARIES})
target_link_libraries(loop_fusion_node ${CMAKE_PROJECT_NAME}__rosidl_typesupport_cpp)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)
  ament_add_gtest(test_tracked_brief_matcher
    test/test_tracked_brief_matcher.cpp
  )
  target_include_directories(test_tracked_brief_matcher PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${OpenCV_INCLUDE_DIRS}
  )

  ament_add_gtest(test_loop_correction_policy
    test/test_loop_correction_policy.cpp
  )
  target_include_directories(test_loop_correction_policy PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
  )

  ament_add_gtest(test_loop_candidate_priority
    test/test_loop_candidate_priority.cpp
  )
  target_include_directories(test_loop_candidate_priority PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
  )

  ament_add_gtest(test_feature_edge_weight
    test/test_feature_edge_weight.cpp
  )
  target_include_directories(test_feature_edge_weight PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
  )

  ament_add_gtest(test_loop_excursion_anchor
    test/test_loop_excursion_anchor.cpp
  )
  target_include_directories(test_loop_excursion_anchor PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
  )

  ament_add_gtest(test_loop_edge_strength
    test/test_loop_edge_strength.cpp
  )
  target_include_directories(test_loop_edge_strength PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
  )

  ament_add_gtest(test_realtime_drift_limiter
    test/test_realtime_drift_limiter.cpp
  )
  target_include_directories(test_realtime_drift_limiter PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
  )
endif()

install(
  TARGETS loop_fusion_node
  DESTINATION lib/${CMAKE_PROJECT_NAME}
)
