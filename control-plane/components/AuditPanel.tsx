"use client";

import type { AuditRow } from "@/lib/types";

export function AuditPanel({ audit }: { audit: AuditRow[] }) {
  const rows = [...audit].reverse().slice(0, 20);
  return (
    <div className="panel">
      <h2>Routing engine audit</h2>
      <table>
        <thead>
          <tr>
            <th>Outcome</th>
            <th>Output</th>
            <th>Input</th>
            <th>Producer</th>
            <th>Reason</th>
            <th>Age</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.decision_id}>
              <td>
                <span className={`pill ${row.outcome}`}>{row.outcome}</span>
              </td>
              <td>{row.output_id}</td>
              <td>{row.input_id ?? "—"}</td>
              <td>{row.producer}</td>
              <td>{row.reason}</td>
              <td>{row.age_ms.toFixed(0)}ms</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
