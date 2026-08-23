#include <cv_bridge/cv_bridge.h>
#include <vins_fusion_ros2/vins_estimator.h>

VinsEstimator::VinsEstimator() : rclcpp::Node("vins_estimator") {
  options = std::make_shared<VINSOptions>();
  estimator_ = std::make_shared<Estimator>();
  tf_broadcaster_ = std::make_shared<tf2_ros::TransformBroadcaster>(this);
  initialize();
}

VinsEstimator::~VinsEstimator() {}

void VinsEstimator::initialize() {
  initializeParamters();
  initializeSubscribers();
  initializerPublishers();
}
void VinsEstimator::initializeParamters() {
  auto config_file = readParam<std::string>(this, "config_file");
  world_frame_id = readParam<std::string>(this, "world_frame_id", "world");
  body_frame_id = readParam<std::string>(this, "body_frame_id", "body");
  camera_frame_id = readParam<std::string>(this, "camera_frame_id", "camera");
  options->readParameters(config_file);
  odometry_spike_guard_ = std::make_unique<OdometrySpikeGuard>(
      options->rawOdometryConfirmationStepM());
  estimator_->initialize(options);
}
void VinsEstimator::initializeSubscribers() {
  imu_callback_group_ =
      this->create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);
  // message_filters assumes each input stream reaches it in timestamp order.
  // A Reentrant group lets the multi-threaded executor run callbacks from the
  // same camera concurrently, which made live cam1 arrive out of order and
  // discarded roughly 15% of otherwise matching stereo pairs.  The tracker
  // stays below one 30 fps frame period, so serializing image callbacks keeps
  // full rate while preserving order.
  image_callback_group_ = this->create_callback_group(
      rclcpp::CallbackGroupType::MutuallyExclusive);
  feature_callback_group_ =
      this->create_callback_group(rclcpp::CallbackGroupType::MutuallyExclusive);

  rclcpp::SubscriptionOptions sub_opt_imu;
  sub_opt_imu.callback_group = imu_callback_group_;

  rclcpp::SubscriptionOptions sub_opt_image;
  sub_opt_image.callback_group = image_callback_group_;

  rclcpp::SubscriptionOptions sub_opt_feature;
  sub_opt_feature.callback_group = feature_callback_group_;

  if (options->hasImu()) {
    // IMU 400Hz -> 深度100=0.25s 缓冲太浅, 处理延迟会丢IMU产生>1s空洞
    // 导致预积分报错发散. 加到 2000 (5s).
    auto imu = this->create_subscription<sensor_msgs::msg::Imu>(
        options->imuTopic(), rclcpp::QoS(rclcpp::KeepLast(2000)),
        [this](const sensor_msgs::msg::Imu::SharedPtr msg) {
          auto imu_msg = fromMsg(*msg);
          estimator_->inputIMU(imu_msg);
        },
        sub_opt_imu);
    subs_.push_back(imu);
  }

  if (options->isUsingStereo()) {
    // 后处理: 加深图像队列 (depth 5->100), 避免 VINS 处理慢帧时丢帧造成数据空洞
    rmw_qos_profile_t img_qos = rmw_qos_profile_sensor_data;
    img_qos.depth = 100;
    img_qos.reliability = RMW_QOS_POLICY_RELIABILITY_RELIABLE;

    sub_img0_filter_ = std::make_shared<Subscriber<Image>>(
        this, options->imageTopic(), img_qos, sub_opt_image);

    sub_img1_filter_ = std::make_shared<Subscriber<Image>>(
        this, options->image1Topic(), img_qos, sub_opt_image);

    sub_img0_filter_->registerCallback(
        [this](const sensor_msgs::msg::Image::ConstSharedPtr&) {
          cam0_received_.fetch_add(1);
        });
    sub_img1_filter_->registerCallback(
        [this](const sensor_msgs::msg::Image::ConstSharedPtr&) {
          cam1_received_.fetch_add(1);
        });

    sync_img_ = std::make_shared<message_filters::Synchronizer<SyncPolicy>>(
        SyncPolicy(100), *sub_img0_filter_, *sub_img1_filter_);

    sync_img_->registerCallback(std::bind(&VinsEstimator::stereoCallback, this,
                                          std::placeholders::_1,
                                          std::placeholders::_2));
  } else {
    auto sub_img0 = this->create_subscription<sensor_msgs::msg::Image>(
        options->imageTopic(), rclcpp::QoS(rclcpp::KeepLast(100)),
        [this](const sensor_msgs::msg::Image::SharedPtr msg) {
          ImageData image;
          image.image0 = fromMsg(*msg);
          image.timestamp = fromMsg(msg->header.stamp);
          estimator_->inputImage(image);
        },
        sub_opt_image);
    subs_.push_back(sub_img0);
  }

  auto sub_feature = this->create_subscription<sensor_msgs::msg::PointCloud>(
      "/feature_tracker/feature", rclcpp::QoS(rclcpp::KeepLast(100)),
      [this](const sensor_msgs::msg::PointCloud::SharedPtr msg) {
        estimator_->inputFeature(fromMsg(msg->header.stamp), fromMsg(*msg));
      },
      sub_opt_feature);
  subs_.push_back(sub_feature);

  publish_timer_ =
      this->create_wall_timer(std::chrono::milliseconds(20),
                              std::bind(&VinsEstimator::timeCallback, this));
  odom_publish_timer_ =
      this->create_wall_timer(std::chrono::milliseconds(5),
                              std::bind(&VinsEstimator::publishOdometry, this));
}
void VinsEstimator::initializerPublishers() {
  pub_latest_odometry =
      this->create_publisher<nav_msgs::msg::Odometry>("imu_propagate", 1);
  pub_path = this->create_publisher<nav_msgs::msg::Path>("path", 1);
  pub_odometry = this->create_publisher<nav_msgs::msg::Odometry>("odometry", 1);
  pub_image_track =
      this->create_publisher<sensor_msgs::msg::Image>("image_track", 1);
  // A stereo keyframe is large enough that a four-message DDS history can be
  // exhausted by short optimizer bursts during offline 30 fps replay.
  rclcpp::QoS loop_keyframe_qos(rclcpp::KeepLast(32));
  loop_keyframe_qos.reliable();
  pub_loop_keyframe =
      this->create_publisher<vins_fusion_ros2::msg::LoopKeyFrame>(
          "/loop_fusion/keyframe", loop_keyframe_qos);
  pub_point_cloud =
      this->create_publisher<sensor_msgs::msg::PointCloud>("point_cloud", 1);
  pub_margin_cloud =
      this->create_publisher<sensor_msgs::msg::PointCloud>("margin_cloud", 1);
  pub_keyframe_point =
      this->create_publisher<sensor_msgs::msg::PointCloud>("keyframe_point", 1);
  pub_keyframe_pose =
      this->create_publisher<nav_msgs::msg::Odometry>("keyframe_pose", 1);
}

