# media-core

`media-core` is the C++/GStreamer process that backs Excalibur's GStreamer
program-output path. It takes whatever source is currently routed to program,
normalizes it to a fixed shape (1080p / 30fps / I420), and republishes it as one
stable MPEG-TS-over-SRT feed that OBS reads. Python
(`excalibur.matrix.media.gstreamer_output.GstProgramOutputManager`) supervises
it and talks to it over newline-delimited JSON on stdin/stdout.

See `../../.cursor/plans/` (the GStreamer Output plan) for the design rationale.

## Why a separate process

- GStreamer's C API (explicit `appsink`/`appsrc`, caps negotiation, bus
  handling, live timestamping) is far cleaner in C++ than through Python
  bindings, and keeps the media engine decoupled from the control plane.
- A crash in native media handling cannot take down the Python director.
- It gives the project a real, self-contained C++ component.

## Runtime requirements (Windows)

Install the **MSVC 64-bit** GStreamer packages from
<https://gstreamer.freedesktop.org/download/> (both are needed to build):

- `gstreamer-1.0-msvc-x86_64-<version>.msi` (runtime)
- `gstreamer-1.0-devel-msvc-x86_64-<version>.msi` (headers + import libs)

During install choose **Complete**, or at minimum ensure these plugin sets are
selected, because the output normalizer draws elements from all of them:

| Element(s)                                   | Plugin set        |
| -------------------------------------------- | ----------------- |
| `appsrc`, `appsink`                          | base (`app`)      |
| `videoconvert`, `videoscale`, `videorate`    | base              |
| `capsfilter`, `queue`, `input-selector`      | core              |
| `decodebin3`, `uridecodebin3`                | base              |
| `x264enc`                                    | ugly              |
| `h264parse`, `mpegtsmux`, `tsdemux`          | bad               |
| `srtsrc`, `srtsink`                          | bad (needs libsrt)|
| `avdec_h264` (software decode fallback)      | libav             |
| `nvh264enc` / `nvh264dec` / `d3d11h264dec`   | bad (optional HW) |

> SRT caveat: `srtsrc`/`srtsink` only appear when the GStreamer build was
> compiled against libsrt. The official MSVC builds include it; some minimal or
> third-party builds do not. Verify with `gst-inspect-1.0 srtsink`.

### Verifying the environment

From Python (used by the manager and the status endpoint):

```python
from excalibur.matrix.media.gst_probe import describe_environment
print(describe_environment())
```

Or directly:

```powershell
gst-inspect-1.0 --exists srtsink; echo $LASTEXITCODE   # 0 == present
gst-inspect-1.0 --exists x264enc; echo $LASTEXITCODE
gst-inspect-1.0 --exists appsrc;  echo $LASTEXITCODE
```

## Building

Requires CMake >= 3.20 and a C++17 compiler. The build discovers GStreamer via
`pkg-config` (Linux/macOS or MSYS2) or via the `GSTREAMER_ROOT`/`PKG_CONFIG_PATH`
environment set by the Windows MSVC installer.

```powershell
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release
```

The resulting `media-core` (or `media-core.exe`) is what
`GstProgramOutputManager` looks for. Point it explicitly with the
`EXCALIBUR_MEDIA_CORE` environment variable, or drop the binary on PATH / in the
repo `native/media-core/build/` tree.

### Building without SRT support

If your GStreamer build lacks `srtsink`, you can still exercise the pipeline by
publishing to a file or UDP sink; the JSON `publish_url` is passed through to the
sink `uri`, so pointing it at `udp://...` works for local testing.

## Control protocol

Newline-delimited JSON. Commands arrive on stdin; acks, status, and errors are
emitted on stdout. See `src/control_protocol.hpp` for the authoritative
constants and `../../src/excalibur/matrix/media/media_core_protocol.py` for the
Python mirror.

Commands:

```json
{"cmd":"start","publish_url":"srt://0.0.0.0:9999?mode=listener&latency=200","width":1920,"height":1080,"fps":30,"video_bitrate_kbps":6000,"encoder":"x264enc","pixel_format":"I420"}
{"cmd":"set_inputs","inputs":[{"id":"input_01","url":"rtsp://127.0.0.1:8554/input_01","source_type":"rtsp"}]}
{"cmd":"take","input_id":"input_01"}
{"cmd":"status"}
{"cmd":"stop"}
```

Responses:

```json
{"type":"ack","cmd":"start","ok":true}
{"type":"status","state":"running","active_input":"input_01","negotiated_caps":"video/x-raw, format=I420, width=1920, height=1080, framerate=30/1","encoder":"x264enc","output_url":"srt://0.0.0.0:9999?mode=listener&latency=200","fps":29.97,"pushed_frames":1234,"dropped_frames":0,"black_frames":12,"last_error":null}
{"type":"error","message":"caps negotiation failed","source_url":"rtsp://...","element":"videoscale","requested_caps":"video/x-raw, ..."}
```

## Pipeline shape

Constant-rate output decoupled from the source via the app boundary:

```
[ source ] -> decodebin3 -> videoconvert ! videoscale ! videorate
           -> capsfilter(video/x-raw,1080p30,I420) -> appsink   (decode side)

appsrc(same caps, is-live, driven at 30fps by a feeder clock)
           -> queue -> x264enc -> h264parse -> mpegtsmux -> srtsink   (encode side)
```

A feeder thread clocks `appsrc` at exactly the target framerate, pushing either
the latest decoded frame or a generated black frame if the source is stalled or
switching. This makes the SRT output continuous across cuts and source loss, and
keeps switching seamless (swap the source pipeline behind the app boundary while
the output never stops).
