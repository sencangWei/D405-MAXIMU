#define main production_main
#include "umi_collector.cpp"
#undef main

#include <cstdlib>

void Check(bool result, const char* message) {
  if (!result) throw Error(message);
}

std::array<std::uint8_t, 63> Packet(
    std::uint32_t sequence, bool bad_encoder) {
  std::array<std::uint8_t, 63> packet{};
  packet[0] = 0xa5;
  packet[1] = 0x5a;
  packet[2] = 1;
  packet[3] = 63;
  packet[4] = bad_encoder ? 5 : 3;
  for (unsigned int index = 0; index < 4; ++index) {
    packet[6 + index] = packet[18 + index] = packet[56 + index] =
        static_cast<std::uint8_t>(sequence >> (8 * index));
  }
  packet[24] = 0xeb;
  packet[25] = 0x90;
  packet[26] = 0x22;
  for (unsigned int index = 24; index < 60; ++index) {
    packet[60] = static_cast<std::uint8_t>(packet[60] + packet[index]);
  }
  const auto crc = Crc16(packet.data(), 61);
  packet[61] = static_cast<std::uint8_t>(crc);
  packet[62] = static_cast<std::uint8_t>(crc >> 8);
  return packet;
}

void SerialTest(const fs::path& root, int mode) {
  int master = posix_openpt(O_RDWR | O_NOCTTY);
  Check(master >= 0 && grantpt(master) == 0 && unlockpt(master) == 0,
        "pty setup");
  std::atomic<bool> quit{false};
  std::atomic<bool> inject{false};
  std::atomic<bool> corrupt{false};
  std::thread writer([&] {
    auto target = Clock::now();
    unsigned int sequence = 0;
    while (!quit.load()) {
      target += std::chrono::microseconds(2500);
      std::this_thread::sleep_until(target);
      if (inject.exchange(false)) {
        const std::uint8_t junk[]{0x11, 0x22, 0x33};
        WriteAll(master, junk, 3);
      }
      auto packet = Packet(sequence++, mode == 2);
      if (corrupt.exchange(false)) packet[61] ^= 1;
      WriteAll(master, packet.data(), packet.size());
    }
  });
  try {
    SerialCollector collector(
        ptsname(master), root / "serial.bin", root / "serial.jsonl");
    collector.Start();
    inject = true;
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    bool rejected = false;
    std::uint64_t formal_start = 0;
    try {
      formal_start = collector.Begin();
    } catch (const Error&) {
      rejected = true;
    }
    if (mode == 2) {
      Check(rejected, "invalid encoder must fail warmup");
    } else {
      Check(!rejected, "healthy warmup must pass");
      if (mode == 1) {
        inject = true;
        corrupt = true;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(550));
      if (mode == 1) {
        bool failed_during_capture = false;
        try {
          collector.ThrowIfFailed();
        } catch (const Error&) {
          failed_during_capture = true;
        }
        Check(failed_during_capture,
              "formal packet corruption must fail during capture");
      }
      collector.End();
      const std::uint64_t formal_stop = MonotonicNs();
      bool stop_failed = false;
      try {
        collector.Stop();
      } catch (const Error&) {
        stop_failed = true;
      }
      Check(stop_failed == (mode == 1),
            "formal corruption must remain visible at Stop");
      const auto metrics = collector.metrics();
      if (mode == 0) {
        Check(metrics.packets >= 190 && metrics.rate > 390 &&
                  metrics.rate < 410,
              "healthy stream count/rate");
      }
      Check(metrics.first_rx_monotonic_ns >= formal_start,
            "warmup packets must precede the formal boundary");
      Check(metrics.last_rx_monotonic_ns <= formal_stop,
            "no packet may enter metrics after the formal stop boundary");
      if (mode == 0) {
        Check(metrics.discarded_bytes == 0 && metrics.crc_errors == 0 &&
                  metrics.sequence_gaps == 0,
              "warmup ignored");
      } else {
        Check(metrics.discarded_bytes > 0 || metrics.crc_errors > 0,
              "formal corruption evidence retained");
      }
      std::cout << "SERIAL mode=" << mode
                << " packets=" << metrics.packets
                << " rate=" << metrics.rate
                << " discarded=" << metrics.discarded_bytes
                << " crc=" << metrics.crc_errors << "\n";
    }
  } catch (...) {
    quit = true;
    writer.join();
    close(master);
    throw;
  }
  quit = true;
  writer.join();
  close(master);
  fs::remove(root / "serial.bin");
  fs::remove(root / "serial.jsonl");
}

