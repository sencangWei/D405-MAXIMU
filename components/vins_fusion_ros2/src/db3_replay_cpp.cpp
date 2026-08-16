#include <builtin_interfaces/msg/time.hpp>
#include <rclcpp/rclcpp.hpp>
#include <rclcpp/serialization.hpp>
#include <rosbag2_cpp/reader.hpp>
#include <rosbag2_storage/storage_filter.hpp>
#include <sensor_msgs/msg/image.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <std_msgs/msg/string.hpp>

#include "vins_fusion_ros2/frame_clock_mapping.h"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <glob.h>
#include <sys/stat.h>
#include <map>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

constexpr char kLeftData[] =
    "/device_0/sensor_0/Infrared_1/image/data";
constexpr char kLeftMeta[] =
    "/device_0/sensor_0/Infrared_1/image/metadata";
constexpr char kRightData[] =
    "/device_0/sensor_0/Infrared_2/image/data";
constexpr char kRightMeta[] =
    "/device_0/sensor_0/Infrared_2/image/metadata";

struct Arguments {
  std::string session;
  std::string image_db3;
  double rate = 1.0;
  double skip_s = 0.0;
  double imu_shift_ms = 0.0;
  bool imu_accel_matrix_supplied = false;
  bool imu_accel_offset_supplied = false;
  std::array<double, 9> imu_accel_matrix{{1.0, 0.0, 0.0,
                                          0.0, 1.0, 0.0,
                                          0.0, 0.0, 1.0}};
  std::array<double, 3> imu_accel_offset_g{{0.0, 0.0, 0.0}};
};

Arguments parseArguments(int argc, char **argv) {
  Arguments args;
  for (int i = 1; i < argc; ++i) {
    const std::string key(argv[i]);
    if (key == "--session" && i + 1 < argc) {
      args.session = argv[++i];
    } else if (key == "--image-db3" && i + 1 < argc) {
      args.image_db3 = argv[++i];
    } else if (key == "--rate" && i + 1 < argc) {
      args.rate = std::stod(argv[++i]);
    } else if (key == "--skip-s" && i + 1 < argc) {
      args.skip_s = std::stod(argv[++i]);
    } else if (key == "--imu-shift-ms" && i + 1 < argc) {
      args.imu_shift_ms = std::stod(argv[++i]);
    } else if (key == "--imu-align-s" && i + 1 < argc) {
      const double align_s = std::stod(argv[++i]);
      args.imu_shift_ms += align_s * 1000.0;
    } else if (key == "--imu-accel-matrix" && i + 9 < argc) {
      for (double &value : args.imu_accel_matrix) value = std::stod(argv[++i]);
      args.imu_accel_matrix_supplied = true;
    } else if (key == "--imu-accel-offset-g" && i + 3 < argc) {
      for (double &value : args.imu_accel_offset_g) value = std::stod(argv[++i]);
      args.imu_accel_offset_supplied = true;
    } else if (key == "--mode" && i + 1 < argc) {
      if (std::string(argv[++i]) != "stereo")
        throw std::runtime_error("C++ replay currently supports stereo mode only");
    } else {
      throw std::runtime_error("Unknown or incomplete argument: " + key);
    }
  }
  if (args.session.empty()) throw std::runtime_error("--session is required");
  if (args.rate <= 0.0) throw std::runtime_error("--rate must be positive");
  if (args.imu_accel_matrix_supplied != args.imu_accel_offset_supplied) {
    throw std::runtime_error(
        "--imu-accel-matrix and --imu-accel-offset-g must be supplied together");
  }
  return args;
}

std::vector<std::string> splitCsv(const std::string &line) {
  std::vector<std::string> fields;
  std::stringstream stream(line);
  std::string field;
  while (std::getline(stream, field, ',')) fields.push_back(field);
  return fields;
}

