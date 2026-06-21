"use client";

import type { StoryEvent } from "@/lib/types";

export function StoryPanel({ stories }: { stories: StoryEvent[] }) {
  const ordered = [...stories].reverse();
  return (
    <div className="panel">
      <h2>Match Intelligence (story bus)</h2>
      {ordered.length === 0 ? <p className="meta">No story events yet.</p> : null}
      {ordered.map((story, index) => (
        <div key={`${story.type}-${index}`} className={`story ${story.stale ? "stale" : ""}`}>
          <div className="row">
            <span className="type">{story.type}</span>
            <span className="meta">
              prio {story.priority} · conf {(story.confidence * 100).toFixed(0)}% ·{" "}
              {story.stale ? "STALE" : `ttl ${story.ttl_ms.toFixed(0)}ms`}
            </span>
          </div>
          <div className="meta">→ {story.recommended_inputs.join(", ") || "—"}</div>
          <div className="evidence">{story.evidence.join(" · ")}</div>
        </div>
      ))}
    </div>
  );
}
