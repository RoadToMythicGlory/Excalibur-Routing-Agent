"use client";

import { useState } from "react";
import { sendControl } from "@/lib/api";
import type { InputSlot, OutputSlot } from "@/lib/types";

export function OperatorControls({
  inputs,
  outputs,
}: {
  inputs: InputSlot[];
  outputs: OutputSlot[];
}) {
  const [output, setOutput] = useState("output_01");
  const [input, setInput] = useState("input_01");
  const [status, setStatus] = useState<string | null>(null);

  async function run(action: Parameters<typeof sendControl>[0]["action"]) {
    try {
      const result = await sendControl({
        action,
        target_output: output,
        candidate_input: input,
        operator: "ui-operator",
        reason: `ui_${action}`,
        lock_ms: action === "lock_output" ? 30000 : undefined,
        hold_ms: action === "hold_source" ? 5000 : undefined,
      });
      setStatus(`${action}: ${result.outcome ?? result.error}`);
    } catch (error) {
      setStatus(`error: ${(error as Error).message}`);
    }
  }

  return (
    <div className="panel">
      <h2>Operator override (control bus)</h2>
      <div className="controls-row">
        <select value={output} onChange={(event) => setOutput(event.target.value)}>
          {outputs.map((slot) => (
            <option key={slot.id} value={slot.id}>
              {slot.id} ({slot.kind})
            </option>
          ))}
        </select>
        <select value={input} onChange={(event) => setInput(event.target.value)}>
          {inputs.map((slot) => (
            <option key={slot.id} value={slot.id}>
              {slot.id} {slot.player_name ? `· ${slot.player_name}` : ""}
            </option>
          ))}
        </select>
      </div>
      <div className="controls-row">
        <button onClick={() => run("manual_cut")}>Manual cut</button>
        <button onClick={() => run("take_next")}>Take next</button>
        <button onClick={() => run("hold_source")}>Hold 5s</button>
        <button onClick={() => run("lock_output")}>Lock 30s</button>
        <button onClick={() => run("release_lock")}>Release lock</button>
        <button className="danger" onClick={() => run("emergency_route")}>
          Emergency route
        </button>
      </div>
      {status ? <p className="meta">{status}</p> : null}
    </div>
  );
}
