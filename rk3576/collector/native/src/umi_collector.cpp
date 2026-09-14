#include <librealsense2/rs.hpp>

#include <arpa/inet.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <ctime>
#include <cstdio>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <map>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace fs = std::filesystem;
using Clock = std::chrono::steady_clock;

namespace {

constexpr int kWidth = 1280;
constexpr int kHeight = 720;
constexpr int kFps = 30;
constexpr int kPreviewWidth = 1280;
constexpr int kPreviewHeight = 720;
constexpr int kPreviewFps = 15;
constexpr int kPreviewJpegQuality = 70;
constexpr std::size_t kColorBytes = kWidth * kHeight * 2ULL;
constexpr std::size_t kEyeBytes = kWidth * kHeight;
constexpr std::size_t kPacketBytes = 63;
constexpr int kRgbBitrate = 8'000'000;
constexpr int kIrBitrate = 3'456'000;
constexpr int kIrBitrateMin = 3'240'000;
constexpr int kIrBitrateMax = 3'672'000;
constexpr std::uint64_t kMinimumFreeBytes = 2ULL * 1024ULL * 1024ULL * 1024ULL;
constexpr const char* kSchema =
    "three-device-slam.rk3576-umi-rsusb-session.v4";

std::atomic<bool> g_stop_requested{false};

class Error final : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

std::uint64_t MonotonicNs() {
  return static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::nanoseconds>(
          Clock::now().time_since_epoch())
          .count());
}

std::string JsonEscape(const std::string& value) {
  std::ostringstream output;
  for (const unsigned char ch : value) {
    switch (ch) {
      case '\\': output << "\\\\"; break;
      case '"': output << "\\\""; break;
      case '\b': output << "\\b"; break;
      case '\f': output << "\\f"; break;
      case '\n': output << "\\n"; break;
      case '\r': output << "\\r"; break;
      case '\t': output << "\\t"; break;
      default:
        if (ch < 0x20) {
          output << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                 << static_cast<unsigned int>(ch) << std::dec;
        } else {
          output << static_cast<char>(ch);
        }
    }
  }
  return output.str();
}

std::string Quote(const std::string& value) {
  return "\"" + JsonEscape(value) + "\"";
}

std::string ReadText(const fs::path& path) {
  std::ifstream input(path);
  if (!input) throw Error("cannot read " + path.string());
  std::ostringstream value;
  value << input.rdbuf();
  std::string result = value.str();
  while (!result.empty() && (result.back() == '\n' || result.back() == '\r')) {
    result.pop_back();
  }
  return result;
}

void WriteAll(int descriptor, const std::uint8_t* data, std::size_t size) {
  while (size != 0) {
    const ssize_t written = write(descriptor, data, size);
    if (written > 0) {
      data += written;
      size -= static_cast<std::size_t>(written);
      continue;
    }
    if (written < 0 && errno == EINTR) continue;
    throw Error("write failed: " + std::string(std::strerror(errno)));
  }
}

void WriteAtomic(const fs::path& path, const std::string& contents) {
  const fs::path temporary = path.parent_path() / ("." + path.filename().string() + ".tmp");
  const int descriptor = open(temporary.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
  if (descriptor < 0) throw Error("cannot create " + temporary.string());
  try {
    WriteAll(descriptor,
             reinterpret_cast<const std::uint8_t*>(contents.data()),
             contents.size());
    if (fsync(descriptor) != 0) throw Error("fsync failed for " + temporary.string());
    if (close(descriptor) != 0) throw Error("close failed for " + temporary.string());
  } catch (...) {
    close(descriptor);
    throw;
  }
  fs::rename(temporary, path);
}

void FsyncDirectory(const fs::path& path) {
  const int descriptor = open(path.c_str(), O_RDONLY | O_DIRECTORY);
  if (descriptor < 0) throw Error("cannot open directory " + path.string());
  const int result = fsync(descriptor);
  close(descriptor);
  if (result != 0) throw Error("cannot fsync directory " + path.string());
}

std::string Sha256(const fs::path& path) {
  int output_pipe[2];
  if (pipe2(output_pipe, O_CLOEXEC) != 0) throw Error("cannot create hash pipe");
  const pid_t child = fork();
  if (child < 0) throw Error("cannot fork sha256sum");
  if (child == 0) {
    dup2(output_pipe[1], STDOUT_FILENO);
    close(output_pipe[0]);
    close(output_pipe[1]);
    execl("/usr/bin/sha256sum", "sha256sum", "--", path.c_str(), nullptr);
    _exit(127);
  }
  close(output_pipe[1]);
  std::string output;
  std::array<char, 256> block{};
  while (true) {
    const ssize_t bytes = read(output_pipe[0], block.data(), block.size());
    if (bytes > 0) output.append(block.data(), static_cast<std::size_t>(bytes));
    else if (bytes == 0) break;
    else if (errno != EINTR) throw Error("cannot read sha256sum output");
  }
  close(output_pipe[0]);
  int status = 0;
  if (waitpid(child, &status, 0) != child || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
    throw Error("sha256sum failed for " + path.string());
  }
  const std::string digest = output.substr(0, 64);
  if (digest.size() != 64 ||
      !std::all_of(digest.begin(), digest.end(), [](unsigned char ch) {
        return std::isxdigit(ch) != 0;
      })) {
    throw Error("invalid sha256sum output for " + path.string());
  }
  return digest;
}

std::string UtcStamp() {
  const std::time_t now = std::time(nullptr);
  std::tm utc{};
  if (gmtime_r(&now, &utc) == nullptr) throw Error("cannot format UTC time");
  std::array<char, 32> buffer{};
  if (std::strftime(buffer.data(), buffer.size(), "%Y%m%dT%H%M%SZ", &utc) == 0) {
    throw Error("cannot format UTC time");
  }
  return buffer.data();
}

std::string RandomSuffix() {
  std::string value = ReadText("/proc/sys/kernel/random/uuid");
  value.erase(std::remove(value.begin(), value.end(), '-'), value.end());
  if (value.size() < 8) throw Error("kernel UUID was too short");
  return value.substr(0, 8);
}

struct UsbSnapshot {
  std::string device;
  std::string speed;
};

UsbSnapshot VerifyUsb(const std::string& serial) {
  std::vector<UsbSnapshot> matches;
  for (const fs::directory_entry& entry : fs::directory_iterator("/sys/bus/usb/devices")) {
    const fs::path base = entry.path();
    if (!fs::exists(base / "serial")) continue;
    try {
      if (ReadText(base / "serial") != serial) continue;
      if (ReadText(base / "idVendor") != "8086" ||
          ReadText(base / "idProduct") != "0b5b") {
        throw Error("requested USB serial is not a D405");
      }
      const std::string speed = ReadText(base / "speed");
      if (std::stod(speed) < 5000.0) throw Error("D405 is not on SuperSpeed");
      matches.push_back({base.filename().string(), speed});
    } catch (const fs::filesystem_error&) {
      continue;
    }
  }
  if (matches.size() != 1) {
    throw Error("expected one D405 USB descriptor serial " + serial +
                ", found " + std::to_string(matches.size()));
  }
  return matches.front();
}

std::string DiscoverStm32(const std::optional<std::string>& requested) {
  if (requested) {
    if (!fs::exists(*requested)) throw Error("STM32 path does not exist: " + *requested);
    return fs::canonical(*requested).string();
  }
  std::vector<fs::path> matches;
  const fs::path root("/dev/serial/by-id");
  if (fs::exists(root)) {
    for (const fs::directory_entry& entry : fs::directory_iterator(root)) {
      if (entry.path().filename().string().find("CP2102N") != std::string::npos) {
        matches.push_back(entry.path());
      }
    }
  }
  if (matches.size() != 1) {
    throw Error("expected one CP2102N serial adapter, found " +
                std::to_string(matches.size()));
  }
  return fs::canonical(matches.front()).string();
}

class Encoder final {
 public:
  Encoder(std::vector<std::string> command, fs::path log_path,
          std::size_t frame_bytes, std::size_t queue_capacity)
      : command_(std::move(command)), log_path_(std::move(log_path)),
        frame_bytes_(frame_bytes), queue_capacity_(queue_capacity) {}

  Encoder(const Encoder&) = delete;
  Encoder& operator=(const Encoder&) = delete;

  ~Encoder() { Cancel(); }

  void Start() {
    if (started_) throw Error("encoder already started");
    int input_pipe[2];
    if (pipe2(input_pipe, O_CLOEXEC) != 0) throw Error("cannot create encoder pipe");
    const int log = open(log_path_.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (log < 0) throw Error("cannot create " + log_path_.string());
    child_ = fork();
    if (child_ < 0) throw Error("cannot fork GStreamer");
    if (child_ == 0) {
      setsid();
      dup2(input_pipe[0], STDIN_FILENO);
      dup2(log, STDOUT_FILENO);
      dup2(log, STDERR_FILENO);
      close(input_pipe[0]);
      close(input_pipe[1]);
      close(log);
      std::vector<char*> arguments;
      arguments.reserve(command_.size() + 1);
      for (std::string& item : command_) arguments.push_back(item.data());
      arguments.push_back(nullptr);
      execvp(arguments.front(), arguments.data());
      _exit(127);
    }
    close(input_pipe[0]);
    close(log);
    input_ = input_pipe[1];
    started_ = true;
    worker_ = std::thread(&Encoder::WriteLoop, this);
  }

  void Submit(const void* data, std::size_t size) {
    if (size != frame_bytes_) throw Error("encoder received invalid frame size");
    std::vector<std::uint8_t> copy(size);
    std::memcpy(copy.data(), data, size);
    std::lock_guard<std::mutex> lock(mutex_);
    if (!failure_.empty()) throw Error(failure_);
    if (!started_ || closing_) throw Error("encoder is not accepting frames");
    if (queue_.size() >= queue_capacity_) {
      ++queue_overflows_;
      throw Error("encoder queue overflow");
    }
    queue_.push_back(std::move(copy));
    maximum_queue_depth_ = std::max(maximum_queue_depth_, queue_.size());
    available_.notify_one();
  }

  void Finish() {
    if (!started_) return;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      closing_ = true;
      available_.notify_all();
    }
    if (worker_.joinable()) worker_.join();
    const int status = WaitForChild(std::chrono::seconds(45));
    started_ = false;
    if (!failure_.empty()) throw Error(failure_);
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
      throw Error("GStreamer encoder exited unsuccessfully");
    }
  }

  void Cancel() noexcept {
    if (!started_) return;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      closing_ = true;
      queue_.clear();
      available_.notify_all();
    }
    if (child_ > 0) kill(-child_, SIGKILL);
    if (input_ >= 0) {
      close(input_);
      input_ = -1;
    }
    if (worker_.joinable()) worker_.join();
    int status = 0;
    if (child_ > 0) waitpid(child_, &status, 0);
    child_ = -1;
    started_ = false;
  }

  std::uint64_t frames() const { return frames_; }
  std::uint64_t bytes() const { return bytes_; }
  std::uint64_t queue_overflows() const { return queue_overflows_; }
  std::size_t maximum_queue_depth() const { return maximum_queue_depth_; }

 private:
  void WriteLoop() noexcept {
    try {
      while (true) {
        std::vector<std::uint8_t> frame;
        {
          std::unique_lock<std::mutex> lock(mutex_);
          available_.wait(lock, [this] { return closing_ || !queue_.empty(); });
          if (queue_.empty()) {
            if (closing_) break;
            continue;
          }
          frame = std::move(queue_.front());
          queue_.pop_front();
        }
        WriteAll(input_, frame.data(), frame.size());
        ++frames_;
        bytes_ += frame.size();
      }
      if (input_ >= 0) {
        close(input_);
        input_ = -1;
      }
    } catch (const std::exception& error) {
      std::lock_guard<std::mutex> lock(mutex_);
      failure_ = std::string("encoder input failed: ") + error.what();
      if (input_ >= 0) {
        close(input_);
        input_ = -1;
      }
    }
  }

  int WaitForChild(std::chrono::seconds timeout) {
    const auto deadline = Clock::now() + timeout;
    int status = 0;
    while (Clock::now() < deadline) {
      const pid_t result = waitpid(child_, &status, WNOHANG);
      if (result == child_) {
        child_ = -1;
        return status;
      }
      if (result < 0 && errno != EINTR) throw Error("waitpid failed");
      std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    kill(-child_, SIGKILL);
    waitpid(child_, &status, 0);
    child_ = -1;
    throw Error("GStreamer encoder shutdown timed out");
  }

  std::vector<std::string> command_;
  fs::path log_path_;
  std::size_t frame_bytes_;
  std::size_t queue_capacity_;
  mutable std::mutex mutex_;
  std::condition_variable available_;
  std::deque<std::vector<std::uint8_t>> queue_;
  std::thread worker_;
  pid_t child_ = -1;
  int input_ = -1;
  bool started_ = false;
  bool closing_ = false;
  std::string failure_;
  std::uint64_t frames_ = 0;
  std::uint64_t bytes_ = 0;
  std::uint64_t queue_overflows_ = 0;
  std::size_t maximum_queue_depth_ = 0;
};

