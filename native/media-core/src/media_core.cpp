#include "media_core.hpp"

#include <cstring>

#include "control_protocol.hpp"

namespace mediacore {

namespace {

constexpr const char* kAppSinkName = "normalized_video";
constexpr const char* kAppSrcName = "program_video";

// Bytes per I420 frame: full Y plane + two quarter-size chroma planes.
gsize i420_frame_size(int width, int height) {
    return static_cast<gsize>(width) * height * 3 / 2;
}

}  // namespace

MediaCore::MediaCore(EmitFn emit) : emit_(std::move(emit)) {}

MediaCore::~MediaCore() { stop(); }

void MediaCore::set_state(const std::string& state) {
    std::lock_guard<std::mutex> lock(state_mutex_);
    state_ = state;
}

std::string MediaCore::current_state() {
    std::lock_guard<std::mutex> lock(state_mutex_);
    return state_;
}

const InputSpec* MediaCore::find_input(const std::string& id) const {
    for (const auto& spec : inputs_) {
        if (spec.id == id) {
            return &spec;
        }
    }
    return nullptr;
}

void MediaCore::emit_error(const std::string& message, const std::string& element) {
    ErrorReport err;
    err.message = message;
    err.element = element;
    err.requested_caps = caps_str_;
    if (active_input_.has_value()) {
        if (const InputSpec* spec = find_input(*active_input_)) {
            err.source_url = spec->url;
        }
    }
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        last_error_ = message;
    }
    if (emit_) {
        emit_(serialize_error(err));
    }
}

// -- lifecycle ---------------------------------------------------------------

bool MediaCore::start(const OutputConfig& cfg) {
    cfg_ = cfg;
    caps_str_ = build_normalize_caps(cfg_);
    set_state(proto::kStateStarting);
    if (!build_output_pipeline()) {
        set_state(proto::kStateError);
        return false;
    }
    set_state(proto::kStateRunning);
    return true;
}

bool MediaCore::build_output_pipeline() {
    const std::string desc = build_output_description(cfg_, kAppSrcName);
    GError* error = nullptr;
    output_pipeline_ = gst_parse_launch(desc.c_str(), &error);
    if (!output_pipeline_ || error) {
        emit_error(error ? error->message : "failed to build output pipeline",
                   "output_pipeline");
        if (error) g_error_free(error);
        return false;
    }
    appsrc_ = gst_bin_get_by_name(GST_BIN(output_pipeline_), kAppSrcName);
    if (!appsrc_) {
        emit_error("appsrc not found in output pipeline", kAppSrcName);
        return false;
    }

    if (gst_element_set_state(output_pipeline_, GST_STATE_PLAYING) ==
        GST_STATE_CHANGE_FAILURE) {
        emit_error("output pipeline failed to reach PLAYING", "output_pipeline");
        return false;
    }

    // Start the output bus watcher and the feeder clock.
    output_bus_running_ = true;
    output_bus_thread_ = std::thread([this] {
        bus_loop(output_pipeline_, "output", output_bus_running_);
    });

    feeding_ = true;
    frame_index_ = 0;
    feeder_ = std::thread([this] { feeder_loop(); });
    return true;
}

void MediaCore::teardown_output_pipeline() {
    feeding_ = false;
    if (feeder_.joinable()) {
        feeder_.join();
    }
    output_bus_running_ = false;
    if (output_bus_thread_.joinable()) {
        output_bus_thread_.join();
    }
    if (output_pipeline_) {
        gst_element_set_state(output_pipeline_, GST_STATE_NULL);
    }
    if (appsrc_) {
        gst_object_unref(appsrc_);
        appsrc_ = nullptr;
    }
    if (output_pipeline_) {
        gst_object_unref(output_pipeline_);
        output_pipeline_ = nullptr;
    }
}

void MediaCore::set_inputs(const std::vector<InputSpec>& inputs) {
    inputs_ = inputs;
    // If the on-air input vanished from the new membership, fall back to black.
    if (active_input_.has_value() && !find_input(*active_input_)) {
        teardown_source_pipeline();
        active_input_.reset();
    }
}

bool MediaCore::take(const std::string& input_id) {
    if (input_id.empty()) {
        // Nothing on air: the feeder keeps the output alive on black.
        teardown_source_pipeline();
        active_input_.reset();
        return true;
    }
    const InputSpec* spec = find_input(input_id);
    if (!spec) {
        emit_error("take referenced unknown input: " + input_id, "take");
        return false;
    }
    teardown_source_pipeline();
    active_input_ = input_id;
    if (!build_source_pipeline(*spec)) {
        // Keep the output alive on black even if the source failed to start.
        set_state(proto::kStateReconnecting);
        return false;
    }
    set_state(proto::kStateRunning);
    return true;
}

