#include "pipeline_spec.hpp"

#include <sstream>

#include <nlohmann/json.hpp>

#include "control_protocol.hpp"

namespace mediacore {

using nlohmann::json;

std::string build_normalize_caps(const OutputConfig& cfg) {
    std::ostringstream os;
    os << "video/x-raw,format=" << cfg.pixel_format << ",width=" << cfg.width
       << ",height=" << cfg.height << ",framerate=" << cfg.fps
       << "/1,pixel-aspect-ratio=1/1";
    return os.str();
}

namespace {

// Source-specific head of the decode branch. decodebin3 handles the actual
// codec so we only vary how bytes are pulled off the wire per transport.
std::string source_head(const InputSpec& input) {
    if (input.source_type == proto::kSourceRtsp) {
        // TCP + a modest jitter buffer keeps RTSP stable over MediaMTX.
        return "rtspsrc location=\"" + input.url +
               "\" protocols=tcp latency=100 ! queue ! decodebin3";
    }
    if (input.source_type == proto::kSourceFile) {
        // File paths become file:// URIs; looping is handled natively by the
        // manager via segment seeks, so we only decode here.
        return "uridecodebin3 uri=\"" + input.url + "\"";
    }
    if (input.source_type == proto::kSourceUrl) {
        return "uridecodebin3 uri=\"" + input.url + "\"";
    }
    // Default: SRT carrying MPEG-TS -> demux -> decode.
    return "srtsrc uri=\"" + input.url +
           "\" ! queue ! tsdemux ! h264parse ! decodebin3";
}

}  // namespace

std::string build_source_description(const InputSpec& input,
                                     const OutputConfig& cfg,
                                     const std::string& appsink_name) {
    std::ostringstream os;
    os << source_head(input)
       << " ! videoconvert ! videoscale ! videorate ! "
       << build_normalize_caps(cfg) << " ! appsink name=" << appsink_name
       << " emit-signals=true sync=false max-buffers=2 drop=true";
    return os.str();
}

std::string build_output_description(const OutputConfig& cfg,
                                     const std::string& appsrc_name) {
    std::ostringstream os;
    os << "appsrc name=" << appsrc_name
       << " is-live=true format=time do-timestamp=false block=false caps="
       << build_normalize_caps(cfg)
       << " ! queue leaky=downstream max-size-buffers=2 ! ";

    if (cfg.encoder == "x264enc") {
        os << "x264enc tune=zerolatency speed-preset=veryfast bitrate="
           << cfg.video_bitrate_kbps << " key-int-max=" << (cfg.fps * 2)
           << " bframes=0";
    } else if (cfg.encoder == "nvh264enc") {
        os << "nvh264enc preset=low-latency-hq rc-mode=cbr bitrate="
           << cfg.video_bitrate_kbps << " gop-size=" << (cfg.fps * 2);
    } else {
        // Unknown encoder: pass the name through with a sane bitrate so the
        // operator can experiment, but keep the surrounding graph intact.
        os << cfg.encoder << " bitrate=" << cfg.video_bitrate_kbps;
    }

    os << " ! h264parse config-interval=1 ! mpegtsmux alignment=7 ! srtsink uri=\""
       << cfg.publish_url << "\"";
    return os.str();
}

// -- protocol ----------------------------------------------------------------

namespace {

OutputConfig parse_output(const json& j) {
    OutputConfig cfg;
    cfg.width = j.value("width", proto::kDefaultWidth);
    cfg.height = j.value("height", proto::kDefaultHeight);
    cfg.fps = j.value("fps", proto::kDefaultFps);
    cfg.pixel_format = j.value("pixel_format", std::string(proto::kDefaultPixelFormat));
    cfg.encoder = j.value("encoder", std::string(proto::kDefaultEncoder));
    cfg.video_bitrate_kbps = j.value("video_bitrate_kbps", proto::kDefaultBitrateKbps);
    cfg.publish_url = j.value("publish_url", cfg.publish_url);
    return cfg;
}

}  // namespace

Command parse_command(const std::string& line) {
    Command cmd;
    json j;
    try {
        j = json::parse(line);
    } catch (const json::exception& e) {
        cmd.parse_error = true;
        cmd.parse_message = std::string("invalid json: ") + e.what();
        return cmd;
    }
    if (!j.is_object() || !j.contains("cmd") || !j["cmd"].is_string()) {
        cmd.parse_error = true;
        cmd.parse_message = "missing string field 'cmd'";
        return cmd;
    }
    cmd.cmd = j["cmd"].get<std::string>();

    if (cmd.cmd == proto::kCmdStart) {
        cmd.output = parse_output(j);
    } else if (cmd.cmd == proto::kCmdSetInputs) {
        if (j.contains("inputs") && j["inputs"].is_array()) {
            for (const auto& item : j["inputs"]) {
                InputSpec spec;
                spec.id = item.value("id", std::string{});
                spec.url = item.value("url", std::string{});
                spec.source_type = item.value("source_type", std::string(proto::kSourceSrt));
                spec.loop = item.value("loop", false);
                if (!spec.id.empty() && !spec.url.empty()) {
                    cmd.inputs.push_back(std::move(spec));
                }
            }
        }
    } else if (cmd.cmd == proto::kCmdTake) {
        cmd.input_id = j.value("input_id", std::string{});
    }
    return cmd;
}

std::string serialize_ack(const std::string& cmd, bool ok) {
    json j;
    j["type"] = proto::kMsgAck;
    j["cmd"] = cmd;
    j["ok"] = ok;
    return j.dump();
}

std::string serialize_status(const StatusReport& status) {
    json j;
    j["type"] = proto::kMsgStatus;
    j["state"] = status.state;
    if (status.active_input.has_value()) {
        j["active_input"] = *status.active_input;
    } else {
        j["active_input"] = nullptr;
    }
    j["negotiated_caps"] = status.negotiated_caps;
    j["encoder"] = status.encoder;
    j["output_url"] = status.output_url;
    j["fps"] = status.fps;
    j["pushed_frames"] = status.pushed_frames;
    j["dropped_frames"] = status.dropped_frames;
    j["black_frames"] = status.black_frames;
    if (status.last_error.has_value()) {
        j["last_error"] = *status.last_error;
    } else {
        j["last_error"] = nullptr;
    }
    return j.dump();
}

std::string serialize_error(const ErrorReport& error) {
    json j;
    j["type"] = proto::kMsgError;
    j["message"] = error.message;
    j["source_url"] = error.source_url;
    j["element"] = error.element;
    j["requested_caps"] = error.requested_caps;
    return j.dump();
}

}  // namespace mediacore