double loadEpochMinusMono(const std::string &session) {
  std::ifstream file(session + "/d405_frames.csv");
  if (!file) throw std::runtime_error("Cannot open d405_frames.csv");
  std::string line;
  if (!std::getline(file, line)) throw std::runtime_error("Empty frames CSV");
  const auto header = splitCsv(line);
  const auto columns =
      vins_fusion_ros2::findFrameClockMappingColumns(header);
  std::vector<double> offsets;
  while (std::getline(file, line)) {
    const auto fields = splitCsv(line);
    if (fields.size() <= std::max(columns.device_ms, columns.monotonic_s) ||
        fields[columns.device_ms].empty() ||
        fields[columns.monotonic_s].empty())
      continue;
    offsets.push_back(std::stod(fields[columns.device_ms]) / 1000.0 -
                      std::stod(fields[columns.monotonic_s]));
  }
  if (offsets.empty()) throw std::runtime_error("No clock mapping samples");
  const auto middle = offsets.begin() + offsets.size() / 2;
  std::nth_element(offsets.begin(), middle, offsets.end());
  return *middle;
}

std::string findDb3(const std::string &session) {
  glob_t matches{};
  const std::string pattern = session + "/*.db3";
  const int result = glob(pattern.c_str(), 0, nullptr, &matches);
  if (result != 0 || matches.gl_pathc == 0) {
    globfree(&matches);
    throw std::runtime_error("No db3 found in session");
  }
  std::string path;
  off_t largest_size = 0;
  for (size_t index = 0; index < matches.gl_pathc; ++index) {
    struct stat info {};
    if (stat(matches.gl_pathv[index], &info) != 0 || !S_ISREG(info.st_mode) ||
        info.st_size <= largest_size)
      continue;
    largest_size = info.st_size;
    path = matches.gl_pathv[index];
  }
  globfree(&matches);
  if (path.empty()) throw std::runtime_error("No non-empty db3 found in session");
  return path;
}

builtin_interfaces::msg::Time toStamp(double seconds) {
  const int64_t nanoseconds = static_cast<int64_t>(std::llround(seconds * 1e9));
  builtin_interfaces::msg::Time stamp;
  stamp.sec = static_cast<int32_t>(nanoseconds / 1000000000LL);
  stamp.nanosec = static_cast<uint32_t>(nanoseconds % 1000000000LL);
  return stamp;
}

struct ImageEvent {
  double timestamp = 0.0;
  bool left = false;
  sensor_msgs::msg::Image image;
};

class ImageEventStream {
 public:
  explicit ImageEventStream(const std::string &db3) {
    rosbag2_storage::StorageOptions storage;
    storage.uri = db3;
    storage.storage_id = "sqlite3";
    reader_.open(storage);
    rosbag2_storage::StorageFilter filter;
    filter.topics = {kLeftData, kLeftMeta, kRightData, kRightMeta};
    reader_.set_filter(filter);
  }

  bool next(ImageEvent &event) {
    while (reader_.has_next()) {
      const auto bag_message = reader_.read_next();
      const bool left = bag_message->topic_name == kLeftData ||
                        bag_message->topic_name == kLeftMeta;
      const int index = left ? 0 : 1;
      rclcpp::SerializedMessage serialized(*bag_message->serialized_data);
      if (bag_message->topic_name == kLeftData ||
          bag_message->topic_name == kRightData) {
        sensor_msgs::msg::Image image;
        image_serialization_.deserialize_message(&serialized, &image);
        pending_images_[index] = std::move(image);
        have_image_[index] = true;
      } else {
        std_msgs::msg::String metadata;
        string_serialization_.deserialize_message(&serialized, &metadata);
        std::smatch match;
        if (!std::regex_search(metadata.data, match, timestamp_regex_)) continue;
        pending_timestamps_[index] = std::stod(match[1].str()) / 1000.0;
        have_timestamp_[index] = true;
      }
      if (have_image_[index] && have_timestamp_[index]) {
        event.timestamp = pending_timestamps_[index];
        event.left = left;
        event.image = std::move(pending_images_[index]);
        have_image_[index] = false;
        have_timestamp_[index] = false;
        return true;
      }
    }
    return false;
  }

