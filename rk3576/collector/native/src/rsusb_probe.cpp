#include <librealsense2/rs.hpp>

#include <iostream>
#include <stdexcept>
#include <string>

namespace {

std::string JsonEscape(const std::string& value) {
  std::string result;
  result.reserve(value.size());
  for (const unsigned char ch : value) {
    switch (ch) {
      case '\\': result += "\\\\"; break;
      case '"': result += "\\\""; break;
      case '\n': result += "\\n"; break;
      case '\r': result += "\\r"; break;
      case '\t': result += "\\t"; break;
      default:
        if (ch < 0x20) {
          throw std::runtime_error("device information contains a control byte");
        }
        result.push_back(static_cast<char>(ch));
    }
  }
  return result;
}

std::string Info(const rs2::device& device, rs2_camera_info key) {
  return device.supports(key) ? device.get_info(key) : "";
}

}  // namespace

int main() {
  try {
    rs2::context context;
    const rs2::device_list devices = context.query_devices();
    std::cout << "{\"backend\":\"librealsense_rsusb_native_cpp\",\"devices\":[";
    for (std::size_t index = 0; index < devices.size(); ++index) {
      if (index != 0) std::cout << ',';
      const rs2::device device = devices[index];
      std::cout << "{\"name\":\"" << JsonEscape(Info(device, RS2_CAMERA_INFO_NAME))
                << "\",\"sdk_serial\":\""
                << JsonEscape(Info(device, RS2_CAMERA_INFO_SERIAL_NUMBER))
                << "\",\"firmware\":\""
                << JsonEscape(Info(device, RS2_CAMERA_INFO_FIRMWARE_VERSION))
                << "\",\"usb_type\":\""
                << JsonEscape(Info(device, RS2_CAMERA_INFO_USB_TYPE_DESCRIPTOR))
                << "\"}";
    }
    std::cout << "]}\n";
    return devices.size() == 0 ? 2 : 0;
  } catch (const rs2::error& error) {
    std::cerr << "librealsense error in " << error.get_failed_function() << ": "
              << error.what() << '\n';
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
  }
  return 1;
}