void SerialStallTest(const fs::path& root) {
  int master = posix_openpt(O_RDWR | O_NOCTTY);
  Check(master >= 0 && grantpt(master) == 0 && unlockpt(master) == 0,
        "stall pty setup");
  std::atomic<bool> quit{false};
  std::thread writer([&] {
    auto target = Clock::now();
    unsigned int sequence = 0;
    while (!quit.load()) {
      target += std::chrono::microseconds(2500);
      std::this_thread::sleep_until(target);
      auto packet = Packet(sequence++, false);
      WriteAll(master, packet.data(), packet.size());
    }
  });
  try {
    SerialCollector collector(
        ptsname(master), root / "serial-stall.bin",
        root / "serial-stall.jsonl");
    collector.Start();
    collector.Begin();
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    quit = true;
    writer.join();
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    bool rejected = false;
    try {
      collector.ThrowIfFailed();
    } catch (const Error&) {
      rejected = true;
    }
    Check(rejected, "STM32 silence after Begin must fail capture");
    rejected = false;
    try {
      collector.Stop();
    } catch (const Error&) {
      rejected = true;
    }
    Check(rejected, "STM32 stall must remain visible during finalization");
  } catch (...) {
    quit = true;
    if (writer.joinable()) writer.join();
    close(master);
    throw;
  }
  quit = true;
  if (writer.joinable()) writer.join();
  close(master);
  fs::remove(root / "serial-stall.bin");
  fs::remove(root / "serial-stall.jsonl");
}

void SerialBoundaryFragmentTest(const fs::path& root) {
  int master = posix_openpt(O_RDWR | O_NOCTTY);
  Check(master >= 0 && grantpt(master) == 0 && unlockpt(master) == 0,
        "boundary pty setup");
  try {
    SerialCollector collector(
        ptsname(master), root / "serial-boundary.bin",
        root / "serial-boundary.jsonl");
    collector.Start();
    const auto crossing = Packet(1, false);
    WriteAll(master, crossing.data(), 20);
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    std::uint64_t formal_start = 0;
    std::exception_ptr begin_error;
    std::thread begin([&] {
      try {
        formal_start = collector.Begin();
      } catch (...) {
        begin_error = std::current_exception();
      }
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    std::vector<std::uint8_t> remainder(
        crossing.begin() + 20, crossing.end());
    for (std::uint32_t sequence = 2; sequence <= 4; ++sequence) {
      const auto packet = Packet(sequence, false);
      remainder.insert(remainder.end(), packet.begin(), packet.end());
    }
    WriteAll(master, remainder.data(), remainder.size());
    begin.join();
    if (begin_error) std::rethrow_exception(begin_error);
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    collector.End();
    const std::uint64_t formal_stop = MonotonicNs();
    collector.Stop();
    const auto metrics = collector.metrics();
    Check(metrics.first_rx_monotonic_ns >= formal_start &&
              metrics.last_rx_monotonic_ns <= formal_stop,
          "fragment test formal timestamps");
    std::ifstream index(root / "serial-boundary.jsonl");
    const std::string rows{
        std::istreambuf_iterator<char>(index),
        std::istreambuf_iterator<char>()};
    Check(rows.find("\"sequence\":1") == std::string::npos,
          "packet crossing Begin must not be recorded");
    Check(rows.find("\"sequence\":3") != std::string::npos,
          "fresh packets after Begin must be recorded");
  } catch (...) {
    close(master);
    throw;
  }
  close(master);
  fs::remove(root / "serial-boundary.bin");
  fs::remove(root / "serial-boundary.jsonl");
}

int main() {
  signal(SIGPIPE, SIG_IGN);
  const fs::path root =
      fs::path("/tmp") / ("umi-native-test-" + std::to_string(getpid()));
  fs::create_directory(root);
  try {
    for (int mode = 0; mode < 3; ++mode) SerialTest(root, mode);
    SerialStallTest(root);
    SerialBoundaryFragmentTest(root);
    {
      CameraLease first("testLock");
      bool blocked = false;
      try {
        CameraLease second("testLock");
      } catch (const Error&) {
        blocked = true;
      }
      Check(blocked, "camera ownership overlap");
    }
    { CameraLease released("testLock"); }
    std::vector<std::uint8_t> data(1024 * 1024, 42);
    {
      Encoder good({"/bin/cat"}, root / "encoder-good.log", data.size(), 4);
      good.Start();
      good.Submit(data.data(), data.size());
      good.Finish(std::chrono::seconds(2));
      Check(good.frames() == 1 &&
                fs::file_size(root / "encoder-good.log") == data.size(),
            "encoder drain");
    }
    {
      Encoder stalled(
          {"/bin/sleep", "60"}, root / "encoder-stall.log", data.size(), 4);
      stalled.Start();
      stalled.Submit(data.data(), data.size());
      const auto begin = Clock::now();
      bool failed = false;
      try {
        stalled.Finish(std::chrono::milliseconds(200));
      } catch (const Error&) {
        failed = true;
      }
      Check(failed && Clock::now() - begin < std::chrono::seconds(2),
            "bounded stalled encoder shutdown");
    }
    {
      Encoder cancelled(
          {"/bin/sleep", "60"}, root / "encoder-cancel.log", data.size(), 4);
      cancelled.Start();
      cancelled.Submit(data.data(), data.size());
      cancelled.Cancel();
    }
    std::cout << "PASS serial warmup, formal corruption, invalid encoder, "
                 "serial stall/boundary, camera lock, "
                 "encoder drain/timeout/cancel\n";
  } catch (const std::exception& error) {
    std::cerr << error.what() << "\n";
    return 1;
  }
  for (const auto& name : {
           "encoder-good.log", "encoder-stall.log", "encoder-cancel.log"}) {
    fs::remove(root / name);
  }
  fs::remove(root);
  return 0;
}