std::vector<std::string> IrCommand(const fs::path& output) {
  return {"gst-launch-1.0", "-q", "-e", "fdsrc", "fd=0", "!",
          "rawvideoparse", "format=gray8", "width=1280", "height=720",
          "framerate=30/1", "!", "videoconvert", "!",
          "video/x-raw,format=NV12", "!", "mpph265enc",
          "bps=3456000", "bps-min=3240000", "bps-max=3672000", "gop=30",
          "header-mode=each-idr", "max-reenc=0", "!", "filesink",
          "sync=false", "location=" + output.string()};
}

std::vector<std::string> RgbCommand(const fs::path& output,
                                    const std::optional<std::string>& preview_host,
                                    int preview_port,
                                    const std::optional<int>& preview_mjpeg_port) {
  std::vector<std::string> command{
      "gst-launch-1.0", "-q", "-e", "fdsrc", "fd=0", "!",
      "rawvideoparse", "format=yuy2", "width=1280", "height=720",
      "framerate=30/1", "!", "tee", "name=rgbtee", "rgbtee.", "!",
      "queue", "max-size-buffers=6", "max-size-bytes=0", "max-size-time=0",
      "!", "videoconvert", "!", "video/x-raw,format=NV12", "!",
      "mpph265enc", "bps=8000000", "gop=30", "header-mode=each-idr", "!",
      "filesink", "sync=false", "location=" + output.string()};
  if (preview_host) {
    const std::vector<std::string> preview{
        "rgbtee.", "!", "queue", "leaky=downstream", "max-size-buffers=2",
        "max-size-bytes=0", "max-size-time=0", "!", "videorate",
        "drop-only=true", "!", "video/x-raw,framerate=15/1", "!",
        "videoscale", "!", "video/x-raw,width=960,height=540", "!",
        "videoconvert", "!", "video/x-raw,format=NV12", "!", "mpph264enc",
        "bps=2000000", "gop=30", "header-mode=each-idr", "!",
        "video/x-h264,stream-format=byte-stream,alignment=au", "!",
        "rtph264pay", "pt=96", "config-interval=1", "mtu=1200", "!",
        "udpsink", "sync=false", "async=false", "host=" + *preview_host,
        "port=" + std::to_string(preview_port)};
    command.insert(command.end(), preview.begin(), preview.end());
  }
  if (preview_mjpeg_port) {
    const std::vector<std::string> preview{
        "rgbtee.", "!", "queue", "leaky=downstream", "max-size-buffers=2",
        "max-size-bytes=0", "max-size-time=0", "!", "videorate",
        "drop-only=true", "!", "video/x-raw,framerate=" +
            std::to_string(kPreviewFps) + "/1", "!", "videoconvert", "!",
        "video/x-raw,format=I420,width=" + std::to_string(kPreviewWidth) +
            ",height=" + std::to_string(kPreviewHeight), "!", "jpegenc",
        "quality=" + std::to_string(kPreviewJpegQuality), "!", "multipartmux",
        "boundary=frame", "!", "tcpserversink", "host=127.0.0.1",
        "port=" + std::to_string(*preview_mjpeg_port), "sync=false",
        "async=false"};
    command.insert(command.end(), preview.begin(), preview.end());
  }
  return command;
}