void VinsEstimator::stereoCallback(
    const sensor_msgs::msg::Image::ConstSharedPtr& img0,
    const sensor_msgs::msg::Image::ConstSharedPtr& img1) {
  std::lock_guard<std::mutex> lock(stereo_processing_mutex_);
  const uint64_t pairs = stereo_pairs_.fetch_add(1) + 1;
  if (pairs % 30 == 0) {
    RCLCPP_INFO(this->get_logger(),
                "[PERF-ROS] cam0=%lu cam1=%lu pairs=%lu",
                cam0_received_.load(), cam1_received_.load(), pairs);
  }
  ImageData image;
  image.image0 = fromMsg(*img0);
  image.image1 = fromMsg(*img1);
  image.timestamp = fromMsg(img0->header.stamp);
  {
    std::lock_guard<std::mutex> frame_lock(stereo_frame_buffer_mutex_);
    stereo_frame_buffer_.push_back({image.timestamp, img0, img1});
    while (stereo_frame_buffer_.size() > 120) {
      stereo_frame_buffer_.pop_front();
    }
  }
  estimator_->inputImage(image);
}

void VinsEstimator::timeCallback() {
  publishImuData();
  publishKeyFrameData();
  publishImage();
  publishPointCloud();
}

void VinsEstimator::publishPointCloud() {
  PointCloudData cloud;
  if (estimator_->getMainCloud(cloud)) {
    auto msg = toMsg(cloud);
    msg.header.frame_id = world_frame_id;
    pub_point_cloud->publish(msg);
  }

  if (estimator_->getMarginCloud(cloud)) {
    auto msg = toMsg(cloud);
    msg.header.frame_id = world_frame_id;
    pub_margin_cloud->publish(msg);
  }

}

