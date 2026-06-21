# Getting real SRT into the matrix

The matrix reads each input as a low-resolution JPEG frame tap produced by
FFmpeg. There are two ways to feed it.

## Option A - Direct SRT (no extra software)

The matrix itself listens on one SRT port per input. Start the backend with the
direct-SRT config:

```powershell
obs-matrix serve --port 8800 --media configs/media.srt.example.yaml --simulate
```

Inputs listen on:

- `input_01` -> `srt://<this-pc>:9001`
- `input_02` -> `srt://<this-pc>:9002`
- `input_03` -> `srt://<this-pc>:9003`

Then push from any software encoder as an SRT **caller**.

FFmpeg test pattern (a real software encoder):

```powershell
ffmpeg -re -f lavfi -i testsrc2=size=1280x720:rate=30 `
  -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p `
  -f mpegts "srt://127.0.0.1:9001?mode=caller&pkt_size=1316"
```

OBS as the encoder (Settings -> Stream):

- Service: `Custom`
- Server: `srt://127.0.0.1:9001?streamid=&latency=200`
- Click `Start Streaming`. OBS pushes as an SRT caller into `input_01`.

Within a few seconds the input slot flips to `active` and its tile shows live
video. Stop the encoder and the slot flips to `missing_signal`; the Routing
Engine then refuses to route to it.

## Option B - MediaMTX hub (recommended for many encoders)

MediaMTX accepts many SRT publishers and republishes them so the matrix can read
them back over RTSP. This is the production path.

1. Install MediaMTX (single binary): https://github.com/bluenviron/mediamtx/releases
2. Run it with the provided config:

```powershell
mediamtx configs/mediamtx.yml
```

3. Start the backend pointed at the MediaMTX config:

```powershell
obs-matrix serve --port 8800 --media configs/media.example.yaml --simulate
```

4. Publish SRT into MediaMTX as a caller, using `streamid=publish:<input_id>`:

```powershell
ffmpeg -re -f lavfi -i testsrc2=size=1280x720:rate=30 `
  -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p `
  -f mpegts "srt://127.0.0.1:8890?streamid=publish:input_01&pkt_size=1316"
```

OBS into MediaMTX (Settings -> Stream):

- Server: `srt://127.0.0.1:8890?streamid=publish:input_01`

MediaMTX republishes each path on `rtsp://127.0.0.1:8554/<input_id>`, which is
what `configs/media.example.yaml` tells the matrix to read. To read SRT directly
from MediaMTX instead of RTSP, set an input `read_url` to
`srt://127.0.0.1:8890?streamid=read:input_01`.

## Option C - Configure SRT per input from the UI (no config file)

Start the backend without a media file and set SRT per input from the
**Configuration** panel:

```powershell
obs-matrix serve --port 8800 --simulate
```

For each input choose an SRT mode and save:

- **Listener** - the matrix binds a local port; point an encoder at
  `srt://<this-pc>:<port>` as a caller. Only the port is needed.
- **Caller** - the matrix dials a remote `IP:port` (e.g. a MediaMTX or encoder
  output). Set IP/host and port.
- **Off** - stop ingest for that input.

Saving (re)starts that input's FFmpeg worker immediately and persists to
`configs/matrix.runtime.json`, so it survives restarts. This is backed by
`POST /api/config/input` with `source_mode` (`srt_listener`/`srt_caller`),
`srt_host`, `srt_port`, `srt_latency`. The legacy `srt_mode` field is still
accepted and mapped automatically.

## Option D - Local file / VOD per input (offline testing & video bits)

Any input can play a local video file instead of a live stream. Use this to
test the whole pipeline with a downloaded CS2 stream, or to let the director
drop in a pre-rendered video bit on a slot.

In the control plane **Configuration** panel, for an input:

1. Set **Source** to `Local file / VOD`.
2. Click **Browse…** to pick a video file from the machine running the backend
   (navigate drives/folders; only video files are shown), or paste a path
   directly (e.g. `C:\streams\cs2_demo.mp4`). Paths are operator-provided backend
   paths; nothing is uploaded from your browser. Backed by `GET /api/files`.