bool MediaCore::build_source_pipeline(const InputSpec& input) {
    const std::string desc = build_source_description(input, cfg_, kAppSinkName);
    GError* error = nullptr;
    source_pipeline_ = gst_parse_launch(desc.c_str(), &error);
    if (!source_pipeline_ || error) {
        emit_error(error ? error->message : "failed to build source pipeline",
                   "source_pipeline");
        if (error) g_error_free(error);
        return false;
    }
    appsink_ = gst_bin_get_by_name(GST_BIN(source_pipeline_), kAppSinkName);
    if (!appsink_) {
        emit_error("appsink not found in source pipeline", kAppSinkName);
        return false;
    }

    GstAppSinkCallbacks callbacks = {};
    callbacks.new_sample = &MediaCore::on_new_sample;
    gst_app_sink_set_callbacks(GST_APP_SINK(appsink_), &callbacks, this, nullptr);

    {
        std::lock_guard<std::mutex> lock(slot_.mutex);
        slot_.caps_validated = false;
    }

    if (gst_element_set_state(source_pipeline_, GST_STATE_PLAYING) ==
        GST_STATE_CHANGE_FAILURE) {
        emit_error("source pipeline failed to reach PLAYING", "source_pipeline");
        return false;
    }

    source_bus_running_ = true;
    source_bus_thread_ = std::thread([this] {
        bus_loop(source_pipeline_, "source", source_bus_running_);
    });
    return true;
}

void MediaCore::teardown_source_pipeline() {
    source_bus_running_ = false;
    if (source_bus_thread_.joinable()) {
        source_bus_thread_.join();
    }
    if (source_pipeline_) {
        gst_element_set_state(source_pipeline_, GST_STATE_NULL);
    }
    if (appsink_) {
        gst_object_unref(appsink_);
        appsink_ = nullptr;
    }
    if (source_pipeline_) {
        gst_object_unref(source_pipeline_);
        source_pipeline_ = nullptr;
    }
    // Drop any pending decoded frame so the feeder falls back to black.
    std::lock_guard<std::mutex> lock(slot_.mutex);
    if (slot_.buffer) {
        gst_buffer_unref(slot_.buffer);
        slot_.buffer = nullptr;
    }
}

void MediaCore::stop() {
    teardown_source_pipeline();
    teardown_output_pipeline();
    set_state(proto::kStateStopped);
}

// -- appsink: pull decoded frames into the slot ------------------------------

GstFlowReturn MediaCore::on_new_sample(GstAppSink* sink, gpointer user_data) {
    return static_cast<MediaCore*>(user_data)->handle_new_sample(sink);
}

GstFlowReturn MediaCore::handle_new_sample(GstAppSink* sink) {
    GstSample* sample = gst_app_sink_pull_sample(sink);
    if (!sample) {
        return GST_FLOW_OK;
    }

    // Validate the negotiated caps once; the upstream capsfilter guarantees the
    // shape, but we assert it so a graph change can never silently feed the
    // encoder a differently-shaped buffer.
    if (!slot_.caps_validated) {
        GstCaps* caps = gst_sample_get_caps(sample);
        if (caps) {
            gchar* caps_desc = gst_caps_to_string(caps);
            GstStructure* s = gst_caps_get_structure(caps, 0);
            gint w = 0, h = 0;
            gst_structure_get_int(s, "width", &w);
            gst_structure_get_int(s, "height", &h);
            if (w != cfg_.width || h != cfg_.height) {
                emit_error(std::string("unexpected appsink caps: ") +
                               (caps_desc ? caps_desc : "unknown"),
                           "appsink");
            }
            if (caps_desc) g_free(caps_desc);
        }
        slot_.caps_validated = true;
    }

    GstBuffer* buffer = gst_sample_get_buffer(sample);
    if (buffer) {
        GstBuffer* copy = gst_buffer_copy_deep(buffer);
        std::lock_guard<std::mutex> lock(slot_.mutex);
        if (slot_.buffer) {
            gst_buffer_unref(slot_.buffer);
            dropped_frames_++;  // previous frame never made it to output
        }
        slot_.buffer = copy;
        slot_.arrived = std::chrono::steady_clock::now();
    }
    gst_sample_unref(sample);
    return GST_FLOW_OK;
}

// -- feeder: clock the output at a fixed rate --------------------------------

