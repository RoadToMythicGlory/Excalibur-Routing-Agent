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

int main(int argc, char* argv[]) {
    gst_init(&argc, &argv);

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
