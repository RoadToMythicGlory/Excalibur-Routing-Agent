"use client";

import { useEffect, useState } from "react";
import { fetchState, sendControl } from "@/lib/api";
import type { MatrixSnapshot } from "@/lib/types";
import { MatrixGrid, OutputsGrid } from "@/components/MatrixGrid";
import { StoryPanel } from "@/components/StoryPanel";
import { LatencyPanel } from "@/components/LatencyPanel";
import { AuditPanel } from "@/components/AuditPanel";
import { OperatorControls } from "@/components/OperatorControls";
import { ConfigPanel } from "@/components/ConfigPanel";

export default function Page() {
  const [state, setState] = useState<MatrixSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [selectedInput, setSelectedInput] = useState<string | null>(null);
  const [takingInput, setTakingInput] = useState<string | null>(null);
  const [takeStatus, setTakeStatus] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const next = await fetchState();
        if (active) {
          setState(next);
          setError(null);
          setTick((value) => value + 1);
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
  const programOutput =
    state?.matrix.outputs.find((o) => o.kind === "program")?.id ?? "output_01";

  async function takeInput(inputId: string) {
    setTakingInput(inputId);
    try {
      const result = await sendControl({
        action: "manual_cut",
        target_output: programOutput,
        candidate_input: inputId,
        operator: "mcr-ui",
        reason: "mcr_take",
      });
      setTakeStatus(`Take ${inputId}: ${result.outcome ?? result.error}`);
      if (result.outcome === "accepted") {
        setSelectedInput(null);
      }
    } catch (err) {
      setTakeStatus(`error: ${(err as Error).message}`);
    } finally {
      setTakingInput(null);
    }
  }

  return (
    <main className="app">
      <div className="header">
        <img src="/logo.png?v=2" alt="Excalibur Routing Agent" className="logo" />
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
          <code>python -m excalibur.matrix.run serve</code>.
        </div>
      ) : null}

      {state ? (
        <div className="grid">
          <div>
            <MatrixGrid
              inputs={state.matrix.inputs}
              programInput={programInput}
              mediaEnabled={state.media_enabled}
              tick={tick}
              selectedInput={selectedInput}
              takingInput={takingInput}
              takeStatus={takeStatus}
              onSelectInput={(inputId) => {
                setSelectedInput(inputId);
                setTakeStatus(null);
              }}
              onTakeInput={takeInput}
            />
            <OutputsGrid outputs={state.matrix.outputs} />
            <OperatorControls
              inputs={state.matrix.inputs}
              outputs={state.matrix.outputs}
              agentEnabled={state.agent_enabled}
              onAgentToggled={(enabled) =>
                setState((prev) =>
                  prev ? { ...prev, agent_enabled: enabled } : prev
                )
              }
            />
            <AuditPanel audit={state.audit} />
          </div>
          <div>
            <ConfigPanel />
            <StoryPanel stories={state.intelligence} />
            <LatencyPanel latency={state.latency} />
          </div>
        </div>
      ) : null}
    </main>
  );
}
