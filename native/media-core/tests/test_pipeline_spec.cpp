// Minimal assertion-based tests for the GStreamer-free pipeline_spec layer.
// No test framework dependency: keeps the native build self-contained. A
// non-zero exit code (via abort) fails `ctest`.
#include <cassert>
#include <cstdio>
#include <string>

#include "pipeline_spec.hpp"

using namespace mediacore;

static bool contains(const std::string& haystack, const std::string& needle) {
    return haystack.find(needle) != std::string::npos;
}

static void test_normalize_caps() {
    OutputConfig cfg;  // defaults: 1920x1080, 30fps, I420
    const std::string caps = build_normalize_caps(cfg);
    assert(contains(caps, "video/x-raw"));
    assert(contains(caps, "format=I420"));
    assert(contains(caps, "width=1920"));
    assert(contains(caps, "height=1080"));
    assert(contains(caps, "framerate=30/1"));
    assert(contains(caps, "pixel-aspect-ratio=1/1"));
}

static void test_source_description_per_type() {
    OutputConfig cfg;

    InputSpec srt{"input_01", "srt://0.0.0.0:9001?mode=listener", "srt", false};
    const std::string srt_desc = build_source_description(srt, cfg, "normalized_video");
    assert(contains(srt_desc, "srtsrc"));
    assert(contains(srt_desc, "tsdemux"));
    assert(contains(srt_desc, "appsink name=normalized_video"));
    assert(contains(srt_desc, "drop=true"));

    InputSpec rtsp{"input_02", "rtsp://127.0.0.1:8554/input_02", "rtsp", false};
    const std::string rtsp_desc = build_source_description(rtsp, cfg, "normalized_video");
    assert(contains(rtsp_desc, "rtspsrc"));
    assert(contains(rtsp_desc, "protocols=tcp"));

    InputSpec file{"input_03", "file:///C:/streams/demo.mp4", "file", true};
    const std::string file_desc = build_source_description(file, cfg, "normalized_video");
    assert(contains(file_desc, "uridecodebin3"));

    // Every source must terminate at the normalized caps + named appsink.
    assert(contains(srt_desc, "framerate=30/1"));
    assert(contains(rtsp_desc, "videoscale"));
    assert(contains(file_desc, "videorate"));
}

static void test_output_description() {
    OutputConfig cfg;
    cfg.video_bitrate_kbps = 6000;
    const std::string desc = build_output_description(cfg, "program_video");
    assert(contains(desc, "appsrc name=program_video"));
    assert(contains(desc, "is-live=true"));
    assert(contains(desc, "do-timestamp=false"));
    assert(contains(desc, "x264enc"));
    assert(contains(desc, "bitrate=6000"));
    assert(contains(desc, "h264parse config-interval=1"));
    assert(contains(desc, "mpegtsmux"));
    assert(contains(desc, "srtsink"));

    OutputConfig hw = cfg;
    hw.encoder = "nvh264enc";
    const std::string hw_desc = build_output_description(hw, "program_video");
    assert(contains(hw_desc, "nvh264enc"));
}

static void test_parse_start() {
    const std::string line =
        R"({"cmd":"start","publish_url":"srt://x:1","width":1280,"height":720,"fps":60,"video_bitrate_kbps":4000,"encoder":"x264enc","pixel_format":"NV12"})";
    Command cmd = parse_command(line);
    assert(!cmd.parse_error);
    assert(cmd.cmd == "start");
    assert(cmd.output.width == 1280);
    assert(cmd.output.height == 720);
    assert(cmd.output.fps == 60);
    assert(cmd.output.video_bitrate_kbps == 4000);
    assert(cmd.output.pixel_format == "NV12");
    assert(cmd.output.publish_url == "srt://x:1");
}

static void test_parse_set_inputs_and_take() {
    const std::string inputs_line =
        R"({"cmd":"set_inputs","inputs":[{"id":"input_01","url":"rtsp://h/1","source_type":"rtsp"},{"id":"bad"}]})";
    Command inputs = parse_command(inputs_line);
    assert(!inputs.parse_error);
    // The second entry is missing a url and must be dropped.
    assert(inputs.inputs.size() == 1);
    assert(inputs.inputs[0].id == "input_01");
    assert(inputs.inputs[0].source_type == "rtsp");

    Command take = parse_command(R"({"cmd":"take","input_id":"input_01"})");
    assert(take.cmd == "take");
    assert(take.input_id == "input_01");
}

static void test_parse_rejects_garbage() {
    Command bad = parse_command("not json at all");
    assert(bad.parse_error);
    Command no_cmd = parse_command(R"({"foo":1})");
    assert(no_cmd.parse_error);
}

static void test_serialize_roundtrip() {
    assert(contains(serialize_ack("start", true), "\"type\":\"ack\""));
    assert(contains(serialize_ack("start", true), "\"ok\":true"));

    StatusReport st;
    st.state = "running";
    st.active_input = "input_01";
    st.pushed_frames = 42;
    const std::string status = serialize_status(st);
    assert(contains(status, "\"type\":\"status\""));
    assert(contains(status, "\"state\":\"running\""));
    assert(contains(status, "\"active_input\":\"input_01\""));
    assert(contains(status, "\"pushed_frames\":42"));

    StatusReport idle;  // active_input == nullopt -> JSON null
    assert(contains(serialize_status(idle), "\"active_input\":null"));

    ErrorReport err;
    err.message = "boom";
    err.element = "videoscale";
    const std::string error = serialize_error(err);
    assert(contains(error, "\"type\":\"error\""));
    assert(contains(error, "\"element\":\"videoscale\""));
}

int main() {
    test_normalize_caps();
    test_source_description_per_type();
    test_output_description();
    test_parse_start();
    test_parse_set_inputs_and_take();
    test_parse_rejects_garbage();
    test_serialize_roundtrip();
    std::puts("all pipeline_spec tests passed");
    return 0;
}
