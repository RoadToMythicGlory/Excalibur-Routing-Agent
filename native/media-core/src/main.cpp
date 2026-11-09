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
// through rtspsrc ! decodebin3, which autoplugs rtph264depay, so refuse to run
// on a vulnerable plugins-good build. The element lives in the "rtp" plugin.
constexpr int kMinGoodMajor = 1;
constexpr int kMinGoodMinor = 28;
constexpr int kMinGoodMicro = 9;

// Returns an empty string when OK, otherwise a human-readable reason.
std::string check_plugins_good_version() {
    GstPlugin* plugin = gst_registry_find_plugin(gst_registry_get(), "rtp");
    if (plugin == nullptr) {
        return "";  // No RTP depayloaders at all: the vulnerable code is absent.
    }
    std::string version = gst_plugin_get_version(plugin) ? gst_plugin_get_version(plugin) : "";
    gst_object_unref(plugin);
    int major = 0, minor = 0, micro = 0;
    if (std::sscanf(version.c_str(), "%d.%d.%d", &major, &minor, &micro) < 2) {
        return "cannot parse gst-plugins-good version '" + version + "'";
    }
    bool too_old = major != kMinGoodMajor ? major < kMinGoodMajor
                 : minor != kMinGoodMinor ? minor < kMinGoodMinor
                 : micro < kMinGoodMicro;
    if (too_old) {
        return "gst-plugins-good " + version +
               " is vulnerable to CVE-2026-119610 (rtph264depay OOB read); "
               "install GStreamer >= 1.28.9";
    }
    return "";
}

}  // namespace

int main(int argc, char* argv[]) {
    gst_init(&argc, &argv);

    const std::string version_problem = check_plugins_good_version();
    if (!version_problem.empty()) {
        mediacore::ErrorReport err;
        err.message = version_problem;
        err.element = "security";
        std::fputs(mediacore::serialize_error(err).c_str(), stdout);
        std::fputc('\n', stdout);
        std::fflush(stdout);
        std::fprintf(stderr, "media-core: %s\n", version_problem.c_str());
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