 private:
  rosbag2_cpp::Reader reader_;
  rclcpp::Serialization<sensor_msgs::msg::Image> image_serialization_;
  rclcpp::Serialization<std_msgs::msg::String> string_serialization_;
  const std::regex timestamp_regex_{"timestamp=([0-9.]+)"};
  sensor_msgs::msg::Image pending_images_[2];
  double pending_timestamps_[2]{};
  bool have_image_[2]{};
  bool have_timestamp_[2]{};
};

#pragma pack(push, 1)
struct ImuRecord {
  double timestamp;
  uint32_t counter;
  float gx, gy, gz, ax, ay, az, temperature;
};
#pragma pack(pop)
static_assert(sizeof(ImuRecord) == 40, "Unexpected IMU record size");

class ImuEventStream {
 public:
  ImuEventStream(const std::string &path, double epoch_minus_mono,
                 double shift_ms)
      : file_(path, std::ios::binary), epoch_minus_mono_(epoch_minus_mono),
        shift_s_(shift_ms / 1000.0) {
    if (!file_) throw std::runtime_error("Cannot open external IMU file");
  }

  bool next(ImuRecord &record) {
    if (!file_.read(reinterpret_cast<char *>(&record), sizeof(record)))
      return false;
    record.timestamp += epoch_minus_mono_ + shift_s_;
    return true;
  }

 private:
  std::ifstream file_;
  double epoch_minus_mono_;
  double shift_s_;
};

std::array<double, 3> calibratedAccelerationG(const ImuRecord &record,
                                               const Arguments &args) {
  const std::array<double, 3> raw{{record.ax, record.ay, record.az}};
  std::array<double, 3> calibrated{};
  for (size_t row = 0; row < 3; ++row) {
    calibrated[row] = args.imu_accel_offset_g[row];
    for (size_t column = 0; column < 3; ++column) {
      calibrated[row] += args.imu_accel_matrix[row * 3 + column] * raw[column];
    }
  }
  return calibrated;
}

sensor_msgs::msg::Imu makeImuMessage(const ImuRecord &record,
                                     const Arguments &args) {
  constexpr double kDegToRad = 3.141592653589793 / 180.0;
  constexpr double kGravity = 9.80665;
  const double gx = record.gx * kDegToRad;
  const double gy = record.gy * kDegToRad;
  const double gz = record.gz * kDegToRad;
  const auto acceleration_g = calibratedAccelerationG(record, args);
  const double ax = acceleration_g[0] * kGravity;
  const double ay = acceleration_g[1] * kGravity;
  const double az = acceleration_g[2] * kGravity;
  sensor_msgs::msg::Imu message;
  message.header.stamp = toStamp(record.timestamp);
  message.header.frame_id = "imu0";
  message.angular_velocity.x = 0.99980212 * gx - 0.01423891 * gy - 0.01389161 * gz;
  message.angular_velocity.y = -0.01423891 * gx - 0.02458715 * gy - 0.99959628 * gz;
  message.angular_velocity.z = 0.01389161 * gx + 0.99959628 * gy - 0.02478503 * gz;
  message.linear_acceleration.x = 0.99980212 * ax - 0.01423891 * ay - 0.01389161 * az;
  message.linear_acceleration.y = -0.01423891 * ax - 0.02458715 * ay - 0.99959628 * az;
  message.linear_acceleration.z = 0.01389161 * ax + 0.99959628 * ay - 0.02478503 * az;
  return message;
}

}  // namespace