void VinsEstimator::publishImage() {
  ImageData image;
  if (estimator_->getTrackImage(image)) {
    auto img = toMsg(image.image0);
    img.header.frame_id = world_frame_id;
    img.header.stamp = toMsg(image.timestamp);
    pub_image_track->publish(img);
  }
}
void VinsEstimator::publishImuData() {
  OdomData imu_odom;
  if (estimator_->getIntegratedImuOdom(imu_odom)) {
    nav_msgs::msg::Odometry odometry = toMsg(imu_odom);
    odometry.header.frame_id = world_frame_id;
    odometry.child_frame_id = body_frame_id;
    pub_latest_odometry->publish(odometry);
  }
}
void VinsEstimator::publishOdometry() {
  OdomData vio_odom;
  if (estimator_->getVisualInertialOdom(vio_odom)) {
    for (const auto& accepted : odometry_spike_guard_->update(vio_odom)) {
      publishOdometrySample(accepted);
    }
  }
}

void VinsEstimator::publishOdometrySample(const OdomData& vio_odom) {
    nav_msgs::msg::Odometry odometry = toMsg(vio_odom);
    odometry.header.frame_id = world_frame_id;
    odometry.child_frame_id = body_frame_id;

    pub_odometry->publish(odometry);
    geometry_msgs::msg::PoseStamped pose_stamped;
    pose_stamped.header = odometry.header;
    pose_stamped.header.frame_id = world_frame_id;
    pose_stamped.pose = odometry.pose.pose;
    path.header = odometry.header;
    path.header.frame_id = world_frame_id;
    path.poses.push_back(pose_stamped);
    pub_path->publish(path);

    // world-->body
    geometry_msgs::msg::TransformStamped tf_msg;
    tf_msg.header.stamp = odometry.header.stamp;
    tf_msg.header.frame_id = world_frame_id;
    tf_msg.child_frame_id = body_frame_id;
    tf_msg.transform.translation.x = odometry.pose.pose.position.x;
    tf_msg.transform.translation.y = odometry.pose.pose.position.y;
    tf_msg.transform.translation.z = odometry.pose.pose.position.z;
    tf_msg.transform.rotation = odometry.pose.pose.orientation;
    tf_broadcaster_->sendTransform(tf_msg);
    PoseData camera_pose;
    estimator_->getCameraPose(0, camera_pose);

    // body-->camera
    tf_msg.header.frame_id = body_frame_id;
    tf_msg.child_frame_id = camera_frame_id;
    tf_msg.transform.translation.x = camera_pose.position.x();
    tf_msg.transform.translation.y = camera_pose.position.y();
    tf_msg.transform.translation.z = camera_pose.position.z();
    tf_msg.transform.rotation.x = camera_pose.orientation.x();
    tf_msg.transform.rotation.y = camera_pose.orientation.y();
    tf_msg.transform.rotation.z = camera_pose.orientation.z();
    tf_msg.transform.rotation.w = camera_pose.orientation.w();
    tf_broadcaster_->sendTransform(tf_msg);
}

