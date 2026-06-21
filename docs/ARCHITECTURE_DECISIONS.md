# Production Video Matrix - Architecture Decisions

This records the concrete choices made to implement the Production Video Matrix
Director plan. The plan listed several open decisions; these are the ones the
first production slice is built on.

## Media backend and transport

- First transport: SRT for contribution feeds. The ingest layer is protocol
  agnostic, so RTMP and Zixi can be added behind the same `FrameTapSource`
  interface.
- Media backend: FFmpeg worker processes for decoding and preview generation.
  The code abstracts this behind `FrameTapSource`, and ships a
  `SimulatedFrameTapSource` so the whole system runs and is testable with no
  live feeds or native media dependencies.
- Matrix outputs to OBS: exposed as stable streams/sources (SRT/NDI/media)
  consumed by OBS scenes. OBS scene switching is done through OBS WebSocket.

## Bus implementation

- First slice uses an in-process async pub/sub bus (`InProcessBus`). All five
  logical buses (Decision, Control, Rule, Game State, Intelligence) are typed
  wrappers over the same primitive.
- The bus surface is deliberately small (`publish` / `subscribe`) so it can be
  swapped for Redis Streams, NATS, or Kafka without touching producers or
  consumers.

## CS2 data access

- A local HTTP GSI receiver normalizes Counter-Strike 2 Game State Integration
  payloads into a `MatchState`.
- A `GsiSimulator` produces deterministic payload sequences for development and
  tests, so no live observer/GOTV client is required to exercise the pipeline.
- `allplayers` data requires spectator/GOTV access in real use; the system
  degrades gracefully and leans on frame taps when game data is missing.

## Match intelligence

- First story detectors: `last_alive`, `potential_clutch`, `site_execute`,
  `retake_started`, `awp_duel`, and `eco_upset_risk`.
- Story events carry priority, confidence, TTL, evidence, affected players, and
  recommended inputs.

## Routing authority

- The `RoutingEngine` is the only writer of matrix route state.
- Agents, operators, and automation rules only publish `RoutingIntent`s.
- Authority order: emergency operator > output locks/holds > safety/health >
  automation rules > agent decisions by priority/confidence/recency.
- Every intent is recorded in an audit log with `age_ms`, `decision_ms`, and an
  accept/reject/delay reason.

## Latency budgets

- Hard per-stage budgets are defined in `LatencyBudget`.
- Intents and game state carry timestamps; stale intents (`age > ttl_ms`) and
  stale game state (`age > max_state_age_ms`) are rejected rather than applied
  late.

## Control plane

- A Next.js/React control plane lives in `control-plane/`. It renders the 11x11
  matrix, routes, latency panel, and story panel, and reads live state from the
  backend service.
- The backend exposes matrix state and accepts operator commands; the first
  slice ships the Python service and a UI skeleton wired to its data shapes.

## Matrix size

- The matrix is always at least 11 inputs by 11 outputs. Slots exist even when
  inactive; the first slice activates a subset.
