// GStreamer-backed program output normalizer.
//
// MediaCore owns two GStreamer pipelines joined by an app boundary:
//
//   source pipeline  : <src> -> decode -> normalize -> appsink   (per active input)
//   output pipeline  : appsrc -> encode -> mux -> srtsink         (long-lived)
//
// A feeder thread clocks the appsrc at exactly the target framerate, pushing the
// most recent decoded frame or a synthesized black frame when the source is
// stalled/switching. That decoupling is what keeps the SRT output continuous
// across cuts and source loss. All GStreamer usage is confined to this TU; the
// string/JSON plumbing lives in pipeline_spec.*.
#pragma once

#include <atomic>
#include <chrono>
#include <functional>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include <gst/gst.h>
#include <gst/app/gstappsink.h>
#include <gst/app/gstappsrc.h>

#include "pipeline_spec.hpp"

namespace mediacore {

// Callback used to emit protocol messages (already-serialized JSON lines).
using EmitFn = std::function<void(const std::string&)>;

class MediaCore {
public:
    explicit MediaCore(EmitFn emit);
    ~MediaCore();

    MediaCore(const MediaCore&) = delete;
    MediaCore& operator=(const MediaCore&) = delete;

    // Command handlers (called from the stdin thread). Each returns true on
    // success; failures are additionally reported via an error message.
    bool start(const OutputConfig& cfg);
    void set_inputs(const std::vector<InputSpec>& inputs);
    bool take(const std::string& input_id);
    void stop();

    // Build and emit a status frame.
    void report_status();

private:
    // Latest raw frame handed over from the source appsink to the feeder.
    struct FrameSlot {
        std::mutex mutex;
        GstBuffer* buffer = nullptr;                  // owned; last decoded frame
        std::chrono::steady_clock::time_point arrived;  // for staleness checks
        bool caps_validated = false;
    };

    bool build_output_pipeline();
    void teardown_output_pipeline();
    bool build_source_pipeline(const InputSpec& input);
    void teardown_source_pipeline();

    // appsink new-sample callback (static trampoline + member impl).
    static GstFlowReturn on_new_sample(GstAppSink* sink, gpointer user_data);
    GstFlowReturn handle_new_sample(GstAppSink* sink);

    // Feeder thread: clocks appsrc at the configured framerate.
    void feeder_loop();
    GstBuffer* make_black_frame();
    void push_buffer(GstBuffer* buffer, bool is_black);

    // Bus polling for both pipelines (errors/EOS -> reconnect or error state).
    void bus_loop(GstElement* pipeline, const char* which, std::atomic<bool>& running);

    void emit_error(const std::string& message, const std::string& element);
    const InputSpec* find_input(const std::string& id) const;
    void set_state(const std::string& state);
    std::string current_state();

    EmitFn emit_;
    OutputConfig cfg_;
    std::string caps_str_;

    GstElement* output_pipeline_ = nullptr;
    GstElement* appsrc_ = nullptr;
    GstElement* source_pipeline_ = nullptr;
    GstElement* appsink_ = nullptr;

    std::vector<InputSpec> inputs_;
    std::optional<std::string> active_input_;

    FrameSlot slot_;
    std::thread feeder_;
    std::atomic<bool> feeding_{false};

    std::thread output_bus_thread_;
    std::thread source_bus_thread_;
    std::atomic<bool> output_bus_running_{false};
    std::atomic<bool> source_bus_running_{false};

    std::mutex state_mutex_;
    std::string state_ = "idle";
    std::optional<std::string> last_error_;

    std::atomic<long long> pushed_frames_{0};
    std::atomic<long long> dropped_frames_{0};
    std::atomic<long long> black_frames_{0};

    // Black frames older than this since the last real frame -> hold on black.
    std::chrono::milliseconds staleness_{std::chrono::milliseconds(250)};
    guint64 frame_index_ = 0;  // monotonic PTS counter for the output clock
};

}  // namespace mediacore
