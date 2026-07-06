// Control protocol constants shared across the media-core process.
//
// The protocol is newline-delimited JSON: one JSON object per line inbound on
// stdin (commands) and outbound on stdout (acks / status / errors). Keeping the
// wire vocabulary in one header means the parser, the pipeline, and the status
// serializer never drift, and it mirrors the Python side in
// ../../src/excalibur/matrix/media/media_core_protocol.py -- the two MUST be
// kept in lockstep.
#pragma once

namespace mediacore::proto {

// -- inbound command names ("cmd" field) ------------------------------------
constexpr const char* kCmdStart = "start";        // build + play the output pipeline
constexpr const char* kCmdSetInputs = "set_inputs";  // define routable inputs
constexpr const char* kCmdTake = "take";          // put an input on air
constexpr const char* kCmdStatus = "status";      // request a status frame
constexpr const char* kCmdStop = "stop";          // tear everything down, exit

// -- outbound message types ("type" field) ----------------------------------
constexpr const char* kMsgAck = "ack";
constexpr const char* kMsgStatus = "status";
constexpr const char* kMsgError = "error";

// -- lifecycle states ("state" field of a status message) -------------------
// idle:         started process, no output pipeline yet.
// starting:     output pipeline is rolling to PLAYING.
// running:      output pipeline is live and clocking frames.
// reconnecting: a source dropped; output holds on black while retrying.
// stopped:      clean teardown; process is about to exit.
// error:        unrecoverable pipeline error; last_error explains why.
constexpr const char* kStateIdle = "idle";
constexpr const char* kStateStarting = "starting";
constexpr const char* kStateRunning = "running";
constexpr const char* kStateReconnecting = "reconnecting";
constexpr const char* kStateStopped = "stopped";
constexpr const char* kStateError = "error";

// -- source types (per-input "source_type" field) ---------------------------
constexpr const char* kSourceSrt = "srt";
constexpr const char* kSourceRtsp = "rtsp";
constexpr const char* kSourceUrl = "url";
constexpr const char* kSourceFile = "file";

// -- defaults ----------------------------------------------------------------
constexpr int kDefaultWidth = 1920;
constexpr int kDefaultHeight = 1080;
constexpr int kDefaultFps = 30;
constexpr int kDefaultBitrateKbps = 6000;
constexpr const char* kDefaultPixelFormat = "I420";
constexpr const char* kDefaultEncoder = "x264enc";

}  // namespace mediacore::proto
