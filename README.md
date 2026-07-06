# Excalibur

Python MVP for an autonomous CS2 stream director. The service watches multiple screens at the same time, detects configured visual events, chooses the best live scene, and can switch OBS scenes through OBS WebSocket without manual approval.

The first MVP is intentionally rule-based. It uses OpenCV-friendly rules such as motion, HUD color, brightness, and template matching, so each CS2 setup can be tuned without training a model.

## What The CS2 MVP Does

- Captures multiple monitor sources in parallel.
- Evaluates CS2 event rules from `configs/games/cs2.yaml`.
- Chooses scenes with director logic: priority, confidence, stable frames, hold time, cooldown, and fallback.
- Starts in OBS dry-run mode by default so decisions are logged before OBS is controlled.
- Switches OBS scenes automatically when `obs.dry_run` is set to `false`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

In OBS:

1. Enable WebSocket server in `Tools > WebSocket Server Settings`.
2. Keep the default port `4455` or update `configs/app.example.yaml`.
3. Create scenes matching the example names:
   - `CS2 - Observer`
   - `CS2 - Player 1`
   - `CS2 - Player 2`

## Run

First, find the monitor indices to use in your source config:

```powershell
excalibur --list-monitors
```

Dry-run mode logs scene decisions without changing OBS:

```powershell
excalibur --config configs/app.example.yaml --log-level DEBUG
```

When dry-run is active you get a throttled status heartbeat that shows the live
scene and the strongest confidence seen per event since the last heartbeat. A
`*` marks events that crossed `min_confidence`:

```
[status] live_scene=CS2 - Observer | peak confidences: player_1_low_health=0.91*, player_2_active_combat=0.40
```

Adjust the heartbeat with `--status-interval SECONDS` (use `0` to disable).

After the profile is tuned, either set `obs.dry_run: false` in
`configs/app.example.yaml`, or override the mode from the command line without
editing the file:

```powershell
# Force live OBS control
excalibur --config configs/app.example.yaml --live

# Force dry-run even if the config enables live control
excalibur --config configs/app.example.yaml --dry-run
```

In live mode the agent connects to OBS, verifies that every scene referenced by
the config actually exists, and warns about any missing scenes before it starts
switching automatically.

## Tuning CS2 Priority Events

Edit `configs/games/cs2.yaml`.

Each event maps visual rules to an OBS scene and a director priority:

```yaml
- name: "player_1_critical_survival"
  category: "survival"
  scene: "CS2 - Player 1"
  priority: 90
  hold_sec: 6.0
  stable_frames: 2
  interrupt: true
  rules:
    - type: "region_color"
      source: "player_1"
      region: [40, 930, 220, 95]
      lower_bgr: [0, 0, 120]
      upper_bgr: [90, 90, 255]
      min_ratio: 0.18
```

Regions are `[x, y, width, height]` in the captured source image. If your CS2 resolution or HUD scale is different, these regions must be adjusted.

### Priority Ladder

The director uses priority first, then confidence, then recency. The default CS2
profile uses this ladder:

- `90`: Critical survival / clutch moment, such as very low health while active.
- `80`: Sniper action, such as scoped AWP/scout engagement.
- `70`: Utility throw or impact, such as molotov, incendiary, HE, flash, or smoke.
- `40`: Generic active combat or heavy movement.
- `10`: Round transition, scoreboard, buy time, or observer fallback.

### Concurrency Policy

When several events happen together, the agent does not cut blindly. It applies:

- `stable_frames`: the event must be visible across enough frames before it can win.
- `priority`: higher-priority events beat lower-priority events.
- `hold_sec`: after a scene wins, keep it live long enough to be watchable.
- `switch_cooldown_sec`: prevent normal rapid cuts between screens.
- `preempt_priority_gap`: a new event must be much more important to break an active hold.
- `interrupt: true`: critical events can break hold/cooldown immediately.
- `recent_event_ttl_sec`: events that happen during a hold are remembered briefly, so the director can still cut to them after the current shot finishes.

For example, a `molotov_throw` at priority `70` should beat generic combat at
priority `40`. A sniper event at priority `80` will not necessarily break an
active molotov shot if the configured `preempt_priority_gap` is `25`; a critical
survival event at priority `90` with `interrupt: true` can break immediately.

### Composite Rules

Events can use either `rule_mode: "any"` or `rule_mode: "all"`.

Use `any` for broad detections where one visual signal is enough. Use `all` for
important events that need multiple signals, such as sniper or utility:

