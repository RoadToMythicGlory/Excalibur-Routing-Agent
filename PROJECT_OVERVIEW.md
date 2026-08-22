# Excalibur Project Overview

Excalibur is a gaming-contest broadcast platform for managing multiple player feeds, routing them through a production matrix, and assisting operators with live broadcast decisions.

The project is designed for player-based gaming events where each participant can stream a selected game or application window into a central production system. Operators can then preview feeds, monitor stream health, route sources, apply manual overrides, and optionally use automated director logic to help choose the best live view.

## What The Project Does

Excalibur combines a player streaming client, a backend routing service, an operator control plane, and media-processing components into one broadcast workflow.

At a high level:

- Players use a lightweight Windows client to choose a game or app window, select audio sources, enter a destination IP and port, and start streaming.
- The backend receives and tracks incoming feeds, maintains the production matrix, manages routing decisions, and exposes state to the operator UI.
- Operators use a browser-based control plane to inspect inputs, previews, routes, latency, alerts, story signals, and routing audit history.
- Media components normalize, preview, switch, and publish video streams for OBS or other production tools.
- Automated director logic can evaluate stream state, game/event signals, operator rules, and feed health before suggesting or applying routing changes.

The goal is to keep the player experience simple while giving operators the control and observability needed for a live event.

## Main Components

### Player Encoder Client

The player encoder client is Galahad, a production Windows desktop encoder built with Tauri, React, TypeScript, and Rust. It is the player-facing capture and contribution app for sending low-latency feeds into the event infrastructure.

It is responsible for:

- Capturing a selected game or application window.
- Capturing cameras, capture cards, or virtual video devices when needed.
- Capturing and mixing game/app audio, voice chat audio, and microphone input.
- Encoding video and audio into a broadcast-friendly stream.
- Connecting to an operator-provided destination over SRT.
- Planning a broadcast-oriented ST 2110-style RTP path when that workflow is enabled.
- Reporting telemetry, diagnostics, connection state, and capability information.
- Accepting operator commands such as restart, stop, switch quality, upload logs, or apply pushed config.
- Recovering from common problems such as stream drops, app restarts, network loss, or encoder failures.

The client intentionally hides broadcast concepts from players. A player should not need to understand matrix inputs, routing, OBS scenes, or streaming protocol details. They only need to identify themselves, select sources, and connect.

#### Full Galahad Encoder Version

The full Galahad version is more than a simple screen streamer. It is a capability-driven Windows encoder with a Rust media runtime and a React/Tauri UI.

Key capabilities include:

- Low-latency SRT output using H.264 or HEVC video with AAC audio over MPEG-TS.
- SRT caller and listener modes, including latency controls and listener fan-out planning.
- Windows Graphics Capture for game or app windows.
- DirectShow capture for cameras, capture cards, and virtual video devices.
- WASAPI-based audio capture and device enumeration.
- Independent audio planning for game/app audio, voice chat audio, microphone input, and fallback system mix.
- Hardware encoder probing and selection across NVENC, Intel QSV, AMD AMF, and software fallback.
- FFmpeg argument planning for stable, low-latency encode and transmit behavior.
- GStreamer ST 2110-style planning for RFC 4175 video and L24 audio over RTP/UDP.
- SDP generation for broadcast receivers in ST 2110-style workflows.
- A Rust pipeline supervisor that owns process lifecycle, reconnect behavior, quality switching, and remote config changes.
- A WebSocket control channel for telemetry, command acknowledgements, remote restart, quality switching, pushed config, diagnostics, and log upload.
- A capability-driven UI that shows only the transports, encoders, devices, and audio modes available on the current machine.

The full runtime shape is:

```text
React/Tauri UI
    |
    | Tauri commands
    v
Rust app state
    |
    | validates config, capabilities, identity, sources
    v
Pipeline supervisor
    |
    +-- SRT window path: Windows Graphics Capture -> raw frames -> FFmpeg
    +-- SRT device path: DirectShow device -> FFmpeg
    +-- Broadcast RTP path: DirectShow device -> GStreamer RTP essence
    |
    v
Telemetry, command acknowledgements, logs, diagnostics
```

The supervisor owns all long-running media processes and threads. Start, stop, restart, reconnect, quality switch, and pushed configuration all converge there so media processes do not leak across UI actions, source failures, or network interruptions.

### Backend Matrix Service

The backend is a Python service that owns the production matrix, routing state, event buses, media input configuration, and control API.

It provides:

- A control API for the operator UI.
- Input and output slot management.
- Preview frame access for incoming feeds.
- Operator commands such as take, hold, lock, and emergency route.
- Automated routing intent arbitration.
- Latency tracking and decision audit logs.
- Optional OBS WebSocket integration for scene or source control.

