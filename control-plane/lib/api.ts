import type { MatrixSnapshot } from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_MATRIX_API ?? "http://127.0.0.1:8800";

export async function fetchState(): Promise<MatrixSnapshot> {
  const response = await fetch(`${API_BASE}/api/state`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Matrix API returned ${response.status}`);
  }
  return (await response.json()) as MatrixSnapshot;
}

export interface ControlPayload {
  action:
    | "manual_cut"
    | "lock_output"
    | "release_lock"
    | "hold_source"
    | "emergency_route"
    | "take_next";
  operator?: string;
  target_output: string;
  candidate_input?: string | null;
  reason?: string;
  hold_ms?: number;
  lock_ms?: number;
}

export async function sendControl(payload: ControlPayload) {
  const response = await fetch(`${API_BASE}/api/control`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Control command failed: ${response.status}`);
  }
  return response.json();
}
