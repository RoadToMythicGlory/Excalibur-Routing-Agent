"use client";

import type { LatencyEntry } from "@/lib/types";

export function LatencyPanel({
  latency,
}: {
  latency: Record<string, LatencyEntry>;
}) {
  const entries = Object.entries(latency);
  return (
    <div className="panel">
      <h2>Latency budget</h2>
      {entries.length === 0 ? <p className="meta">No samples yet.</p> : null}
      <table>
        <tbody>
          {entries.map(([stage, entry]) => {
            const pct = Math.min(
              100,
              (entry.elapsed_ms / Math.max(1, entry.budget_ms)) * 100
            );
            return (
              <tr key={stage}>
                <td style={{ width: "40%" }}>{stage}</td>
                <td style={{ width: "35%" }}>
                  <div className="bar">
                    <span
                      className={entry.within_budget ? "ok" : "over"}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </td>
                <td>
                  {entry.elapsed_ms.toFixed(0)} / {entry.budget_ms.toFixed(0)} ms
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