3. Optionally enable **Loop playback** so a short clip or test stream repeats
   forever.
4. **Save**. The input plays in real time (FFmpeg `-re`) and its tile goes live.
5. **Take** it to program like any other input. OBS keeps reading the same
   stable program media source URL; Excalibur swaps the file in behind it.

File inputs are played in real time and, for the program feed, transcoded
(`libx264`/`aac`) so arbitrary file codecs publish reliably. This is backed by
`POST /api/config/input` with `source_mode: file`, `file_path`, and `loop`.

RTSP and plain stream URLs (HLS/HTTP) are also selectable as `RTSP stream` and
`URL / HLS stream`; both read from a `source_url`.

## Configurable matrix size (inputs and outputs)

The matrix is no longer fixed at 11x11. It defaults to 11 inputs / 11 outputs,
but you can add or remove slots from the **Configuration** panel under
*Matrix size*:

- **+ Add input / + Add output** appends the next sequential slot
  (`input_12`, `output_12`, ...).
- **Remove last input / output** removes only the highest-numbered slot, and
  only when it is not currently routed (the program output can never be
  removed).

Counts persist to `configs/matrix.runtime.json` (`input_count`, `output_count`)
and are applied on the next start. Missing counts default to 11, so older
runtime files keep working without any migration. This is backed by
`POST /api/config/matrix` with `input_count` and/or `output_count`.

## How it works internally

- `matrix/media/ingest.py` spawns one FFmpeg process per input:
  `ffmpeg -i <read_url> -vf "fps=N,scale=W:-2" -update 1 -y .preview/<input_id>.jpg`
- The ingest manager checks each preview file's freshness every 250 ms and sets
  the slot `active` (fresh) or `missing_signal` (stale/missing), with watchdog
  restart of dead FFmpeg workers.
- The backend serves the latest frame at `GET /api/preview/<input_id>.jpg`.
- The control plane renders those frames as tiles, refreshed each poll.

## Program output to OBS (the broadcast video path)

The matrix separates two video paths so the agent and the broadcast never share
a pipeline:

- **Agent / preview path** - the low-res JPEG frame taps above. Used by the
  director agent and the multiview tiles only.
- **Program path** - one full-quality feed that Excalibur publishes for OBS. The
  routing engine decides which input is on program; an FFmpeg worker
  (`matrix/media/output.py`) republishes that input to a single stable URL.

OBS adds **one Media Source** pointed at that stable URL and never changes it.
When the routing engine cuts to a different input, Excalibur swaps the content
behind the URL; OBS keeps owning the layout, overlays, branding, and audio.

By default the program output binds an SRT listener:

- Publish (Excalibur side): `srt://0.0.0.0:9999?mode=listener&latency=200`
- OBS Media Source (read side): `srt://<this-pc>:9999?mode=caller&latency=200`

The exact OBS URL is shown (with a copy button) in the control plane
**Configuration** panel under *Program output → OBS*, and in
`GET /api/config` as `program.obs_url`. Override the publish URL by setting
`program_publish_url` in `configs/matrix.runtime.json` (for example to publish
into MediaMTX as `srt://127.0.0.1:8890?streamid=publish:program`).

Notes and constraints:

- The program publisher only starts FFmpeg once real media ingest is enabled
  (`--media ...` or per-input SRT configured). In `--simulate` mode it reports
  the intended source but stays idle.
- It reads each input from the same `uri` the frame taps use. With direct
  SRT-listener inputs only one consumer can read the port, so use the MediaMTX
  hub (Option B) when you want both the agent preview and the program feed.
- `-c copy` is used by default to keep source quality and minimise latency/CPU.
- A cut restarts the program FFmpeg worker, so expect a brief transition on each
  switch; a persistent compositor is a later improvement.

## Program output as an OBS device (virtual camera)

