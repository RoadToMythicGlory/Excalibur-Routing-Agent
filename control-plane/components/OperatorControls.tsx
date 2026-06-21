"use client";

import { useEffect, useState } from "react";
import { sendControl, setAgentEnabled } from "@/lib/api";
import type { InputSlot, OutputSlot } from "@/lib/types";

export function OperatorControls({
  inputs,
  outputs,
  agentEnabled,
  onAgentToggled,
}: {
  inputs: InputSlot[];
  outputs: OutputSlot[];
  agentEnabled: boolean;
  onAgentToggled?: (enabled: boolean) => void;
}) {
  const [output, setOutput] = useState("output_01");
  const [input, setInput] = useState("input_01");
  const [status, setStatus] = useState<string | null>(null);
  const [togglingAgent, setTogglingAgent] = useState(false);

  async function toggleAgent() {
    setTogglingAgent(true);
    try {
      const result = await setAgentEnabled(!agentEnabled);
      onAgentToggled?.(result.agent_enabled);
      setStatus(`AI director ${result.agent_enabled ? "activated" : "deactivated"}`);
    } catch (error) {
      setStatus(`error: ${(error as Error).message}`);
    } finally {
      setTogglingAgent(false);
    }
  }

  // Slot lists can change at runtime (add/remove). Keep selections valid so we
  // never POST a command targeting a slot that no longer exists.
  useEffect(() => {
    if (outputs.length > 0 && !outputs.some((slot) => slot.id === output)) {
      setOutput(outputs[0].id);
    }
  }, [outputs, output]);

  useEffect(() => {
    if (inputs.length > 0 && !inputs.some((slot) => slot.id === input)) {
      setInput(inputs[0].id);
    }
  }, [inputs, input]);

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
      <div className="agent-toggle-row">
        <div>
          <h2>AI Director</h2>
          <p className="meta">
            {agentEnabled
              ? "Active — automatically cutting to the live action."
              : "Off — manual operator control only."}
          </p>
        </div>
        <button
          type="button"
          className={`agent-toggle ${agentEnabled ? "on" : "off"}`}
          aria-pressed={agentEnabled}
          disabled={togglingAgent}
          onClick={toggleAgent}
        >
          <span className="agent-toggle-knob" />
          <span className="agent-toggle-label">{agentEnabled ? "ON" : "OFF"}</span>
        </button>
      </div>
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
