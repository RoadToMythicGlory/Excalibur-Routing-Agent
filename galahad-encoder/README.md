# Galahad Encoder

A Windows player client that captures a game/app window plus audio and streams it
as an **SRT caller** into the OBS matrix. Players only enter a destination IP and
port; all input identity and routing stay server-side.

```
Window (WGC) ─┐
Game audio    ├─ mix ─► bundled FFmpeg ─► H.264 + AAC / MPEG-TS ─► SRT caller ─► matrix listener
Discord audio │                                   ▲
Microphone   ─┘                                   └─ Control Channel (telemetry + commands) ─► control plane
```

- **Capture**: Windows Graphics Capture (not `gdigrab`) for reliable game capture.
- **Encoder**: `EncoderBackend` with H.264 default (NVENC ▸ QSV ▸ AMF ▸ software),
  HEVC experimental (Phase 2, gated behind a flag and receiver validation).
- **Audio**: game + Discord + microphone mixed to AAC. Per-process isolation is
  used when available; otherwise a system-audio mix + mic, with a clear warning.
- **Reconnect**: always on. The supervisor recovers from game/Discord restarts,
  network loss, and FFmpeg crashes with capped exponential backoff.
- **Control Channel**: bidirectional WebSocket carrying telemetry, operator
  commands (start/stop/restart/switch quality/log upload/push config), logs and
  diagnostics. Identity is `playerId` (stable) + `sessionId` (per launch).

## Prerequisites

- Windows 10 (build 19041+) or Windows 11
- [Node.js](https://nodejs.org) 18+
- [Rust](https://rustup.rs) (MSVC toolchain) + Visual Studio Build Tools
- For HEVC / NVENC: up-to-date GPU drivers

## Setup

```powershell
cd galahad-encoder
npm install

# Download + bundle FFmpeg (one time; binary is git-ignored)
pwsh ./scripts/fetch-ffmpeg.ps1

# Generate app icons from a source PNG (one time)
npm run tauri icon ./icons/source.png
```

## Develop

```powershell
npm run tauri:dev
```

## Build the Windows installer

```powershell
npm run tauri:build
# Output: src-tauri/target/release/bundle/nsis/*.exe
```

## Test

Pure-logic Rust modules (SRT URL building, presets, encoder selection, FFmpeg
arg building, reconnect state machine, control-channel command parsing, capability
parsing, resampler) are unit tested:

```powershell
cd src-tauri
cargo test
```

Frontend type-check / bundle:

```powershell
npm run build
```

## Layout

| Path | Purpose |
|------|---------|
| `src/` | React/TypeScript UI (capability-driven) |
| `src-tauri/src/capture/` | Windows Graphics Capture backend |
| `src-tauri/src/audio/` | WASAPI capture + mixer |
| `src-tauri/src/pipeline.rs` | Supervisor: capture+audio+FFmpeg, reconnect |
| `src-tauri/src/control_channel.rs` / `control_runtime.rs` | Control Channel |
| `src-tauri/src/capability.rs` | Startup capability detection |
| `src-tauri/src/encoder.rs` / `ffmpeg.rs` / `srt.rs` | Encoder + wire format |

See [docs/PLAYER.md](docs/PLAYER.md) and [docs/OPERATOR.md](docs/OPERATOR.md).
