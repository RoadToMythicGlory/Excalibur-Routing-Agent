// Pure (GStreamer-free) pipeline description + protocol (de)serialization.
//
// Everything here is deliberately independent of libgstreamer so it can be unit
// tested on any machine (see tests/test_pipeline_spec.cpp). media_core.cpp
// consumes these helpers and feeds the resulting strings to
// gst_parse_launch()/gst_caps_from_string().
#pragma once

#include <optional>
#include <string>
#include <vector>

namespace mediacore {

// Output shape every routed source is normalized to before encoding.
struct OutputConfig {
    int width = 1920;
    int height = 1080;
    int fps = 30;
    std::string pixel_format = "I420";
    std::string encoder = "x264enc";
    int video_bitrate_kbps = 6000;
    std::string publish_url = "srt://0.0.0.0:9999?mode=listener&latency=200";
};

// One routable input the compositor may put on air.
struct InputSpec {
    std::string id;
    std::string url;
    std::string source_type = "srt";  // srt | rtsp | url | file
    bool loop = false;
};

// A parsed inbound command. `cmd` is the protocol verb; the remaining fields are
// populated per-command (unused fields stay empty/default).
struct Command {
    std::string cmd;
    OutputConfig output;              // start
    std::vector<InputSpec> inputs;    // set_inputs
    std::string input_id;             // take
    bool parse_error = false;
    std::string parse_message;
};

// Snapshot the process reports back to Python.
struct StatusReport {
    std::string state = "idle";
    std::optional<std::string> active_input;
    std::string negotiated_caps;
    std::string encoder;
    std::string output_url;
    double fps = 0.0;
    long long pushed_frames = 0;
    long long dropped_frames = 0;
    long long black_frames = 0;
    std::optional<std::string> last_error;
};

// A structured error emitted when a source/caps failure occurs.
struct ErrorReport {
    std::string message;
    std::string source_url;
    std::string element;
    std::string requested_caps;
};

// -- caps ---------------------------------------------------------------------

// The fixed raw-video caps pinned at BOTH app-boundary ends (appsink in,
// appsrc out). pixel-aspect-ratio is forced to 1/1 to avoid stretched output.
std::string build_normalize_caps(const OutputConfig& cfg);

// -- pipeline descriptions (gst_parse_launch syntax) --------------------------

// Decode/normalize branch for one source, terminating in a named appsink.
// `appsink_name` is the element name media_core.cpp fetches to pull samples.
std::string build_source_description(const InputSpec& input,
                                     const OutputConfig& cfg,
                                     const std::string& appsink_name);

// Encode/output branch fed by a named appsrc.
std::string build_output_description(const OutputConfig& cfg,
                                     const std::string& appsrc_name);

// -- protocol (de)serialization ----------------------------------------------

// Parse one line of JSON into a Command. On malformed input the returned
// Command has parse_error == true and an explanatory parse_message.
Command parse_command(const std::string& line);

// Serialize an ack/status/error into a single JSON line (no trailing newline).
std::string serialize_ack(const std::string& cmd, bool ok);
std::string serialize_status(const StatusReport& status);
std::string serialize_error(const ErrorReport& error);

}  // namespace mediacore
