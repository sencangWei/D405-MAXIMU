#include "openvr.h"
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>

// Connection probe only. Query timestamps are NOT sensor acquisition timestamps.
// Standing universe, zero additional future prediction; no compositor dependency.
int main(int argc, char **argv) {
    if (argc != 3) {
        std::fprintf(stderr, "Usage: %s LHR-SERIAL seconds\n", argv[0]);
        return 2;
    }
    char *end = nullptr;
    const double seconds = std::strtod(argv[2], &end);
    if (*end || !std::isfinite(seconds) || seconds < 1 || seconds > 120) return 2;
    vr::EVRInitError error = vr::VRInitError_None;
    auto *system = vr::VR_Init(&error, vr::VRApplication_Background);
    if (!system || error != vr::VRInitError_None) {
        std::fprintf(stderr, "VR_Init: %s\n", vr::VR_GetVRInitErrorAsSymbol(error));
        return 1;
    }
    vr::TrackedDeviceIndex_t target = vr::k_unTrackedDeviceIndexInvalid;
    for (unsigned i = 0; i < vr::k_unMaxTrackedDeviceCount; ++i) {
        if (!system->IsTrackedDeviceConnected(i)) continue;
        char serial[vr::k_unMaxPropertyStringSize] = {};
        vr::ETrackedPropertyError property_error = vr::TrackedProp_Success;
        system->GetStringTrackedDeviceProperty(i, vr::Prop_SerialNumber_String,
                                              serial, sizeof(serial), &property_error);
        const auto device_class = system->GetTrackedDeviceClass(i);
        std::fprintf(stderr, "index=%u class=%d serial=%s property_error=%d\n",
                     i, int(device_class), serial, int(property_error));
        if (property_error == vr::TrackedProp_Success &&
            device_class == vr::TrackedDeviceClass_GenericTracker &&
            std::strcmp(serial, argv[1]) == 0) target = i;
    }
    if (target == vr::k_unTrackedDeviceIndexInvalid) {
        std::fprintf(stderr, "Requested physical GenericTracker not found\n");
        vr::VR_Shutdown();
        return 1;
    }
    std::puts("sequence,query_monotonic_begin_ns,query_monotonic_end_ns,host_realtime_ns,index,connected,pose_valid,tracking_result,m00,m01,m02,px,m10,m11,m12,py,m20,m21,m22,pz,vx,vy,vz,wx,wy,wz");
    using Clock = std::chrono::steady_clock;
    const auto start = Clock::now();
    auto next = start;
    unsigned count = 0, good = 0;
    while (std::chrono::duration<double>(Clock::now() - start).count() < seconds) {
        vr::TrackedDevicePose_t poses[vr::k_unMaxTrackedDeviceCount] = {};
        const auto before = Clock::now();
        const auto wall = std::chrono::system_clock::now();
        system->GetDeviceToAbsoluteTrackingPose(vr::TrackingUniverseStanding, 0.f,
                                               poses, vr::k_unMaxTrackedDeviceCount);
        const auto after = Clock::now();
        const auto &pose = poses[target];
        good += pose.bDeviceIsConnected && pose.bPoseIsValid &&
                pose.eTrackingResult == vr::TrackingResult_Running_OK;
        auto ns = [](auto t) { return std::chrono::duration_cast<std::chrono::nanoseconds>(t.time_since_epoch()).count(); };
        std::printf("%u,%lld,%lld,%lld,%u,%d,%d,%d", count++,
                    (long long)ns(before), (long long)ns(after), (long long)ns(wall),
                    target, pose.bDeviceIsConnected, pose.bPoseIsValid, int(pose.eTrackingResult));
        for (const auto &row : pose.mDeviceToAbsoluteTracking.m)
            for (float v : row) std::printf(",%.9g", double(v));
        for (float v : pose.vVelocity.v) std::printf(",%.9g", double(v));
        for (float v : pose.vAngularVelocity.v) std::printf(",%.9g", double(v));
        std::putchar('\n');
        next += std::chrono::nanoseconds(8333333); // 120 Hz queries, not a sensor-rate claim.
        std::this_thread::sleep_until(next);
    }
    const bool output_ok = std::fflush(stdout) == 0 && !std::ferror(stdout);
    std::fprintf(stderr, "queries=%u connected_valid_running_ok=%u\n", count, good);
    vr::VR_Shutdown();
    return output_ok && count && double(good) / count >= 0.95 ? 0 : 1;
}
