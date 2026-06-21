"use client";

import { useEffect, useState } from "react";
import { fetchState } from "@/lib/api";
import type { MatrixSnapshot } from "@/lib/types";
import { MatrixGrid, OutputsGrid } from "@/components/MatrixGrid";
import { StoryPanel } from "@/components/StoryPanel";
import { LatencyPanel } from "@/components/LatencyPanel";
import { AuditPanel } from "@/components/AuditPanel";
import { OperatorControls } from "@/components/OperatorControls";

export default function Page() {
  const [state, setState] = useState<MatrixSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const next = await fetchState();
        if (active) {
          setState(next);
          setError(null);
        }
      } catch (err) {
        if (active) setError((err as Error).message);
      }
    };
    poll();
    const timer = setInterval(poll, 1000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const programInput =
    state?.matrix.outputs.find((o) => o.kind === "program")?.input_id ?? null;

  return (
    <main className="app">
      <div className="header">
        <h1>Matrix Director — Control Plane</h1>
        <div className="meta">
          {state?.match
            ? `${state.match.map_name ?? "map"} · ${state.match.round_id} · ${state.match.phase} · ${
                state.match.fresh ? "GSI fresh" : "GSI stale"
              }`
            : "Waiting for matrix API…"}
        </div>
      </div>

      {error ? (
        <div className="panel error">
          Cannot reach matrix API: {error}. Start it with{" "}
          <code>python -m obs_auto_director.matrix.run serve</code>.
        </div>
      ) : null}

      {state ? (
        <div className="grid">
          <div>
            <MatrixGrid inputs={state.matrix.inputs} programInput={programInput} />
            <OutputsGrid outputs={state.matrix.outputs} />
            <OperatorControls
              inputs={state.matrix.inputs}
              outputs={state.matrix.outputs}
            />
            <AuditPanel audit={state.audit} />
          </div>
          <div>
            <StoryPanel stories={state.intelligence} />
            <LatencyPanel latency={state.latency} />
          </div>
        </div>
      ) : null}
    </main>
  );
}
