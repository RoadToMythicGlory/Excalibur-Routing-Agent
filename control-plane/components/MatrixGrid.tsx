"use client";

import type { InputSlot, OutputSlot } from "@/lib/types";

function teamTag(team: string | null) {
  if (!team) return null;
  const cls = team === "CT" ? "tag ct" : team === "T" ? "tag t" : "tag";
  return <span className={cls}>{team}</span>;
}

export function MatrixGrid({
  inputs,
  programInput,
}: {
  inputs: InputSlot[];
  programInput: string | null;
}) {
  return (
    <div className="panel">
      <h2>Inputs (11x11 surface)</h2>
      <div className="matrix">
        {inputs.map((slot) => {
          const isProgram = slot.id === programInput;
          const cls = `slot ${slot.state} ${isProgram ? "program" : ""}`;
          return (
            <div key={slot.id} className={cls}>
              <div className="id">{slot.id}</div>
              <div className="name">{slot.player_name ?? slot.label}</div>
              {teamTag(slot.team)}
              <div>
                <span className="tag">{slot.state}</span>
                {slot.protocol ? <span className="tag">{slot.protocol}</span> : null}
                {isProgram ? <span className="tag" style={{ color: "#f85149" }}>ON AIR</span> : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function OutputsGrid({ outputs }: { outputs: OutputSlot[] }) {
  return (
    <div className="panel">
      <h2>Outputs</h2>
      <div className="matrix">
        {outputs.map((slot) => (
          <div key={slot.id} className={`slot ${slot.state}`}>
            <div className="id">{slot.id}</div>
            <div className="name">{slot.kind}</div>
            <div>
              <span className="tag">{slot.input_id ?? "—"}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