void VinsEstimator::publishKeyFrameData() {
  KeyFrameData keyframe;
  while (estimator_->getKeyFrameData(keyframe)) {
    const auto &pose = keyframe.pose;
    nav_msgs::msg::Odometry odometry;
    odometry.header.stamp = toMsg(pose.timestamp);
    odometry.header.frame_id = world_frame_id;
    odometry.child_frame_id = body_frame_id;
    odometry.pose.pose.position.x = pose.position.x();
    odometry.pose.pose.position.y = pose.position.y();
    odometry.pose.pose.position.z = pose.position.z();
    odometry.pose.pose.orientation.x = pose.orientation.x();
    odometry.pose.pose.orientation.y = pose.orientation.y();
    odometry.pose.pose.orientation.z = pose.orientation.z();
    odometry.pose.pose.orientation.w = pose.orientation.w();
    sensor_msgs::msg::Image stereo;
    if (!buildLoopStereoKeyFrame(pose.timestamp, stereo)) {
      return;
    }
    auto cloud = toMsg(keyframe.cloud);
    cloud.header.frame_id = world_frame_id;
    pub_keyframe_point->publish(cloud);
    pub_keyframe_pose->publish(odometry);
    vins_fusion_ros2::msg::LoopKeyFrame loop_keyframe;
    loop_keyframe.sequence = loop_keyframe_sequence_.fetch_add(1);
    loop_keyframe.stereo = std::move(stereo);
    loop_keyframe.pose = odometry;
    loop_keyframe.cloud = cloud;
    pub_loop_keyframe->publish(loop_keyframe);
  }
}

bool VinsEstimator::buildLoopStereoKeyFrame(
    double timestamp, sensor_msgs::msg::Image &stereo) {
  StereoFramePair pair;
  bool found = false;
  {
    std::lock_guard<std::mutex> lock(stereo_frame_buffer_mutex_);
    constexpr double kMaxTimestampErrorS = 0.002;
    auto best = stereo_frame_buffer_.end();
    double best_error = kMaxTimestampErrorS;
    for (auto it = stereo_frame_buffer_.begin();
         it != stereo_frame_buffer_.end(); ++it) {
      const double error = std::abs(it->timestamp - timestamp);
      if (error <= best_error) {
        best = it;
        best_error = error;
      }
    }
    if (best != stereo_frame_buffer_.end()) {
      pair = *best;
      stereo_frame_buffer_.erase(stereo_frame_buffer_.begin(),
                                 std::next(best));
      found = true;
    }
  }

  if (!found) {
    RCLCPP_WARN(this->get_logger(),
                "[LOOP_STEREO_KEYFRAME_DROP] reason=no_timestamp_match "
                "timestamp=%.9f",
                timestamp);
    return false;
  }

  if (!pair.left || !pair.right || pair.left->height != pair.right->height ||
      pair.left->width != pair.right->width || pair.left->width == 0 ||
      pair.left->height == 0 || pair.left->step < pair.left->width ||
      pair.right->step < pair.right->width) {
    RCLCPP_WARN(this->get_logger(),
                "[LOOP_STEREO_KEYFRAME_DROP] reason=invalid_image_shape");
    return false;
  }

  stereo.header = pair.left->header;
  stereo.header.stamp = toMsg(timestamp);
  stereo.header.frame_id = camera_frame_id;
  stereo.height = pair.left->height;
  stereo.width = pair.left->width * 2;
  stereo.encoding = "mono8";
  stereo.is_bigendian = false;
  stereo.step = stereo.width;
  stereo.data.resize(static_cast<size_t>(stereo.step) * stereo.height);
  for (uint32_t row = 0; row < stereo.height; ++row) {
    const auto* left_row = pair.left->data.data() +
                           static_cast<size_t>(row) * pair.left->step;
    const auto* right_row = pair.right->data.data() +
                            static_cast<size_t>(row) * pair.right->step;
    auto* output_row = stereo.data.data() +
                       static_cast<size_t>(row) * stereo.step;
    std::copy_n(left_row, pair.left->width, output_row);
    std::copy_n(right_row, pair.right->width,
                output_row + pair.left->width);
  }
  return true;
}
