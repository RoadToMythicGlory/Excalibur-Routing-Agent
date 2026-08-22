# Soccer Director

Excalibur can run as a soccer director on the production matrix path. Camera 1
(`input_01`) is the lead camera. Automation leaves that camera only for special
events: foul, free kick, penalty kick, corner kick, or a burst attack
(התקפה מתפרצת). Ordinary passing stays on Camera 1.

## Enable soccer mode

Write `sport_mode` into the matrix runtime file (do not overwrite a live CS2
operator config by accident):

```json
{
  "sport_mode": "soccer",
  "game_profile": "configs/games/soccer.json",
  "director_mode": "automatic",
  "main_input": "input_01",
  "cv_profile": "auto"
}
```

Or pick any of the runtime paths:

- CLI: `excalibur-matrix serve --sport soccer --game-profile configs/games/soccer.json`
- API: `POST /api/sport {"sport_mode": "soccer"}` switches live and persists.
  If real media ingest is already running, the response sets
  `media_restart_required: true` — restart the service so decode settings
  (CV pixel loading, inference width/FPS) apply to the ingest workers.
- Or construct the service with `sport_mode="soccer"` and restart
  `excalibur-matrix serve`.

CS2 GSI idle rotation is disabled while soccer is selected. Manual cuts, locks,
holds, and emergency routes stay available.

## Structured event feed (authoritative)

`POST /api/soccer/events` accepts either a heartbeat or an event revision.
Accepted payloads return 200; malformed, out-of-order, future-skewed, or
stale-timestamped payloads return 422 with `status: "rejected"` (the running
rejection count is also exposed as `soccer.rejected` in `/api/state`).
Contracts live in:

- `configs/schemas/soccer-event.schema.json`
- `configs/schemas/soccer-profile.schema.json`
- `configs/games/soccer.json`
- `configs/soccer.events.example.json`

The JSON Schemas are documentation for feed integrators; the runtime source of
truth is the Pydantic validation in `src/excalibur/matrix/soccer/config.py` and
`receiver.py` (which also enforces a required `setup` phase per event plan,
known camera-role ids, and semver profile versions).

A fresh structured feed always wins over video inference. When the feed is stale
or missing, local CV may propose the same special events. If neither source is
trustworthy the director stays on Camera 1.

Ordinary `pass` observations are never events.

## Manual ownership

Any accepted operator `manual_cut`, `take_next`, or `emergency_route` suspends
soccer automation and persists `director_mode=manual_suspended` together with
who/why/when (`soccer.suspension` in `/api/state`). Automation does not resume
after an event ends or after restart. The operator must press **Resume** (or
`POST /api/agent {"enabled": true}`), which clears the suspension record.
While suspended, `agent_enabled` reports `false`; read `director_mode` for the
three-way `automatic` / `manual_suspended` / `disabled` distinction.

## Local computer vision

Video inference is a fallback and a camera-role estimator for inputs other than
Camera 1. Until a model manifest passes the evaluation gates in the soccer
profile, CV stays in shadow mode: camera roles and health still surface in the
UI (with a `cv_shadow` warning and `fallback_reason: vision_shadow`), but
vision can never gain routing authority or create events.

To deploy a model: export an ONNX file plus manifest, point
`inference.model_manifest` in `configs/games/soccer.json` at the manifest, and
restart. The service then constructs the real ONNX Runtime backend
automatically (`onnxruntime` from the `cv-cpu`/`cv-gpu` extras); without a
manifest it runs the deterministic mock in shadow mode. `tools/cv/eval.py`
only marks a manifest `validated` when the metrics JSON itself attests
`"validated": true` from a real held-out evaluation run — the tool cannot
grant that flag on its own, and the routing gate requires it.

Training and export:

```text
tools/cv/extract_frames.py
tools/cv/label.py
tools/cv/train.py
tools/cv/eval.py
tools/cv/export.py
tools/cv/bench.py
```

Install extras only when needed: `pip install -e ".[cv-cpu]"` / `cv-gpu` / `cv-train`.

## Operator workflow

1. Leave Camera 1 as the program source during open play.
2. When a special event starts, the director may change angles during setup,
   execution, and follow-through.
3. When the event ends (structured end, stable resumed open play, loss of both
   sources, or max duration) the director returns to Camera 1. If the program
   camera loses signal, or both sources go dark mid-event, the director stops
   cutting and comes home to Camera 1.
4. Take over at any time. Resume only when you want automation back.

## Rollback

Set `sport_mode` back to `cs2` or toggle the AI director off. Shadow-mode CV
cannot change routes if the model manifest is missing or fails its gates.