std::vector<std::string> PreviewOnlyCommand(int port) {
  return {"gst-launch-1.0", "-q", "-e", "fdsrc", "fd=0", "!",
          "rawvideoparse", "format=yuy2", "width=1280", "height=720",
          "framerate=30/1", "!", "videoconvert", "!",
          "video/x-raw,format=I420,width=" + std::to_string(kPreviewWidth) +
              ",height=" + std::to_string(kPreviewHeight),
          "!", "jpegenc", "quality=" + std::to_string(kPreviewJpegQuality), "!",
          "multipartmux", "boundary=frame", "!",
          "tcpserversink", "host=127.0.0.1", "port=" + std::to_string(port),
          "sync=false", "async=false"};
}

std::uint16_t Load16(const std::uint8_t* data) {
  return static_cast<std::uint16_t>(data[0]) |
         (static_cast<std::uint16_t>(data[1]) << 8U);
}

std::uint32_t Load32(const std::uint8_t* data) {
  return static_cast<std::uint32_t>(data[0]) |
         (static_cast<std::uint32_t>(data[1]) << 8U) |
         (static_cast<std::uint32_t>(data[2]) << 16U) |
         (static_cast<std::uint32_t>(data[3]) << 24U);
}

std::uint16_t Crc16(const std::uint8_t* data, std::size_t size) {
  std::uint16_t crc = 0xffffU;
  for (std::size_t i = 0; i < size; ++i) {
    crc ^= static_cast<std::uint16_t>(data[i]) << 8U;
    for (int bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000U) != 0
                ? static_cast<std::uint16_t>((crc << 1U) ^ 0x1021U)
                : static_cast<std::uint16_t>(crc << 1U);
    }
  }
  return crc;
}

struct SerialMetrics {
  std::uint64_t packets = 0;
  double rate = 0.0;
  std::uint64_t crc_errors = 0;
  std::uint64_t discarded_bytes = 0;
  std::uint64_t sequence_gaps = 0;
  std::uint64_t sequence_regressions = 0;
  std::uint64_t invalid_imu_flags = 0;
  std::uint64_t invalid_encoder_flags = 0;
};

class SerialCollector final {
 public:
  SerialCollector(std::string port, fs::path payload, fs::path index)
      : port_(std::move(port)), payload_(std::move(payload)), index_(std::move(index)) {}

  ~SerialCollector() { StopNoThrow(); }

  void Start() {
    thread_ = std::thread(&SerialCollector::Loop, this);
    std::unique_lock<std::mutex> lock(mutex_);
    if (!ready_cv_.wait_for(lock, std::chrono::seconds(3), [this] { return ready_; })) {
      throw Error("STM32 collector did not become ready");
    }
    if (!failure_.empty()) throw Error(failure_);
  }

  void Begin() { recording_.store(true); }

  void Stop() {
    stop_.store(true);
    if (thread_.joinable()) thread_.join();
    std::lock_guard<std::mutex> lock(mutex_);
    if (!failure_.empty()) throw Error(failure_);
  }

  SerialMetrics metrics() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return metrics_;
  }

 private:
  static void Configure(int descriptor) {
    termios config{};
    if (tcgetattr(descriptor, &config) != 0) throw Error("tcgetattr failed");
    cfmakeraw(&config);
    config.c_iflag = IGNPAR;
    config.c_cflag = B921600 | CS8 | CREAD | CLOCAL;
    config.c_cc[VMIN] = 0;
    config.c_cc[VTIME] = 1;
    cfsetispeed(&config, B921600);
    cfsetospeed(&config, B921600);
    if (tcsetattr(descriptor, TCSANOW, &config) != 0) throw Error("tcsetattr failed");
    tcflush(descriptor, TCIFLUSH);
  }

  static FILE* OpenExclusive(const fs::path& path, const char* mode) {
    const int descriptor = open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (descriptor < 0) throw Error("cannot create " + path.string());
    FILE* stream = fdopen(descriptor, mode);
    if (stream == nullptr) {
      close(descriptor);
      throw Error("fdopen failed for " + path.string());
    }
    return stream;
  }

  void SignalReady(const std::string& failure = {}) {
    std::lock_guard<std::mutex> lock(mutex_);
    failure_ = failure;
    ready_ = true;
    ready_cv_.notify_all();
  }

  void Loop() noexcept {
    int descriptor = -1;
    FILE* payload = nullptr;
    FILE* index = nullptr;
    try {
      descriptor = open(port_.c_str(), O_RDONLY | O_NOCTTY | O_NONBLOCK);
      if (descriptor < 0) throw Error("cannot open STM32 port " + port_);
      Configure(descriptor);
      const auto warmup_end = Clock::now() + std::chrono::milliseconds(750);
      std::array<std::uint8_t, 4096> input{};
      while (Clock::now() < warmup_end) {
        pollfd event{descriptor, POLLIN, 0};
        const int ready = poll(&event, 1, 50);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) throw Error("STM32 warmup poll failed");
        if (ready > 0 && (event.revents & POLLIN) != 0) {
          const ssize_t discarded = read(descriptor, input.data(), input.size());
          if (discarded < 0 && errno != EAGAIN && errno != EINTR) {
            throw Error("STM32 warmup read failed");
          }
        }
      }
      tcflush(descriptor, TCIFLUSH);
      payload = OpenExclusive(payload_, "wb");
      index = OpenExclusive(index_, "w");
      SignalReady();
      std::vector<std::uint8_t> buffer;
      buffer.reserve(8192);
      std::optional<std::uint32_t> previous_sequence;
      std::optional<std::uint64_t> first_rx;
      std::optional<std::uint64_t> last_rx;
      while (!stop_.load()) {
        pollfd event{descriptor, POLLIN, 0};
        const int ready = poll(&event, 1, 50);
        if (ready < 0 && errno == EINTR) continue;
        if (ready < 0) throw Error("STM32 poll failed");
        if (ready == 0 || (event.revents & POLLIN) == 0) continue;
        const ssize_t count = read(descriptor, input.data(), input.size());
        if (count < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        if (count < 0) throw Error("STM32 read failed");
        if (count == 0) continue;
        const std::uint64_t rx_ns = MonotonicNs();
        buffer.insert(buffer.end(), input.begin(), input.begin() + count);
        while (true) {
          auto header = std::find(buffer.begin(), buffer.end(), 0xa5U);
          while (header != buffer.end() &&
                 (std::next(header) == buffer.end() || *std::next(header) != 0x5aU)) {
            header = std::find(std::next(header), buffer.end(), 0xa5U);
          }
          if (header == buffer.end()) {
            if (buffer.size() > 1) {
              std::lock_guard<std::mutex> lock(mutex_);
              metrics_.discarded_bytes += buffer.size() - 1;
              buffer.erase(buffer.begin(), buffer.end() - 1);
            }
            break;
          }
          const std::size_t skipped = static_cast<std::size_t>(header - buffer.begin());
          if (skipped != 0) {
            std::lock_guard<std::mutex> lock(mutex_);
            metrics_.discarded_bytes += skipped;
            buffer.erase(buffer.begin(), header);
          }
          if (buffer.size() < kPacketBytes) break;
          const std::uint8_t* packet = buffer.data();
          const bool envelope = packet[2] == 1 && packet[3] == kPacketBytes;
          unsigned int raw_sum = 0;
          for (std::size_t i = 24; i < 60; ++i) raw_sum += packet[i];
          const bool valid = envelope && Crc16(packet, 61) == Load16(packet + 61) &&
                             packet[24] == 0xeb && packet[25] == 0x90 &&
                             packet[26] == 0x22 &&
                             static_cast<std::uint8_t>(raw_sum) == packet[60] &&
                             Load32(packet + 18) == Load32(packet + 56);
          if (!valid) {
            std::lock_guard<std::mutex> lock(mutex_);
            ++metrics_.crc_errors;
            const std::size_t discarded = envelope ? kPacketBytes : 1;
            metrics_.discarded_bytes += discarded;
            buffer.erase(buffer.begin(), buffer.begin() + discarded);
            continue;
          }
          if (recording_.load()) {
            const std::uint16_t flags = Load16(packet + 4);
            const std::uint32_t sequence = Load32(packet + 6);
            const std::uint32_t imu_us = Load32(packet + 10);
            const std::uint32_t encoder_us = Load32(packet + 14);
            const std::uint32_t counter = Load32(packet + 18);
            const std::uint16_t encoder_response = Load16(packet + 22);
            {
              std::lock_guard<std::mutex> lock(mutex_);
              if (previous_sequence) {
                const std::uint32_t delta = sequence - *previous_sequence;
                if (delta == 0 || delta > 0x7fffffffU) ++metrics_.sequence_regressions;
                else if (delta != 1) metrics_.sequence_gaps += delta - 1;
              }
              previous_sequence = sequence;
              if ((flags & 0x01U) == 0) ++metrics_.invalid_imu_flags;
              if ((flags & 0x02U) == 0) ++metrics_.invalid_encoder_flags;
              const std::uint64_t record_index = metrics_.packets++;
              if (!first_rx) first_rx = rx_ns;
              last_rx = rx_ns;
              if (std::fwrite(packet, 1, kPacketBytes, payload) != kPacketBytes) {
                throw Error("STM32 payload write failed");
              }
              std::ostringstream row;
              row << "{\"record_index\":" << record_index
                  << ",\"offset\":" << record_index * kPacketBytes
                  << ",\"size\":" << kPacketBytes
                  << ",\"host_read_complete_monotonic_ns\":" << rx_ns
                  << ",\"sequence\":" << sequence << ",\"flags\":" << flags
                  << ",\"imu_time_us\":" << imu_us
                  << ",\"encoder_time_us\":" << encoder_us
                  << ",\"imu_counter\":" << counter
                  << ",\"encoder_response\":" << encoder_response << "}\n";
              const std::string text = row.str();
              if (std::fwrite(text.data(), 1, text.size(), index) != text.size()) {
                throw Error("STM32 index write failed");
              }
            }
          }
          buffer.erase(buffer.begin(), buffer.begin() + kPacketBytes);
        }
      }
      if (std::fflush(payload) != 0 || fsync(fileno(payload)) != 0 ||
          std::fflush(index) != 0 || fsync(fileno(index)) != 0) {
        throw Error("STM32 output fsync failed");
      }
      if (first_rx && last_rx && *last_rx > *first_rx) {
        std::lock_guard<std::mutex> lock(mutex_);
        metrics_.rate = (metrics_.packets - 1) * 1e9 /
                        static_cast<double>(*last_rx - *first_rx);
      }
      std::fclose(payload); payload = nullptr;
      std::fclose(index); index = nullptr;
      close(descriptor); descriptor = -1;
    } catch (const std::exception& error) {
      if (payload != nullptr) std::fclose(payload);
      if (index != nullptr) std::fclose(index);
      if (descriptor >= 0) close(descriptor);
      SignalReady(std::string("STM32 collector failed: ") + error.what());
    }
  }

  void StopNoThrow() noexcept {
    stop_.store(true);
    if (thread_.joinable()) thread_.join();
  }

  std::string port_;
  fs::path payload_;
  fs::path index_;
  std::atomic<bool> recording_{false};
  std::atomic<bool> stop_{false};
  mutable std::mutex mutex_;
  std::condition_variable ready_cv_;
  bool ready_ = false;
  std::string failure_;
  SerialMetrics metrics_;
  std::thread thread_;
};