GstBuffer* MediaCore::make_black_frame() {
    const gsize size = i420_frame_size(cfg_.width, cfg_.height);
    GstBuffer* buffer = gst_buffer_new_allocate(nullptr, size, nullptr);
    GstMapInfo map;
    if (gst_buffer_map(buffer, &map, GST_MAP_WRITE)) {
        const gsize y_size = static_cast<gsize>(cfg_.width) * cfg_.height;
        // I420 studio-range black: Y = 16, Cb = Cr = 128.
        std::memset(map.data, 16, y_size);
        std::memset(map.data + y_size, 128, size - y_size);
        gst_buffer_unmap(buffer, &map);
    }
    return buffer;
}

void MediaCore::push_buffer(GstBuffer* buffer, bool is_black) {
    const GstClockTime duration = GST_SECOND / cfg_.fps;
    GST_BUFFER_PTS(buffer) = frame_index_ * duration;
    GST_BUFFER_DTS(buffer) = GST_BUFFER_PTS(buffer);
    GST_BUFFER_DURATION(buffer) = duration;
    frame_index_++;

    GstFlowReturn ret = gst_app_src_push_buffer(GST_APP_SRC(appsrc_), buffer);
    if (ret == GST_FLOW_OK) {
        pushed_frames_++;
        if (is_black) {
            black_frames_++;
        }
    }
}

void MediaCore::feeder_loop() {
    const auto frame_period =
        std::chrono::nanoseconds(1'000'000'000LL / cfg_.fps);
    auto next = std::chrono::steady_clock::now();

    while (feeding_) {
        next += frame_period;

        GstBuffer* to_push = nullptr;
        bool is_black = true;
        {
            std::lock_guard<std::mutex> lock(slot_.mutex);
            const bool fresh =
                slot_.buffer != nullptr &&
                (std::chrono::steady_clock::now() - slot_.arrived) < staleness_;
            if (fresh) {
                // Re-clock the decoded frame onto the output timeline. Copy so a
                // late replacement in the slot never races the push.
                to_push = gst_buffer_copy_deep(slot_.buffer);
                is_black = false;
            }
        }
        if (!to_push) {
            to_push = make_black_frame();
            if (current_state() == proto::kStateRunning && active_input_.has_value()) {
                set_state(proto::kStateReconnecting);
            }
        } else if (current_state() == proto::kStateReconnecting) {
            set_state(proto::kStateRunning);
        }

        if (appsrc_) {
            push_buffer(to_push, is_black);
        } else {
            gst_buffer_unref(to_push);
        }

        std::this_thread::sleep_until(next);
    }
}

// -- bus ---------------------------------------------------------------------

void MediaCore::bus_loop(GstElement* pipeline, const char* which,
                         std::atomic<bool>& running) {
    GstBus* bus = gst_element_get_bus(pipeline);
    while (running) {
        GstMessage* msg = gst_bus_timed_pop_filtered(
            bus, 100 * GST_MSECOND,
            static_cast<GstMessageType>(GST_MESSAGE_ERROR | GST_MESSAGE_EOS));
        if (!msg) {
            continue;
        }
        if (GST_MESSAGE_TYPE(msg) == GST_MESSAGE_ERROR) {
            GError* err = nullptr;
            gchar* debug = nullptr;
            gst_message_parse_error(msg, &err, &debug);
            const std::string element =
                GST_OBJECT_NAME(GST_MESSAGE_SRC(msg));
            emit_error(std::string(which) + " error: " +
                           (err ? err->message : "unknown"),
                       element);
            if (err) g_error_free(err);
            if (debug) g_free(debug);
            set_state(proto::kStateReconnecting);
        } else if (GST_MESSAGE_TYPE(msg) == GST_MESSAGE_EOS) {
            // Source EOS (e.g. a file ended). The feeder holds black; the
            // manager decides whether to loop/re-take.
            set_state(proto::kStateReconnecting);
        }
        gst_message_unref(msg);
    }
    gst_object_unref(bus);
}

// -- status ------------------------------------------------------------------

void MediaCore::report_status() {
    StatusReport status;
    {
        std::lock_guard<std::mutex> lock(state_mutex_);
        status.state = state_;
        status.last_error = last_error_;
    }
    status.active_input = active_input_;
    status.negotiated_caps = caps_str_;
    status.encoder = cfg_.encoder;
    status.output_url = cfg_.publish_url;
    status.fps = static_cast<double>(cfg_.fps);
    status.pushed_frames = pushed_frames_.load();
    status.dropped_frames = dropped_frames_.load();
    status.black_frames = black_frames_.load();
    if (emit_) {
        emit_(serialize_status(status));
    }
}

}  // namespace mediacore
