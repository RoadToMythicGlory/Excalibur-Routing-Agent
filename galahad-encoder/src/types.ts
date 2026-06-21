// Mirror of the Rust serde (camelCase) payloads exposed via Tauri commands.

export type StreamState =
  | "idle"
  | "starting"
  | "streaming"
  | "degraded"
  | "reconnecting"
  | "failed";

export interface OsInfo {
  name: string;
  build: string | null;
}

export interface EncoderBackend {
  codec: "h264" | "hevc";
  ffmpegName: string;
  vendor: "nvidia" | "intel" | "amd" | "software";
  hardware: boolean;
}

export interface ProcessInfo {
  pid: number;
  name: string;
}

export interface MicrophoneInfo {
  id: string;
  name: string;
}

export interface Capabilities {
  capture: string;
  encoders: string[];
  encoderBackends: EncoderBackend[];
  processAudio: boolean;
  discordAudio: boolean;
  discordProcesses: ProcessInfo[];
  microphones: MicrophoneInfo[];
  ffmpegAvailable: boolean;
  ffmpegPath: string;
  os: OsInfo;
}

export interface WindowInfo {
  id: number;
  title: string;
  processName: string;
  pid: number;
}

export interface Identity {
  playerId: string;
  sessionId: string;
  displayName: string;
}

export interface AudioSourceConfig {
  enabled: boolean;
  muted: boolean;
  gain: number;
}

export interface AudioConfig {
  game: AudioSourceConfig;
  discord: AudioSourceConfig;
  microphone: AudioSourceConfig;
  microphoneDeviceId: string | null;
  discordProcessId: number | null;
}

export interface EncoderConfig {
  allowSoftware: boolean;
  experimentalHevc: boolean;
}

export interface ClientConfig {
  playerId: string;
  displayName: string;
  destinationHost: string;
  destinationPort: number;
  latencyMs: number;
  presetId: string;
  sourceWindowTitle: string | null;
  sourceProcessName: string | null;
  audio: AudioConfig;
  encoder: EncoderConfig;
  controlChannelUrl: string | null;
}

export interface StreamStatus {
  state: StreamState;
  source: string | null;
  fps: number;
  bitrate: number;
  droppedFrames: number;
  encoder: string | null;
  captureBackend: string | null;
  lastError: string | null;
  audioWarnings: string[];
}

export interface QualityPreset {
  id: string;
  label: string;
  width: number;
  height: number;
  fps: number;
}

export const PRESETS: QualityPreset[] = [
  { id: "720p30", label: "720p30", width: 1280, height: 720, fps: 30 },
  { id: "1080p30", label: "1080p30", width: 1920, height: 1080, fps: 30 },
  { id: "1080p60", label: "1080p60", width: 1920, height: 1080, fps: 60 },
];