struct Continuity {
  explicit Continuity(std::size_t bytes) : expected_bytes(bytes) {}

  std::size_t expected_bytes = 0;
  std::uint64_t received = 0;
  std::optional<std::uint64_t> first_sequence;
  std::optional<std::uint64_t> last_sequence;
  std::optional<double> first_timestamp_ms;
  std::optional<double> last_timestamp_ms;
  std::optional<std::uint64_t> last_arrival_ns;
  std::uint64_t sequence_gaps = 0;
  std::uint64_t repeated_sequences = 0;
  std::uint64_t sequence_regressions = 0;
  std::uint64_t timestamp_regressions = 0;
  std::uint64_t payload_size_errors = 0;
  double maximum_arrival_ms = 0.0;

  void Observe(std::uint64_t sequence, double timestamp_ms,
               std::uint64_t arrival_ns, std::size_t bytes) {
    if (last_sequence) {
      if (sequence > *last_sequence + 1) sequence_gaps += sequence - *last_sequence - 1;
      else if (sequence == *last_sequence) ++repeated_sequences;
      else if (sequence < *last_sequence) ++sequence_regressions;
    }
    if (last_timestamp_ms && timestamp_ms <= *last_timestamp_ms) ++timestamp_regressions;
    if (last_arrival_ns) {
      maximum_arrival_ms = std::max(
          maximum_arrival_ms, (arrival_ns - *last_arrival_ns) / 1e6);
    }
    if (bytes != expected_bytes) ++payload_size_errors;
    if (!first_sequence) {
      first_sequence = sequence;
      first_timestamp_ms = timestamp_ms;
    }
    ++received;
    last_sequence = sequence;
    last_timestamp_ms = timestamp_ms;
    last_arrival_ns = arrival_ns;
  }

  double TimestampSpan() const {
    return first_timestamp_ms && last_timestamp_ms
               ? (*last_timestamp_ms - *first_timestamp_ms) / 1000.0
               : 0.0;
  }

  double Rate() const {
    const double span = TimestampSpan();
    return span > 0.0 && received > 1 ? (received - 1) / span : 0.0;
  }

  bool Healthy() const {
    const double rate = Rate();
    return received > 1 && rate >= 29.0 && rate <= 31.0 && sequence_gaps == 0 &&
           repeated_sequences == 0 && sequence_regressions == 0 &&
           timestamp_regressions == 0 && payload_size_errors == 0 &&
           maximum_arrival_ms <= 250.0;
  }
};

std::string ContinuityJson(const Continuity& item) {
  std::ostringstream output;
  output << std::setprecision(17)
         << "{\"received\":" << item.received
         << ",\"first_sequence\":" << item.first_sequence.value_or(0)
         << ",\"last_sequence\":" << item.last_sequence.value_or(0)
         << ",\"timestamp_span_s\":" << item.TimestampSpan()
         << ",\"observed_rate_hz\":" << item.Rate()
         << ",\"sequence_gaps\":" << item.sequence_gaps
         << ",\"repeated_sequences\":" << item.repeated_sequences
         << ",\"sequence_regressions\":" << item.sequence_regressions
         << ",\"timestamp_regressions\":" << item.timestamp_regressions
         << ",\"payload_size_errors\":" << item.payload_size_errors
         << ",\"max_arrival_interval_ms\":" << item.maximum_arrival_ms << '}';
  return output.str();
}

enum class StreamKind { kUnknown, kColor, kLeft, kRight };

StreamKind Kind(const rs2::frame& frame) {
  const rs2::stream_profile profile = frame.get_profile();
  const rs2_stream stream = profile.stream_type();
  const int index = profile.stream_index();
  if (stream == RS2_STREAM_COLOR) return StreamKind::kColor;
  if (stream == RS2_STREAM_INFRARED && index == 1) return StreamKind::kLeft;
  if (stream == RS2_STREAM_INFRARED && index == 2) return StreamKind::kRight;
  return StreamKind::kUnknown;
}

struct EyeFrame {
  std::vector<std::uint8_t> data;
  double timestamp_ms = 0.0;
  std::uint64_t arrival_ns = 0;
};

struct PendingPair {
  std::optional<EyeFrame> left;
  std::optional<EyeFrame> right;
};

struct Options {
  fs::path output_root;
  int duration = 10;
  bool until_signal = false;
  bool preview_only = false;
  std::string sdk_serial;
  std::string usb_serial;
  std::optional<std::string> stm32_port;
  std::optional<std::string> preview_host;
  int preview_port = 5004;
  std::optional<int> preview_mjpeg_port;
};