Some operators prefer to pick the program feed from OBS' **Video Capture
Device** list (like a webcam) instead of adding an SRT Media Source. Excalibur
can also expose the same routed program feed as a Windows virtual-camera device
named **Excalibur**.

This is an *optional second path* and runs independently of the SRT Media Source
above. It uses the open-source
[akvirtualcamera](https://github.com/webcamoid/akvirtualcamera) DirectShow
virtual camera, fed by an FFmpeg decode pipeline.

> **For OBS, prefer the SRT Media Source above.** A virtual camera is a
> video-only, webcam-quality DirectShow device and is a downgrade for the
> broadcast path. Only use the device output when a *third-party* app (Zoom,
> Discord, another machine's OBS) needs the feed as a real camera.
>
> **Windows 11 caveat:** akvirtualcamera's Media Foundation backend is broken on
> Windows 11 (`IMFActivate::ActivateObject()` returns `E_ACCESSDENIED`), and on
> some builds its DirectShow filter fails to register (`regsvr32` →
> `DllRegisterServer` error). If the `Excalibur` device never appears in OBS'
> *Video Capture Device* list after registration, this is the cause — fall back
> to the SRT Media Source, which always works.

### One-time setup on the broadcast machine

1. Install akvirtualcamera (download the Windows installer from its releases
   page). This registers `AkVCamManager` on the system.
2. Register the Excalibur device once, **as Administrator** (these exact
   commands are shown with a copy button in the control plane *Configuration*
   panel under *Program output → OBS device*):

```powershell
AkVCamManager add-device "Excalibur" --id Excalibur
AkVCamManager add-format Excalibur RGB24 1920 1080 30
AkVCamManager update
```

On Windows the installer drops `AkVCamManager.exe` under
`C:\Program Files\akvirtualcamera\x64\` and does **not** add it to `PATH`. The
backend auto-discovers it there, but to run the commands above by hand either
use the full path or `cd` into that folder first. `update` registers a
system-wide DirectShow filter, so it must run from an **elevated** (Admin)
terminal.

### Enable it

In the control plane **Configuration** panel, under *Program output → OBS
device*, tick **Expose Excalibur as an OBS device**. This is backed by
`POST /api/config/program` with `virtualcam_enabled` (plus optional
`virtualcam_device`, `virtualcam_width`, `virtualcam_height`, `virtualcam_fps`,
`virtualcam_manager_bin`) and persists to `configs/matrix.runtime.json` under
`virtualcam`.

When enabled (and real media ingest is running), the backend decodes the routed
program feed and pipes raw frames into the Excalibur device:

```text
ffmpeg -i <routed input> -an -vf scale=W:H,fps=N,format=rgb24 -f rawvideo -pix_fmt rgb24 pipe:1
  | AkVCamManager stream --fps N Excalibur RGB24 W H
```

In OBS, add a **Video Capture Device** source and choose **Excalibur**. The
device only carries video; broadcast audio still flows through OBS exactly as
before. Like the SRT program path, switching inputs restarts the pipeline, so a
cut causes a brief transition.

Notes and constraints:

- Registration touches the system DirectShow registry and requires elevation, so
  the backend never runs it for you; it only reports the commands and whether
  `AkVCamManager` is on `PATH`.
- The virtual camera follows the *same routed input* as the SRT program output.
  With direct SRT-listener inputs only one consumer can read a port, so use the
  MediaMTX hub (Option B) if you want the SRT Media Source, the device, and the
  agent preview all live at once.
- akvirtualcamera is GPLv3; review its license before bundling it in an
  installer.

## Notes

- The frame tap is intentionally low-res (~480px, ~5 fps): it is the agent's and
  multiview's preview path, not the program-quality video.
- For program-quality video to OBS, use the program output above (point an OBS
  media source at `program.obs_url`). OBS WebSocket scene switching remains wired
  for broad layout changes (gameplay/break/replay), but is no longer how the
  program video itself is selected.
- Full-motion video directly in the browser (WebRTC/HLS) is a later extension;
  MediaMTX already exposes those endpoints if you want them.
