"use client";

import { previewUrl } from "@/lib/api";
import type { InputSlot, OutputSlot, SoccerCameraRole } from "@/lib/types";

function teamTag(team: string | null) {
  if (!team) return null;
  const cls = team === "CT" ? "tag ct" : team === "T" ? "tag t" : "tag";
  return <span className={cls}>{team}</span>;
}

export function MatrixGrid({
  inputs,
  programInput,
  mediaEnabled,
  tick,
  selectedInput,
  takingInput,
  takeStatus,
  onSelectInput,
  onTakeInput,
  soccerCameras,
}: {
  inputs: InputSlot[];
  programInput: string | null;
  mediaEnabled: boolean;
  tick: number;
  selectedInput: string | null;
  takingInput: string | null;
  takeStatus: string | null;
  onSelectInput: (inputId: string) => void;
  onTakeInput: (inputId: string) => void;
  soccerCameras?: Record<string, SoccerCameraRole>;
}) {
  return (
    <div className="panel">
      <h2>Inputs ({inputs.length})</h2>
      <div className="matrix">
        {inputs.map((slot) => {
          const isProgram = slot.id === programInput;
          const isSelected = slot.id === selectedInput;
          const cls = `slot clickable ${slot.state} ${isProgram ? "program" : ""} ${
            isSelected ? "selected" : ""
          }`;
          const frameFresh =
            slot.last_frame_at != null && Date.now() - slot.last_frame_at <= 3000;
          const live =
            mediaEnabled && slot.state === "active" && frameFresh;
          return (
            <div
              key={slot.id}
              role="button"
              tabIndex={0}
              className={cls}
              onClick={() => onSelectInput(slot.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelectInput(slot.id);
                }
              }}
            >
              {mediaEnabled ? (
                <div className="preview">
                  {live ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={previewUrl(slot.id, tick)}
                      alt={`${slot.id} preview`}
                    />
                  ) : (
                    <div className="no-signal">NO SIGNAL</div>
                  )}
                </div>
              ) : null}
              <div className="id">{slot.id}</div>
              <div className="name">{slot.player_name ?? slot.label}</div>
              {teamTag(slot.team)}
              <div>
                <span className="tag">{slot.state}</span>
                {slot.protocol ? <span className="tag">{slot.protocol}</span> : null}
                {isProgram ? <span className="tag" style={{ color: "#f85149" }}>ON AIR</span> : null}
                {soccerCameras?.[slot.id] ? (
                  <span className="tag">
                    {soccerCameras[slot.id].role} {(soccerCameras[slot.id].confidence * 100).toFixed(0)}%
                    {soccerCameras[slot.id].score ? ` · ${soccerCameras[slot.id].score.toFixed(0)}` : ""}
                  </span>
                ) : slot.role ? (
                  <span className="tag">{slot.role}</span>
                ) : null}
              </div>
              {isSelected ? (
                <div className="take-row">
                  <button
                    type="button"
                    className="take-button"
                    disabled={takingInput === slot.id}
                    onClick={(event) => {
                      event.stopPropagation();
                      onTakeInput(slot.id);
                    }}
                  >
                    {takingInput === slot.id ? "Taking…" : "Take"}
                  </button>
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
      {takeStatus ? <p className="meta">{takeStatus}</p> : null}
    </div>
  );
}

export function OutputsGrid({ outputs }: { outputs: OutputSlot[] }) {
  return (
    <div className="panel">
      <h2>Outputs ({outputs.length})</h2>
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