Options Parse(int argc, char** argv) {
  Options options;
  for (int index = 1; index < argc; ++index) {
    const std::string key = argv[index];
    auto value = [&]() -> std::string {
      if (++index >= argc) throw Error("missing value for " + key);
      return argv[index];
    };
    if (key == "--output-root") options.output_root = value();
    else if (key == "--duration") options.duration = std::stoi(value());
    else if (key == "--until-signal") options.until_signal = true;
    else if (key == "--preview-only") options.preview_only = true;
    else if (key == "--d405-sdk-serial") options.sdk_serial = value();
    else if (key == "--d405-usb-serial") options.usb_serial = value();
    else if (key == "--stm32-port") options.stm32_port = value();
    else if (key == "--preview-rtp-host") options.preview_host = value();
    else if (key == "--preview-rtp-port") options.preview_port = std::stoi(value());
    else if (key == "--preview-mjpeg-port") {
      options.preview_mjpeg_port = std::stoi(value());
    }
    else if (key == "--ir-encoding") {
      if (value() != "y8_split_h265") throw Error("native collector supports y8_split_h265 only");
    } else {
      throw Error("unknown argument: " + key);
    }
  }
  if (options.output_root.empty() || options.sdk_serial.empty() ||
      options.usb_serial.empty()) {
    throw Error("--output-root and both D405 serials are required");
  }
  if (options.duration < 1 || options.duration > 86400) {
    throw Error("duration must be between 1 and 86400 seconds");
  }
  if (options.preview_port < 1 || options.preview_port > 65535) {
    throw Error("preview RTP port is invalid");
  }
  if (options.preview_host) {
    in_addr address{};
    if (inet_pton(AF_INET, options.preview_host->c_str(), &address) != 1) {
      throw Error("preview RTP host must be an IPv4 address");
    }
  }
  if (options.preview_mjpeg_port &&
      (*options.preview_mjpeg_port < 1 || *options.preview_mjpeg_port > 65535)) {
    throw Error("preview MJPEG port is invalid");
  }
  if (options.preview_only && !options.preview_mjpeg_port) {
    throw Error("--preview-only requires --preview-mjpeg-port");
  }
  return options;
}

std::string DeviceInfo(const rs2::device& device, rs2_camera_info key) {
  return device.supports(key) ? device.get_info(key) : "";
}

std::string BuildProfileJson() {
  std::ostringstream output;
  output << "{\"infrared\":{\"encoding\":\"y8_split_h265\","
         << "\"layout\":\"separate_left_right_files\",\"width\":" << kWidth
         << ",\"height\":" << kHeight << ",\"fps\":" << kFps
         << ",\"frame_bytes\":" << kEyeBytes * 2
         << ",\"frame_bytes_per_eye\":" << kEyeBytes
         << ",\"files\":{\"left\":\"infrared-left-y8.h265\","
         << "\"right\":\"infrared-right-y8.h265\"},"
         << "\"semantics\":\"lossy_stereo_slam_candidate\","
         << "\"compression\":{\"codec\":\"h265\",\"container\":\"annex_b\","
         << "\"encoder\":\"rockchip_mpp\",\"rate_control\":\"cbr\","
         << "\"target_bitrate_per_eye\":" << kIrBitrate
         << ",\"minimum_bitrate_per_eye\":" << kIrBitrateMin
         << ",\"maximum_bitrate_per_eye\":" << kIrBitrateMax
         << ",\"gop_frames\":" << kFps
         << ",\"pixel_format\":\"yuv420p_nv12\",\"source_encoding\":\"y8\","
         << "\"content\":\"one_access_unit_per_complete_y8_frame_per_eye\"}},"
         << "\"rgb\":{\"encoding\":\"h265_annex_b\",\"source_encoding\":\"yuyv_rsusb\","
         << "\"width\":" << kWidth << ",\"height\":" << kHeight
         << ",\"fps\":" << kFps << ",\"target_bitrate\":" << kRgbBitrate
         << ",\"semantics\":\"lossy_preview_and_training_image\"},"
         << "\"stm32\":{\"encoding\":\"stm32_combined_v1_raw\","
         << "\"packet_bytes\":" << kPacketBytes << ",\"baud\":921600,"
         << "\"nominal_rate_hz\":400,\"semantics\":\"lossless_wire_packet\"}}";
  return output.str();
}

std::string FileClaims(const fs::path& root) {
  std::vector<fs::path> files;
  for (const fs::directory_entry& entry : fs::directory_iterator(root)) {
    if (!entry.is_regular_file()) continue;
    const std::string name = entry.path().filename().string();
    if (name == ".recording" || name == "manifest.json") continue;
    files.push_back(entry.path());
  }
  std::sort(files.begin(), files.end());
  std::ostringstream output;
  output << '{';
  for (std::size_t index = 0; index < files.size(); ++index) {
    if (index != 0) output << ',';
    output << Quote(files[index].filename().string()) << ":{\"size\":"
           << fs::file_size(files[index]) << ",\"sha256\":"
           << Quote(Sha256(files[index])) << '}';
  }
  output << '}';
  return output.str();
}

