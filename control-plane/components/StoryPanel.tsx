"use client";

import type { SoccerSnapshot, StoryEvent } from "@/lib/types";

export function StoryPanel({
  stories,
  soccer,
}: {
  stories: StoryEvent[];
  soccer?: SoccerSnapshot | null;
}) {
  const ordered = [...stories].reverse();
  return (
    <div className="panel">
      <h2>Match Intelligence (story bus)</h2>
      {soccer ? (
        <div className="story">
          <div className="row">
            <span className="type">
              {soccer.event ? soccer.event.type : "open play"}
            </span>
            <span className="meta">
              {soccer.authority} · {soccer.director_mode}
            </span>
          </div>
          <div className="meta">
            {soccer.event
              ? `${soccer.event.phase} · ${soccer.event.lifecycle} · ${(soccer.event.confidence * 100).toFixed(0)}% · ${soccer.event.source}`
              : "Lead camera until a special event starts"}
          </div>
          <div className="meta">
            profile v{soccer.profile_version} · cv {soccer.cv_profile}
            {soccer.model_version ? ` · model ${soccer.model_version}` : " · no model (shadow)"}
            {soccer.fallback_reason ? ` · fallback: ${soccer.fallback_reason}` : ""}
          </div>
          {soccer.suspension ? (
            <div className="evidence">
              suspended by {soccer.suspension.by} ({soccer.suspension.reason})
            </div>
          ) : null}
          {soccer.warnings.length ? (
            <div className="evidence">{soccer.warnings.join(" · ")}</div>
          ) : null}
        </div>
      ) : null}
      {ordered.length === 0 ? <p className="meta">No story events yet.</p> : null}
      {ordered.map((story, index) => (
        <div key={`${story.type}-${index}`} className={`story ${story.stale ? "stale" : ""}`}>
          <div className="row">
            <span className="type">{story.type}</span>
            <span className="meta">
              prio {story.priority} · conf {(story.confidence * 100).toFixed(0)}% ·{" "}
              {story.stale ? "STALE" : `ttl ${story.ttl_ms.toFixed(0)}ms`}
              {story.phase ? ` · ${story.phase}` : ""}
              {story.source ? ` · ${story.source}` : ""}
            </span>
          </div>
          <div className="meta">→ {story.recommended_inputs.join(", ") || "—"}</div>
          <div className="evidence">{story.evidence.join(" · ")}</div>
        </div>
      ))}
    </div>
  );
}