int main(int argc, char **argv) {
  try {
    const Arguments args = parseArguments(argc, argv);
    rclcpp::init(argc, argv);
    auto node = std::make_shared<rclcpp::Node>("db3_replay_cpp");
    auto image_qos = rclcpp::QoS(rclcpp::KeepLast(100)).reliable();
    auto cam0 = node->create_publisher<sensor_msgs::msg::Image>(
        "/cam0/image_raw", image_qos);
    auto cam1 = node->create_publisher<sensor_msgs::msg::Image>(
        "/cam1/image_raw", image_qos);
    auto imu = node->create_publisher<sensor_msgs::msg::Imu>(
        "/imu0", rclcpp::QoS(rclcpp::KeepLast(2000)).reliable());

    const double epoch_minus_mono = loadEpochMinusMono(args.session);
    const std::string image_db3 =
        args.image_db3.empty() ? findDb3(args.session) : args.image_db3;
    ImageEventStream images(image_db3);
    ImuEventStream imus(args.session + "/external_imu/imu.bin",
                        epoch_minus_mono, args.imu_shift_ms);
    ImageEvent image_event;
    ImuRecord imu_record{};
    bool have_image = images.next(image_event);
    bool have_imu = imus.next(imu_record);
    if (!have_image || !have_imu)
      throw std::runtime_error("Image or IMU stream is empty");

    const auto discovery_deadline =
        std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (std::chrono::steady_clock::now() < discovery_deadline &&
           (cam0->get_subscription_count() == 0 ||
            cam1->get_subscription_count() == 0 ||
            imu->get_subscription_count() == 0)) {
      rclcpp::spin_some(node);
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    RCLCPP_INFO(node->get_logger(), "DDS matches: cam0=%zu cam1=%zu imu=%zu",
                cam0->get_subscription_count(), cam1->get_subscription_count(),
                imu->get_subscription_count());
    RCLCPP_INFO(node->get_logger(), "IMU accelerometer calibration: %s",
                args.imu_accel_matrix_supplied ? "enabled" : "disabled");

    const double skip_end =
        std::min(image_event.timestamp, imu_record.timestamp) + args.skip_s;
    bool clock_started = false;
    double data_start = 0.0;
    std::chrono::steady_clock::time_point wall_start;
    auto wait_until = [&](double timestamp) {
      if (!clock_started) {
        clock_started = true;
        data_start = timestamp;
        wall_start = std::chrono::steady_clock::now();
      }
      const auto elapsed = std::chrono::duration<double>(
          (timestamp - data_start) / args.rate);
      std::this_thread::sleep_until(wall_start +
                                    std::chrono::duration_cast<
                                        std::chrono::steady_clock::duration>(elapsed));
    };

    uint64_t image_count = 0;
    uint64_t imu_count = 0;
    while (have_image && rclcpp::ok()) {
      while (have_imu && imu_record.timestamp <= image_event.timestamp) {
        if (imu_record.timestamp >= skip_end) {
          wait_until(imu_record.timestamp);
          imu->publish(makeImuMessage(imu_record, args));
          ++imu_count;
        }
        have_imu = imus.next(imu_record);
      }
      if (image_event.timestamp >= skip_end) {
        wait_until(image_event.timestamp);
        image_event.image.header.stamp = toStamp(image_event.timestamp);
        image_event.image.header.frame_id = image_event.left ? "cam0" : "cam1";
        image_event.image.encoding = "mono8";
        (image_event.left ? cam0 : cam1)->publish(std::move(image_event.image));
        ++image_count;
      }
      have_image = images.next(image_event);
      if (imu_count > 0 && imu_count % 4000 == 0) {
        RCLCPP_INFO(node->get_logger(), "progress images=%lu imu=%lu",
                    image_count, imu_count);
      }
    }
    std::this_thread::sleep_for(std::chrono::seconds(2));
    RCLCPP_INFO(node->get_logger(), "complete images=%lu imu=%lu",
                image_count, imu_count);
    rclcpp::shutdown();
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "[db3_replay_cpp] " << error.what() << std::endl;
    if (rclcpp::ok()) rclcpp::shutdown();
    return 1;
  }
}