std::string Capture(const Options& options) {
  const UsbSnapshot usb = VerifyUsb(options.usb_serial);
  const std::string stm32_path = DiscoverStm32(options.stm32_port);
  fs::create_directories(options.output_root);
  struct statvfs storage{};
  if (statvfs(options.output_root.c_str(), &storage) != 0) throw Error("statvfs failed");
  const std::uint64_t free_bytes = static_cast<std::uint64_t>(storage.f_bavail) * storage.f_frsize;
  if (free_bytes < kMinimumFreeBytes) throw Error("less than 2 GiB free space remains");

  rs2::context context;
  std::vector<rs2::device> matches;
  for (rs2::device&& candidate : context.query_devices()) {
    if (DeviceInfo(candidate, RS2_CAMERA_INFO_SERIAL_NUMBER) == options.sdk_serial) {
      matches.push_back(candidate);
    }
  }
  if (matches.size() != 1) throw Error("expected exactly one requested RSUSB D405");
  rs2::device device = matches.front();
  const std::string model = DeviceInfo(device, RS2_CAMERA_INFO_NAME);
  if (model.find("D405") == std::string::npos) throw Error("requested SDK device is not D405");
  const std::string firmware = DeviceInfo(device, RS2_CAMERA_INFO_FIRMWARE_VERSION);
  const std::string usb_type = DeviceInfo(device, RS2_CAMERA_INFO_USB_TYPE_DESCRIPTOR);
  rs2::depth_sensor sensor = device.first<rs2::depth_sensor>();
  std::map<StreamKind, rs2::stream_profile> selected;
  for (const rs2::stream_profile& profile : sensor.get_stream_profiles()) {
    const rs2::video_stream_profile video = profile.as<rs2::video_stream_profile>();
    if (!video || video.width() != kWidth || video.height() != kHeight ||
        profile.fps() != kFps) continue;
    if (profile.stream_type() == RS2_STREAM_COLOR && profile.format() == RS2_FORMAT_YUYV) {
      selected[StreamKind::kColor] = profile;
    } else if (profile.stream_type() == RS2_STREAM_INFRARED &&
               profile.format() == RS2_FORMAT_Y8 && profile.stream_index() == 1) {
      selected[StreamKind::kLeft] = profile;
    } else if (profile.stream_type() == RS2_STREAM_INFRARED &&
               profile.format() == RS2_FORMAT_Y8 && profile.stream_index() == 2) {
      selected[StreamKind::kRight] = profile;
    }
  }
  if (selected.size() != 3) throw Error("D405 1280x720@30 profiles are incomplete");
  std::vector<rs2::stream_profile> profiles{
      selected.at(StreamKind::kColor), selected.at(StreamKind::kLeft),
      selected.at(StreamKind::kRight)};

  const std::string session_id = "rk3576-rsusb-cpp-" + UtcStamp() + '-' + RandomSuffix();
  const fs::path partial = options.output_root / ("." + session_id + ".partial");
  const fs::path final = options.output_root / session_id;
  if (!fs::create_directory(partial)) throw Error("cannot create session directory");
  fs::permissions(partial, fs::perms::owner_all, fs::perm_options::replace);
  WriteAtomic(partial / ".recording", "unsealed\n");

  const fs::path left_partial = partial / "infrared-left-y8.h265.partial";
  const fs::path right_partial = partial / "infrared-right-y8.h265.partial";
  const fs::path rgb_partial = partial / "rgb.h265.partial";
  Encoder left_encoder(IrCommand(left_partial), partial / "ir-left-gstreamer.log",
                       kEyeBytes, 16);
  Encoder right_encoder(IrCommand(right_partial), partial / "ir-right-gstreamer.log",
                        kEyeBytes, 16);
  Encoder rgb_encoder(RgbCommand(rgb_partial, options.preview_host, options.preview_port,
                                 options.preview_mjpeg_port),
                      partial / "rgb-gstreamer.log", kColorBytes, 16);
  SerialCollector serial(stm32_path, partial / "stm32.bin.partial",
                         partial / "stm32_packets.jsonl");
  std::ofstream ir_index(partial / "ir_frames.jsonl", std::ios::out | std::ios::trunc);
  std::ofstream rgb_index(partial / "rgb_frames.jsonl", std::ios::out | std::ios::trunc);
  if (!ir_index || !rgb_index) throw Error("cannot create frame indexes");

  const std::string profile_json = BuildProfileJson();
  std::ostringstream device_json_builder;
  device_json_builder << "{\"d405_serial\":" << Quote(options.sdk_serial)
                      << ",\"d405_sdk_serial\":" << Quote(options.sdk_serial)
                      << ",\"d405_usb_descriptor_serial\":" << Quote(options.usb_serial)
                      << ",\"d405_usb\":{\"usb_device\":" << Quote(usb.device)
                      << ",\"vidpid\":\"8086:0b5b\",\"speed_mbit_s\":"
                      << Quote(usb.speed) << "},\"d405_rsusb\":{\"sdk_serial\":"
                      << Quote(options.sdk_serial) << ",\"name\":" << Quote(model)
                      << ",\"firmware\":" << Quote(firmware) << ",\"usb_type\":"
                      << Quote(usb_type) << "},\"rsusb_module\":{\"backend\":"
                      << "\"librealsense_rsusb_native_cpp\"},\"stm32_port\":"
                      << Quote(stm32_path) << '}';
  const std::string device_json = device_json_builder.str();
  std::ostringstream config;
  config << "{\"schema\":" << Quote(kSchema) << ",\"session_id\":"
         << Quote(session_id) << ",\"capture_host\":{\"producer\":"
         << "\"umi_collector_cpp17\"},\"device\":" << device_json
         << ",\"profile\":" << profile_json << ",\"requested\":";
  if (options.until_signal) {
    config << "{\"mode\":\"until_signal\",\"maximum_duration_s\":"
           << options.duration << '}';
  } else {
    config << "{\"mode\":\"fixed_frames\",\"duration_s\":" << options.duration
           << ",\"frames\":" << options.duration * kFps << '}';
  }
  if (options.preview_host) {
    config << ",\"preview\":{\"transport\":\"rtp_h264\",\"host\":"
           << Quote(*options.preview_host) << ",\"port\":" << options.preview_port
           << ",\"payload_type\":96,\"codec\":\"H264\",\"width\":960,"
           << "\"height\":540,\"fps\":15,\"bitrate\":2000000,"
           << "\"semantics\":\"webrtc_ingress_candidate\"}";
  }
  if (options.preview_mjpeg_port) {
    config << ",\"app_preview\":{\"transport\":\"tcp_multipart_mjpeg\","
           << "\"listen\":\"127.0.0.1\",\"port\":"
           << *options.preview_mjpeg_port
           << ",\"codec\":\"MJPEG\",\"width\":" << kPreviewWidth
           << ",\"height\":" << kPreviewHeight << ",\"fps\":"
           << kPreviewFps << ",\"semantics\":\"rgb_preview_only\"}";
  }
  config << '}';
  WriteAtomic(partial / "session_config.json", config.str() + "\n");

  rs2::frame_queue frame_queue(128, false);
  bool sensor_open = false;
  bool sensor_started = false;
  try {
    serial.Start();
    left_encoder.Start();
    right_encoder.Start();
    rgb_encoder.Start();
    sensor.open(profiles);
    sensor_open = true;
    sensor.start(frame_queue);
    sensor_started = true;
    std::map<StreamKind, int> warmup{{StreamKind::kColor, 0},
                                     {StreamKind::kLeft, 0},
                                     {StreamKind::kRight, 0}};
    const auto warmup_deadline = Clock::now() + std::chrono::seconds(3);
    while (Clock::now() < warmup_deadline) {
      rs2::frame frame = frame_queue.wait_for_frame(1000);
      const StreamKind kind = Kind(frame);
      if (kind != StreamKind::kUnknown) {
        static_cast<void>(frame.get_data());
        ++warmup[kind];
      }
    }
    rs2::frame stale;
    while (frame_queue.poll_for_frame(&stale)) {
      const StreamKind kind = Kind(stale);
      if (kind != StreamKind::kUnknown) ++warmup[kind];
    }
    if (warmup[StreamKind::kColor] < kFps || warmup[StreamKind::kLeft] < kFps ||
        warmup[StreamKind::kRight] < kFps) {
      throw Error("D405 warmup did not receive all three streams");
    }

    std::map<StreamKind, Continuity> streams{
        {StreamKind::kColor, Continuity{kColorBytes}},
        {StreamKind::kLeft, Continuity{kEyeBytes}},
        {StreamKind::kRight, Continuity{kEyeBytes}}};
    std::map<std::uint64_t, PendingPair> pending;
    std::uint64_t ir_rows = 0;
    std::uint64_t rgb_rows = 0;
    std::optional<double> first_rgb_timestamp;
    const std::uint64_t maximum_frames = options.duration * kFps;
    std::optional<std::uint64_t> capture_target =
        options.until_signal ? std::nullopt : std::optional<std::uint64_t>(maximum_frames);
    std::string termination_reason = options.until_signal
                                         ? "maximum_duration_complete"
                                         : "fixed_duration_complete";
    std::optional<std::uint64_t> stop_observed_ns;
    serial.Begin();
    const std::uint64_t formal_start_ns = MonotonicNs();
    const auto bounded_deadline = Clock::now() + std::chrono::seconds(options.duration + 15);
    auto next_storage_check = Clock::now() + std::chrono::seconds(1);
    while (true) {
      if (options.until_signal && !capture_target && g_stop_requested.load()) {
        stop_observed_ns = MonotonicNs();
        const std::uint64_t current = std::max(
            {streams.at(StreamKind::kColor).received,
             streams.at(StreamKind::kLeft).received,
             streams.at(StreamKind::kRight).received, std::uint64_t{2}});
        capture_target = current;
        termination_reason = "external_stop";
      }
      if (options.until_signal && !capture_target &&
          streams.at(StreamKind::kColor).received >= maximum_frames &&
          streams.at(StreamKind::kLeft).received >= maximum_frames &&
          streams.at(StreamKind::kRight).received >= maximum_frames) {
        capture_target = maximum_frames;
      }
      if (capture_target && streams.at(StreamKind::kColor).received >= *capture_target &&
          streams.at(StreamKind::kLeft).received >= *capture_target &&
          streams.at(StreamKind::kRight).received >= *capture_target) break;
      if (Clock::now() >= next_storage_check) {
        struct statvfs current_storage {};
        if (statvfs(options.output_root.c_str(), &current_storage) != 0) {
          throw Error("storage guard statvfs failed");
        }
        const std::uint64_t current_free =
            static_cast<std::uint64_t>(current_storage.f_bavail) *
            current_storage.f_frsize;
        if (current_free < kMinimumFreeBytes) {
          const std::uint64_t current = std::max(
              {streams.at(StreamKind::kColor).received,
               streams.at(StreamKind::kLeft).received,
               streams.at(StreamKind::kRight).received, std::uint64_t{2}});
          if (!capture_target || *capture_target > current) {
            capture_target = current;
            termination_reason = "storage_low_water";
          }
        }
        next_storage_check = Clock::now() + std::chrono::seconds(1);
      }
      if (Clock::now() >= bounded_deadline) throw Error("capture exceeded bounded deadline");
      rs2::frame frame = frame_queue.wait_for_frame(1000);
      const StreamKind kind = Kind(frame);
      if (kind == StreamKind::kUnknown) continue;
      const std::uint64_t limit = capture_target.value_or(maximum_frames);
      if (streams.at(kind).received >= limit) continue;
      const std::size_t size = frame.get_data_size();
      const std::uint64_t sequence = frame.get_frame_number();
      const double timestamp_ms = frame.get_timestamp();
      const std::uint64_t arrival_ns = MonotonicNs();
      streams.at(kind).Observe(sequence, timestamp_ms, arrival_ns, size);
      if (kind == StreamKind::kColor) {
        rgb_encoder.Submit(frame.get_data(), size);
        if (!first_rgb_timestamp) first_rgb_timestamp = timestamp_ms;
        const std::uint64_t pts_ns = static_cast<std::uint64_t>(
            std::llround((timestamp_ms - *first_rgb_timestamp) * 1e6));
        rgb_index << "{\"record_index\":" << rgb_rows++
                  << ",\"sequence\":" << sequence << ",\"bytes_used\":" << size
                  << ",\"source_timestamp_ms\":" << std::setprecision(17) << timestamp_ms
                  << ",\"observed_host_monotonic_ns\":" << arrival_ns
                  << ",\"pipeline_pts_ns\":" << pts_ns << "}\n";
      } else {
        EyeFrame eye;
        eye.data.resize(size);
        std::memcpy(eye.data.data(), frame.get_data(), size);
        eye.timestamp_ms = timestamp_ms;
        eye.arrival_ns = arrival_ns;
        PendingPair& pair = pending[sequence];
        if (kind == StreamKind::kLeft) pair.left = std::move(eye);
        else pair.right = std::move(eye);
        if (pair.left && pair.right) {
          left_encoder.Submit(pair.left->data.data(), pair.left->data.size());
          right_encoder.Submit(pair.right->data.data(), pair.right->data.size());
          ir_index << "{\"record_index\":" << ir_rows++
                   << ",\"sequence\":" << sequence << ",\"left_sequence\":"
                   << sequence << ",\"right_sequence\":" << sequence
                   << ",\"bytes_used\":" << pair.left->data.size() + pair.right->data.size()
                   << ",\"source_monotonic_ns\":"
                   << static_cast<std::uint64_t>(std::llround(pair.left->timestamp_ms * 1e6))
                   << ",\"left_source_timestamp_ms\":" << std::setprecision(17)
                   << pair.left->timestamp_ms << ",\"right_source_timestamp_ms\":"
                   << pair.right->timestamp_ms << ",\"observed_host_monotonic_ns\":"
                   << std::max(pair.left->arrival_ns, pair.right->arrival_ns) << "}\n";
          pending.erase(sequence);
        }
        if (pending.size() > 16) throw Error("stereo IR pairing window exceeded");
      }
    }
    const std::uint64_t formal_stop_ns = MonotonicNs();
    serial.Stop();
    sensor.stop(); sensor_started = false;
    sensor.close(); sensor_open = false;
    rgb_encoder.Finish();
    left_encoder.Finish();
    right_encoder.Finish();
    ir_index.flush(); rgb_index.flush();
    ir_index.close(); rgb_index.close();
    if (!pending.empty() || ir_rows != rgb_rows) throw Error("RGB/IR alignment mismatch");
    if (!streams.at(StreamKind::kColor).Healthy() ||
        !streams.at(StreamKind::kLeft).Healthy() ||
        !streams.at(StreamKind::kRight).Healthy()) {
      throw Error("D405 continuity check failed");
    }
    if (rgb_encoder.frames() != rgb_rows || left_encoder.frames() != ir_rows ||
        right_encoder.frames() != ir_rows || rgb_encoder.queue_overflows() != 0 ||
        left_encoder.queue_overflows() != 0 || right_encoder.queue_overflows() != 0) {
      throw Error("encoder frame accounting mismatch");
    }
    const SerialMetrics serial_metrics = serial.metrics();
    if (serial_metrics.rate < 395.0 || serial_metrics.rate > 405.0 ||
        serial_metrics.packets == 0 || serial_metrics.crc_errors != 0 ||
        serial_metrics.discarded_bytes != 0 || serial_metrics.sequence_gaps != 0 ||
        serial_metrics.sequence_regressions != 0 || serial_metrics.invalid_imu_flags != 0 ||
        serial_metrics.invalid_encoder_flags != 0) {
      throw Error("STM32 integrity or rate check failed");
    }
    fs::rename(left_partial, partial / "infrared-left-y8.h265");
    fs::rename(right_partial, partial / "infrared-right-y8.h265");
    fs::rename(rgb_partial, partial / "rgb.h265");
    fs::rename(partial / "stm32.bin.partial", partial / "stm32.bin");
    for (const char* name : {"infrared-left-y8.h265", "infrared-right-y8.h265", "rgb.h265"}) {
      std::ifstream stream(partial / name, std::ios::binary);
      std::array<unsigned char, 4> prefix{};
      stream.read(reinterpret_cast<char*>(prefix.data()), prefix.size());
      if (stream.gcount() < 3 || !(prefix[0] == 0 && prefix[1] == 0 &&
          (prefix[2] == 1 || (prefix[2] == 0 && prefix[3] == 1)))) {
        throw Error(std::string(name) + " is not Annex-B");
      }
    }

    const std::uint64_t left_size = fs::file_size(partial / "infrared-left-y8.h265");
    const std::uint64_t right_size = fs::file_size(partial / "infrared-right-y8.h265");
    const std::uint64_t eye_input = ir_rows * kEyeBytes;
    const std::uint64_t compressed_total = left_size + right_size;
    const double compression_ratio = compressed_total / static_cast<double>(eye_input * 2);
    const double formal_span = (formal_stop_ns - formal_start_ns) / 1e9;
    std::ostringstream metrics;
    metrics << std::setprecision(17)
            << "{\"rsusb_streams\":{\"color\":"
            << ContinuityJson(streams.at(StreamKind::kColor)) << ",\"infrared_left\":"
            << ContinuityJson(streams.at(StreamKind::kLeft)) << ",\"infrared_right\":"
            << ContinuityJson(streams.at(StreamKind::kRight)) << "},"
            << "\"formal_start_monotonic_ns\":" << formal_start_ns
            << ",\"formal_stop_monotonic_ns\":" << formal_stop_ns
            << ",\"formal_host_span_s\":" << formal_span
            << ",\"termination\":{\"mode\":"
            << Quote(options.until_signal ? "until_signal" : "fixed_frames")
            << ",\"reason\":" << Quote(termination_reason)
            << ",\"captured_frames\":" << ir_rows
            << ",\"stop_observed_monotonic_ns\":";
    if (stop_observed_ns) metrics << *stop_observed_ns;
    else metrics << "null";
    metrics << ",\"storage_guard\":null},\"ir\":{\"frames\":" << ir_rows
            << ",\"writer\":{\"frames\":" << ir_rows
            << ",\"input_bytes\":" << eye_input * 2
            << ",\"compressed_bytes\":" << compressed_total
            << ",\"queue_overflows\":0,\"compression_ratio\":" << compression_ratio
            << ",\"streams\":{\"left\":{\"encoding\":\"y8_h265_annex_b\","
            << "\"codec\":\"h265\",\"encoder\":\"rockchip_mpp\",\"lossless\":false,"
            << "\"target_bitrate\":" << kIrBitrate << ",\"gop_frames\":" << kFps
            << ",\"frames\":" << ir_rows << ",\"bytes\":" << eye_input
            << ",\"compressed_bytes\":" << left_size << ",\"queue_overflows\":0,"
            << "\"maximum_queue_depth_frames\":" << left_encoder.maximum_queue_depth()
            << "},\"right\":{\"encoding\":\"y8_h265_annex_b\",\"codec\":\"h265\","
            << "\"encoder\":\"rockchip_mpp\",\"lossless\":false,\"target_bitrate\":"
            << kIrBitrate << ",\"gop_frames\":" << kFps << ",\"frames\":" << ir_rows
            << ",\"bytes\":" << eye_input << ",\"compressed_bytes\":" << right_size
            << ",\"queue_overflows\":0,\"maximum_queue_depth_frames\":"
            << right_encoder.maximum_queue_depth() << "}}}},"
            << "\"rgb\":{\"input_frames\":" << rgb_rows
            << ",\"input_bytes\":" << rgb_encoder.bytes()
            << ",\"queue_overflows\":0,\"maximum_queue_depth_frames\":"
            << rgb_encoder.maximum_queue_depth() << ",\"encoded_bytes\":"
            << fs::file_size(partial / "rgb.h265") << ",\"pts_regressions\":0},"
            << "\"stm32\":{\"packets\":" << serial_metrics.packets
            << ",\"observed_rate_hz\":" << serial_metrics.rate
            << ",\"crc_errors\":" << serial_metrics.crc_errors
            << ",\"discarded_bytes\":" << serial_metrics.discarded_bytes
            << ",\"sequence_gaps\":" << serial_metrics.sequence_gaps
            << ",\"sequence_regressions\":" << serial_metrics.sequence_regressions
            << ",\"invalid_imu_flags\":" << serial_metrics.invalid_imu_flags
            << ",\"invalid_encoder_flags\":" << serial_metrics.invalid_encoder_flags
            << "}}";
    const std::string files_json = FileClaims(partial);
    std::ostringstream manifest;
    manifest << "{\"schema\":" << Quote(kSchema) << ",\"status\":\"SEALED\","
             << "\"session_id\":" << Quote(session_id) << ",\"warnings\":[],"
             << "\"profile\":" << profile_json << ",\"device\":" << device_json
             << ",\"counts\":{\"ir_frames\":" << ir_rows
             << ",\"rgb_input_frames\":" << rgb_rows
             << ",\"stm32_packets\":" << serial_metrics.packets << "},"
             << "\"metrics\":" << metrics.str() << ",\"files\":" << files_json << '}';
    WriteAtomic(partial / "manifest.json", manifest.str() + "\n");
    FsyncDirectory(partial);
    fs::rename(partial, final);
    FsyncDirectory(options.output_root);
    fs::remove(final / ".recording");
    FsyncDirectory(final);
    FsyncDirectory(options.output_root);
    return final.string();
  } catch (...) {
    if (sensor_started) {
      try { sensor.stop(); } catch (...) {}
    }
    if (sensor_open) {
      try { sensor.close(); } catch (...) {}
    }
    throw;
  }
}