```yaml
- name: "player_1_sniper_scoped_action"
  category: "sniper"
  scene: "CS2 - Player 1"
  priority: 80
  hold_sec: 5.0
  stable_frames: 1
  rule_mode: "all"
  rules:
    - type: "template"
      source: "player_1"
      region: [0, 0, 1920, 1080]
      template_path: "../assets/templates/cs2/scope_overlay.png"
      threshold: 0.80
    - type: "motion"
      source: "player_1"
      region: [760, 360, 400, 260]
      threshold: 22
      min_ratio: 0.04
```

### Template Assets

The CS2 profile references templates under `assets/templates/cs2/`:

- `scope_overlay.png`
- `molotov_icon.png`
- `grenade_icon.png`

These files should be cropped screenshots from your actual CS2 resolution and
HUD scale. Missing templates do not crash the agent; they log a warning and
score `0.0` confidence until the images are added.

## Architecture: Why WebSocket Now, Plugin Later

This MVP is an external Python service that controls OBS through the OBS
WebSocket API (`SetCurrentProgramScene`), rather than a native OBS plugin. That
is a deliberate choice for the MVP:

- OBS 28+ ships with WebSocket support built in, so no plugin install or native
  build toolchain is needed.
- Visual rule tuning iterates much faster in Python than in a compiled C/C++
  OBS plugin.
- The hard, uncertain part of this product is the detection and director logic,
  not OBS integration. WebSocket lets us prove that part first.

The code is structured to stay plugin-ready. Each concern is isolated so a
future OBS plugin can become the UI/control shell without rewriting the
computer-vision engine:

| Concern | Module | Plugin-later note |
| --- | --- | --- |
| Screen capture | `src/excalibur/capture/manager.py` | Could be replaced by direct OBS source frames. |
| Event detection | `src/excalibur/detection/rules.py` | Stays in Python; reused as-is. |
| Director decisions | `src/excalibur/director/engine.py` | A plugin UI can inspect/override state here. |
| OBS control | `src/excalibur/obs/client.py` | The only OBS-specific surface; swap for an in-process plugin call. |

### When to actually build an OBS plugin

Add a native plugin later only if you need one of these:

- A native OBS dock / control panel inside OBS.
- Installer-style distribution for streamers.
- Direct access to OBS source frames instead of monitor capture.
- Lower-latency or deeper OBS scene/source control than WebSocket offers.

The likely end state is a hybrid: an OBS plugin for streamer-facing UI plus this
Python service for detection and director logic, until there is a strong reason
to port that logic into C++.

## Production Video Matrix

The MVP above watches monitor captures. The production architecture in
`src/excalibur/matrix/` replaces that with a bus-driven software video
matrix where agents observe **inputs** (not OBS Program) and a single Routing
Engine is the only authority that applies routes. See
`docs/ARCHITECTURE_DECISIONS.md` for the concrete technology choices and
`configs/matrix.example.yaml` for the persistent 11x11 configuration.

### Data flow

```
CS2 GSI -> Game State Bus -> Match Intelligence -> Intelligence Bus
        \-> (facts)                              \-> (stories)
                                                      |
Frame taps (preview) ----------------------------> Director Agent -> Decision Bus
Operator UI -------------------------------------------------------> Control Bus
Automation rules --------------------------------------------------> Rule Bus
                                                                       |
                                          Decision/Control/Rule -> Routing Engine -> Matrix -> OBS WebSocket
```

### Components

| Component | Module |
| --- | --- |
| Domain models (slots, routes, intents, decisions, match/intel) | `matrix/domain.py` |
| In-process buses (Decision/Control/Rule/Game State/Intelligence) | `matrix/buses.py` |
| Always-11x11 matrix state | `matrix/matrix_core.py` |
| Routing Engine (sole authority, audit log) | `matrix/routing_engine.py` |
| CS2 GSI receiver + Game State Bus | `matrix/gsi/receiver.py` |
| Match Intelligence Engine (story detectors) | `matrix/intelligence/engine.py` |
| Frame tap pipeline (preview frames per input) | `matrix/frame_tap.py` |
| Director Agent (publishes intents) | `matrix/agents/director_agent.py` |
| OBS bridge (matrix output -> OBS scene) | `matrix/obs_bridge.py` |
| Latency budgets and tracker | `matrix/latency.py` |
| Wiring + HTTP control API | `matrix/service.py`, `matrix/server.py` |

### Run the matrix

Run the simulated clutch round end-to-end (dry-run OBS):

```powershell
excalibur-matrix simulate
```

Run the control API for the Next.js control plane (CS2 GSI sink at `POST /gsi`):

```powershell
excalibur-matrix serve --port 8800
```

Then start the UI:

```powershell
cd control-plane
npm install
npm run dev
```

The control plane (http://localhost:3000) shows the 11x11 matrix, routes, the
match-intelligence story bus, the latency budget, the routing-engine audit log,
and operator overrides.

## Tests

```powershell
pytest
```
