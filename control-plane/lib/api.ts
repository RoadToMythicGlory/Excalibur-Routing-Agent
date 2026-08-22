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

export async function setAgentEnabled(enabled: boolean) {
  const response = await fetch(`${API_BASE}/api/agent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!response.ok) {
    throw new Error(`Agent toggle failed: ${response.status}`);
  }
  return response.json() as Promise<{
    status: string;
    agent_enabled: boolean;
    director_mode?: string;
  }>;
}

export type SourceMode =
  | "off"
  | "srt_listener"
  | "srt_caller"
  | "rtsp"
  | "url"
  | "file";

export interface InputConfigRow {
  id: string;
  label: string;
  player_name: string | null;
  team: string | null;
  role: string | null;
  obs_scene: string | null;
  state: string;
  uri: string | null;
  source_mode: SourceMode | string;
  srt_mode: string | number;
  srt_host: string | number;
  srt_port: string | number;
  srt_latency: string | number;
  file_path: string | null;
  source_url: string | null;
  loop: boolean;
}

export interface ProgramConfig {
  output_id: string;
  publish_url: string;
  obs_url: string;
  source_input: string | null;
  running: boolean;
  active: boolean;
}

export interface VirtualCamConfig {
  enabled: boolean;
  device_name: string;
  width: number;
  height: number;
  fps: number;
  source_input: string | null;
  running: boolean;
  active: boolean;
  manager_available: boolean;
  setup_commands: string[];
}

export interface MatrixConfig {
  program_output: string;
  input_count: number;
  output_count: number;
  program: ProgramConfig;
  virtualcam: VirtualCamConfig;
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
  source_mode?: SourceMode | string | null;
  srt_mode?: string | null;
  srt_host?: string | null;
  srt_port?: number | null;
  srt_latency?: number | null;
  file_path?: string | null;
  source_url?: string | null;
  loop?: boolean | null;
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

export interface FileEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface FileListing {
  path: string;
  parent: string | null;
  is_root: boolean;
  entries: FileEntry[];
}

export async function browseFiles(path: string): Promise<FileListing> {
  const response = await fetch(
    `${API_BASE}/api/files?path=${encodeURIComponent(path)}`,
    { cache: "no-store" }
  );
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      if (body?.error) detail = body.error;
    } catch {
      // ignore parse errors; keep status code
    }
    throw new Error(`Browse failed: ${detail}`);
  }
  return (await response.json()) as FileListing;
}

export interface ProgramConfigPayload {
  virtualcam_enabled?: boolean;
  virtualcam_device?: string;
  virtualcam_width?: number;
  virtualcam_height?: number;
  virtualcam_fps?: number;
  virtualcam_manager_bin?: string;
}

export async function saveProgramConfig(payload: ProgramConfigPayload) {
  const response = await fetch(`${API_BASE}/api/config/program`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      if (body?.error) detail = body.error;
    } catch {
      // ignore parse errors; keep status code
    }
    throw new Error(`Program config save failed: ${detail}`);
  }
  return response.json();
}

export interface MatrixSizePayload {
  input_count?: number;
  output_count?: number;
}

export async function saveMatrixSize(payload: MatrixSizePayload) {
  const response = await fetch(`${API_BASE}/api/config/matrix`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const body = await response.json();
      if (body?.error) detail = body.error;
    } catch {
      // ignore parse errors; keep status code
    }
    throw new Error(`Matrix resize failed: ${detail}`);
  }
  return response.json();
}
