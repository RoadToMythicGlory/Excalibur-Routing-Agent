// media-core entry point.
//
// Reads newline-delimited JSON commands on stdin, drives a MediaCore instance,
// and writes newline-delimited JSON acks/status/errors on stdout. This is the
// process GstProgramOutputManager (Python) spawns and supervises.
#include <cstdio>
#include <iostream>
#include <mutex>
#include <string>

#include <gst/gst.h>

#include "control_protocol.hpp"
#include "media_core.hpp"
#include "pipeline_spec.hpp"

namespace {

// CVE-2026-119610 (GStreamer SA-2026-0095): rtph264depay in gst-plugins-good
// < 1.28.9 has a remotely triggerable out-of-bounds heap read. RTSP inputs go
// through rtspsrc ! decodebin3, which autoplugs rtph264depay. On a vulnerable
// build, remove that element from the registry so it can never be plugged:
// H.264-over-RTP inputs stop working until GStreamer is upgraded, everything
// else keeps running. The element lives in the "rtp" plugin.
constexpr int kMinGoodMajor = 1;
constexpr int kMinGoodMinor = 28;
constexpr int kMinGoodMicro = 9;
constexpr const char* kVulnerableElement = "rtph264depay";

// Returns "" when nothing had to be done, otherwise a description of the
// mitigation. Sets `failed` when the element could not be disabled.
std::string mitigate_cve_2026_119610(bool& failed) {
    failed = false;
    GstRegistry* registry = gst_registry_get();
    GstPlugin* plugin = gst_registry_find_plugin(registry, "rtp");
    if (plugin == nullptr) {
        return "";  // No RTP depayloaders at all: the vulnerable code is absent.
    }
    std::string version = gst_plugin_get_version(plugin) ? gst_plugin_get_version(plugin) : "";
    gst_object_unref(plugin);
    int major = 0, minor = 0, micro = 0;
    const bool parsed = std::sscanf(version.c_str(), "%d.%d.%d", &major, &minor, &micro) >= 2;
    const bool too_old = !parsed  // unknown version: assume vulnerable
                 || (major != kMinGoodMajor ? major < kMinGoodMajor
                   : minor != kMinGoodMinor ? minor < kMinGoodMinor
                   : micro < kMinGoodMicro);
    if (!too_old) {
        return "";
    }
    GstPluginFeature* feature = gst_registry_lookup_feature(registry, kVulnerableElement);
    if (feature != nullptr) {
        gst_registry_remove_feature(registry, feature);
        gst_object_unref(feature);
    }
    GstElementFactory* still_there = gst_element_factory_find(kVulnerableElement);
    if (still_there != nullptr) {
        gst_object_unref(still_there);
        failed = true;
        return "gst-plugins-good " + version +
               " is vulnerable to CVE-2026-119610 and rtph264depay could not be disabled";
    }
    return "gst-plugins-good " + version +
           " is vulnerable to CVE-2026-119610: rtph264depay disabled "
           "(H.264 RTP/RTSP inputs unavailable until GStreamer >= 1.28.9)";
}

}  // namespace

int main(int argc, char* argv[]) {
    gst_init(&argc, &argv);

    bool mitigation_failed = false;
    const std::string mitigation = mitigate_cve_2026_119610(mitigation_failed);
    if (!mitigation.empty()) {
        std::fprintf(stderr, "media-core: %s\n", mitigation.c_str());
        std::fflush(stderr);
    }
    if (mitigation_failed) {
        mediacore::ErrorReport err;
        err.message = mitigation;
        err.element = "security";
        std::fputs(mediacore::serialize_error(err).c_str(), stdout);
        std::fputc('\n', stdout);
        std::fflush(stdout);
        return 2;
    }

    // Serialize all stdout writes; the feeder/bus threads never write directly,
    // but status is emitted from the main thread and errors from bus threads.
    std::mutex out_mutex;
    auto emit = [&out_mutex](const std::string& line) {
        std::lock_guard<std::mutex> lock(out_mutex);
        std::fputs(line.c_str(), stdout);
        std::fputc('\n', stdout);
        std::fflush(stdout);
    };

    mediacore::MediaCore core(emit);

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) {
            continue;
        }
        mediacore::Command cmd = mediacore::parse_command(line);
        if (cmd.parse_error) {
            mediacore::ErrorReport err;
            err.message = cmd.parse_message;
            err.element = "protocol";
            emit(mediacore::serialize_error(err));
            continue;
        }

        if (cmd.cmd == mediacore::proto::kCmdStart) {
            bool ok = core.start(cmd.output);
            emit(mediacore::serialize_ack(cmd.cmd, ok));
            core.report_status();
        } else if (cmd.cmd == mediacore::proto::kCmdSetInputs) {
            core.set_inputs(cmd.inputs);
            emit(mediacore::serialize_ack(cmd.cmd, true));
        } else if (cmd.cmd == mediacore::proto::kCmdTake) {
            bool ok = core.take(cmd.input_id);
            emit(mediacore::serialize_ack(cmd.cmd, ok));
            core.report_status();
        } else if (cmd.cmd == mediacore::proto::kCmdStatus) {
            core.report_status();
        } else if (cmd.cmd == mediacore::proto::kCmdStop) {
            core.stop();
            emit(mediacore::serialize_ack(cmd.cmd, true));
            break;
        } else {
            mediacore::ErrorReport err;
            err.message = "unknown command: " + cmd.cmd;
            err.element = "protocol";
            emit(mediacore::serialize_error(err));
        }
    }

    core.stop();
    gst_deinit();
    return 0;
}
