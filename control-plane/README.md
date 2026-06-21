# Matrix Director — Control Plane

Next.js/React control plane for the production video matrix. It renders the
always-11x11 matrix, current routes, the match-intelligence story bus, the
latency budget, the routing-engine audit log, and operator overrides.

## Run

1. Start the backend control API:

```bash
python -m obs_auto_director.matrix.run serve
```

2. Install and run the UI:

```bash
cd control-plane
npm install
npm run dev
```

3. Open http://localhost:3000

The UI polls `GET /api/state` from the backend (default
`http://127.0.0.1:8800`, override with `NEXT_PUBLIC_MATRIX_API`).

## Feeding game state

The backend exposes a CS2 GSI sink at `POST /gsi`. Point a CS2 Game State
Integration config file at it, or replay simulated payloads. Operator overrides
from the UI are sent to `POST /api/control` and arbitrated by the Routing
Engine like any other intent.

## Panels

- **Inputs**: 11 input slots; the on-air input is highlighted.
- **Outputs**: 11 output slots and their current routed input.
- **Match Intelligence**: story-level events with evidence, confidence, and TTL.
- **Latency budget**: per-stage elapsed vs. max-ms budget.
- **Routing engine audit**: accepted/rejected/delayed/superseded decisions.
- **Operator override**: manual cut, take, hold, lock, and emergency route.