std::uint64_t PreviewOnly(const Options& options) {
  static_cast<void>(VerifyUsb(options.usb_serial));
  fs::create_directories(options.output_root);
  rs2::context context;
  std::vector<rs2::device> matches;
  for (rs2::device&& candidate : context.query_devices()) {
    if (DeviceInfo(candidate, RS2_CAMERA_INFO_SERIAL_NUMBER) ==
        options.sdk_serial) {
      matches.push_back(candidate);
    }
  }
  if (matches.size() != 1) throw Error("expected exactly one requested RSUSB D405");
  rs2::device device = matches.front();
  if (DeviceInfo(device, RS2_CAMERA_INFO_NAME).find("D405") ==
      std::string::npos) {
    throw Error("requested SDK device is not D405");
  }
  rs2::depth_sensor sensor = device.first<rs2::depth_sensor>();
  std::map<StreamKind, rs2::stream_profile> selected;
  for (const rs2::stream_profile& profile : sensor.get_stream_profiles()) {
    const rs2::video_stream_profile video =
        profile.as<rs2::video_stream_profile>();
    if (!video || video.width() != kWidth || video.height() != kHeight ||
        profile.fps() != kFps) continue;
    if (profile.stream_type() == RS2_STREAM_COLOR &&
        profile.format() == RS2_FORMAT_YUYV) {
      selected[StreamKind::kColor] = profile;
    } else if (profile.stream_type() == RS2_STREAM_INFRARED &&
               profile.format() == RS2_FORMAT_Y8 &&
               profile.stream_index() == 1) {
      selected[StreamKind::kLeft] = profile;
    } else if (profile.stream_type() == RS2_STREAM_INFRARED &&
               profile.format() == RS2_FORMAT_Y8 &&
               profile.stream_index() == 2) {
      selected[StreamKind::kRight] = profile;
    }
  }
  if (selected.size() != 3) {
    throw Error("D405 RGB and stereo IR 1280x720@30 preview profiles are incomplete");
  }
  std::vector<rs2::stream_profile> profiles{
      selected.at(StreamKind::kColor), selected.at(StreamKind::kLeft),
      selected.at(StreamKind::kRight)};
  const fs::path log_path =
      options.output_root /
      ("preview-gstreamer-" + std::to_string(getpid()) + ".log");
  Encoder encoder(PreviewOnlyCommand(*options.preview_mjpeg_port), log_path,
                  kColorBytes, 4);
  rs2::frame_queue frame_queue(16, false);
  bool sensor_open = false;
  bool sensor_started = false;
  try {
    encoder.Start();
    sensor.open(profiles);
    sensor_open = true;
    sensor.start(frame_queue);
    sensor_started = true;
    std::map<StreamKind, int> warmup{{StreamKind::kColor, 0},
                                     {StreamKind::kLeft, 0},
                                     {StreamKind::kRight, 0}};
    const auto warmup_deadline = Clock::now() + std::chrono::seconds(3);
    while (Clock::now() < warmup_deadline) {
      rs2::frame frame = frame_queue.wait_for_frame(1000);
      const StreamKind kind = Kind(frame);
      if (kind != StreamKind::kUnknown) ++warmup[kind];
    }
    if (warmup[StreamKind::kColor] < kFps ||
        warmup[StreamKind::kLeft] < kFps ||
        warmup[StreamKind::kRight] < kFps) {
      throw Error("D405 preview warmup did not receive all three streams");
    }
    const auto deadline = Clock::now() + std::chrono::seconds(options.duration);
    while (!g_stop_requested.load() && Clock::now() < deadline) {
      rs2::frame frame = frame_queue.wait_for_frame(1000);
      if (Kind(frame) != StreamKind::kColor) continue;
      if ((frame.get_frame_number() & 1U) != 0U) continue;
      encoder.Submit(frame.get_data(), frame.get_data_size());
    }
    sensor.stop();
    sensor_started = false;
    sensor.close();
    sensor_open = false;
    encoder.Finish();
    const std::uint64_t encoded_frames = encoder.frames();
    fs::remove(log_path);
    return encoded_frames;
  } catch (...) {
    if (sensor_started) {
      try { sensor.stop(); } catch (...) {}
    }
    if (sensor_open) {
      try { sensor.close(); } catch (...) {}
    }
    throw;
  }
}

void SignalHandler(int) { g_stop_requested.store(true); }

}  // namespace

int main(int argc, char** argv) {
  signal(SIGPIPE, SIG_IGN);
  signal(SIGINT, SignalHandler);
  signal(SIGTERM, SignalHandler);
  try {
    const Options options = Parse(argc, argv);
    if (options.preview_only) {
      const std::uint64_t frames = PreviewOnly(options);
      std::cout << "{\"status\":\"PREVIEW_STOPPED\",\"frames\":"
                << frames << "}\n";
    } else {
      const std::string session = Capture(options);
      std::cout << "{\"status\":\"SEALED\",\"session\":" << Quote(session)
                << "}\n";
    }
    return 0;
  } catch (const rs2::error& error) {
    std::cout << "{\"status\":\"FAIL\",\"reason\":"
              << Quote(std::string("librealsense: ") + error.what()) << "}\n";
  } catch (const std::exception& error) {
    std::cout << "{\"status\":\"FAIL\",\"reason\":" << Quote(error.what()) << "}\n";
  }
  return 1;
}
