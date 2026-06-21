import type { MatrixSnapshot } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_MATRIX_API ?? "http://127.0.0.1:8800";

export function previewUrl(inputId: string, tick: number): string {
  return `${API_BASE}/api/preview/${inputId}.jpg?t=${tick}`;
}

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

export interface InputConfigRow {
  id: string;
  label: string;
  player_name: string | null;
  team: string | null;
  role: string | null;
  obs_scene: string | null;
  state: string;
  uri: string | null;
  srt_mode: string | number;
  srt_host: string | number;
  srt_port: string | number;
  srt_latency: string | number;
}

export interface ProgramConfig {
  output_id: string;
  publish_url: string;
  obs_url: string;
  source_input: string | null;
  running: boolean;
  active: boolean;
}

export interface MatrixConfig {
  program_output: string;
  program: ProgramConfig;
  host: { primary_ip: string; addresses: string[]; public_ip: string | null };
  inputs: InputConfigRow[];
  outputs: { id: string; label: string; kind: string; uri: string | null }[];
}

export async function fetchConfig(): Promise<MatrixConfig> {
  const response = await fetch(`${API_BASE}/api/config`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Config fetch failed: ${response.status}`);
  }
  return (await response.json()) as MatrixConfig;
}

export interface InputConfigPayload {
  input_id: string;
  player_name?: string | null;
  team?: string | null;
  role?: string | null;
  obs_scene?: string | null;
  srt_mode?: string | null;
  srt_host?: string | null;
  srt_port?: number | null;
  srt_latency?: number | null;
}

export async function saveInputConfig(payload: InputConfigPayload) {
  const response = await fetch(`${API_BASE}/api/config/input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Config save failed: ${response.status}`);
  }
  return response.json();
}
