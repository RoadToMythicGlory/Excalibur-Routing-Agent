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
`POST /api/config/input` with `srt_mode`, `srt_host`, `srt_port`, `srt_latency`.

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

## Notes

- The frame tap is intentionally low-res (~480px, ~5 fps): it is the agent's and
  multiview's preview path, not the program-quality video.
- For program-quality video to OBS, use the program output above (point an OBS
  media source at `program.obs_url`). OBS WebSocket scene switching remains wired
  for broad layout changes (gameplay/break/replay), but is no longer how the
  program video itself is selected.
- Full-motion video directly in the browser (WebRTC/HLS) is a later extension;
  MediaMTX already exposes those endpoints if you want them.