The backend uses a bus-driven design so different parts of the system can publish facts, operator commands, automation rules, and director decisions without tightly coupling everything together.

### Operator Control Plane

The control plane is a Next.js and React web app for live event operators.

It shows:

- Matrix inputs and outputs.
- Current routes and on-air state.
- Feed previews.
- Operator controls.
- Latency budget status.
- Routing decision history.
- Story or event signals from the director pipeline.
- Configuration panels for inputs, outputs, and program output.

The UI reads backend state through HTTP API endpoints and sends operator actions back to the routing engine, where they are handled alongside automation and director decisions.

### Media Core

The native media layer is a C++ and GStreamer component used for stable program output.

It can:

- Decode selected media inputs.
- Normalize video to a consistent output shape.
- Maintain a continuous output stream across route changes.
- Publish a stable MPEG-TS-over-SRT feed for OBS or other broadcast software.
- Report status and errors back to the Python supervisor over newline-delimited JSON.

Keeping this as a separate process isolates native media crashes from the Python control service and gives the project a dedicated high-performance media path.

### Director And Intelligence Layer

The director layer evaluates available information and produces routing intents.

Inputs can include:

- Feed health.
- Preview-frame signals.
- Event or game-state data.
- Operator rules.
- Manual overrides.
- Priority, confidence, recency, hold time, and cooldown logic.

The routing engine remains the final authority. Automated decisions, operator commands, and emergency actions are all arbitrated before changing the matrix.

## Technology Stack

### Backend

- Python 3.11+
- Pydantic for typed data models.
- PyYAML for configuration files.
- OpenCV and NumPy for frame analysis and visual processing.
- OBS WebSocket integration through `obsws-python`.
- In-process async-style pub/sub abstractions for internal buses.
- Pytest for backend and pipeline tests.

### Player Client

- Tauri 2 for the desktop shell.
- Rust 2021 for native Windows capture, encoder control, process supervision, media planning, and system integration.
- React 18 for the client UI.
- TypeScript for frontend application logic and typed state.
- Vite for frontend development and bundling.
- Tokio and WebSocket libraries for async control-channel runtime.
- FFmpeg for H.264/HEVC encoding, AAC audio, MPEG-TS muxing, and SRT publishing.
- SRT for low-latency contribution transport.
- GStreamer for ST 2110-style RTP essence planning.
- Windows Graphics Capture for window capture.
- DirectShow for camera, capture-card, and virtual-device capture.
- WASAPI and Windows audio APIs for audio capture, device enumeration, and mixing support.
- Capability probing for GPU encoder selection and feature availability.

### Operator Control Plane

- Next.js 14.
- React 18.
- TypeScript.
- HTTP polling/API integration with the backend service.
- Component-based panels for routing, audit, latency, configuration, and operator controls.

### Native Media

- C++17.
- GStreamer for media decoding, normalization, encoding, and SRT output.
- CMake for native builds.
- Newline-delimited JSON for the control protocol between Python and the media process.

### Broadcast And Transport

- OBS as the main production/broadcast surface.
- OBS WebSocket for scene/source control.
- SRT caller and listener workflows for low-latency contribution and program feeds.
- MPEG-TS over SRT for stable handoff into broadcast software.
- FFmpeg worker processes for ingest, preview generation, and encoding tasks.
- RTP/UDP essence planning for ST 2110-style broadcast workflows.
- SDP output for receivers that need explicit stream descriptions.

## Architecture Summary

The system separates player simplicity from operator control.

Players run a desktop encoder that captures only the intended sources and sends a contribution feed to the event infrastructure. The backend maps that incoming feed to matrix inputs, tracks its state, and exposes it to the operator control plane. Operators can preview and route sources manually, while automated director logic can assist by publishing routing intents based on rules, events, latency, and feed health.

All route changes pass through the routing engine. This keeps manual actions, automation, emergency overrides, and director decisions consistent and auditable.

## Why This Architecture Works

- The player client stays simple and event-friendly.
- Operators retain final control over the broadcast.
- Routing decisions are centralized and auditable.
- Media processing is isolated from control logic.
- The system can run in simulation mode for testing and demos.
- Transport and bus layers are designed so future protocols or infrastructure can be swapped in without rewriting the full product.

## Intended Use

Excalibur is intended for organized gaming contests, showmatches, tournaments, creator events, and other live productions where multiple player perspectives need to be collected, monitored, and routed into a broadcast workflow.

It is not tied to one specific game. The same architecture can support different titles or event formats by changing source configuration, event adapters, routing rules, templates, and operator workflows.
