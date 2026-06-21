export type SlotState =
  | "active"
  | "inactive"
  | "missing_signal"
  | "reserved"
  | "locked"
  | "degraded";

export interface InputSlot {
  id: string;
  label: string;
  state: SlotState;
  protocol: string | null;
  player_name: string | null;
  team: string | null;
  role: string | null;
  last_frame_at: number | null;
}

export interface OutputSlot {
  id: string;
  label: string;
  kind: string;
  state: SlotState;
  obs_scene: string | null;
  uri: string | null;
  input_id: string | null;
}

export interface ProgramStatus {
  publish_url: string;
  source_input: string | null;
  source_url: string | null;
  running: boolean;
  active: boolean;
}

export interface RouteRow {
  output_id: string;
  input_id: string | null;
  set_by: string;
  reason: string;
  set_at: number;
}

export interface StoryEvent {
  type: string;
  priority: number;
  confidence: number;
  ttl_ms: number;
  age_ms: number;
  stale: boolean;
  affected_players: string[];
  recommended_inputs: string[];
  evidence: string[];
}

export interface AuditRow {
  decision_id: string;
  outcome: "accepted" | "rejected" | "delayed" | "superseded";
  output_id: string;
  input_id: string | null;
  producer: string;
  reason: string;
  age_ms: number;
  decision_ms: number;
  created_at: number;
}

export interface LatencyEntry {
  elapsed_ms: number;
  budget_ms: number;
  within_budget: boolean;
}

export interface MatchPlayer {
  slot: string;
  input_id: string | null;
  name: string;
  team: string;
  health: number;
  alive: boolean;
  active_weapon: string | null;
}

export interface MatchSnapshot {
  round_id: string;
  phase: string;
  round_number: number;
  map_name: string | null;
  fresh: boolean;
  age_ms: number;
  bomb: { state: string; site: string | null; countdown_ms: number | null };
  players: MatchPlayer[];
}

export interface MatrixSnapshot {
  media_enabled: boolean;
  agent_enabled: boolean;
  program: ProgramStatus;
  matrix: {
    inputs: InputSlot[];
    outputs: OutputSlot[];
    routes: RouteRow[];
  };
  buses: Record<string, { published: number; delivered: number; errors: number }>;
  latency: Record<string, LatencyEntry>;
  audit: AuditRow[];
  intelligence: StoryEvent[];
  match: MatchSnapshot | null;
}
