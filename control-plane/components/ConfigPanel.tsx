"use client";

import { useCallback, useEffect, useState } from "react";
import {
  browseFiles,
  fetchConfig,
  saveInputConfig,
  saveMatrixSize,
  saveProgramConfig,
  type FileListing,
  type InputConfigRow,
  type MatrixConfig,
  type ProgramConfigPayload,
  type SourceMode,
} from "@/lib/api";

interface Draft {
  player_name: string;
  team: string;
  role: string;
  obs_scene: string;
  source_mode: SourceMode;
  srt_host: string;
  srt_port: string;
  srt_latency: string;
  file_path: string;
  source_url: string;
  loop: boolean;
}

const SOURCE_MODE_OPTIONS: { value: SourceMode; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "srt_listener", label: "SRT listener (bind port)" },
  { value: "srt_caller", label: "SRT caller (dial IP:port)" },
  { value: "rtsp", label: "RTSP stream" },
  { value: "url", label: "URL / HLS stream" },
  { value: "file", label: "Local file / VOD" },
];

const SRT_MODES: SourceMode[] = ["srt_listener", "srt_caller"];
const URL_MODES: SourceMode[] = ["rtsp", "url"];

function str(value: string | number | null): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

function normalizeMode(row: InputConfigRow): SourceMode {
  const explicit = str(row.source_mode);
  if (explicit && explicit !== "off") return explicit as SourceMode;
  if (explicit === "off") return "off";
  // Legacy fallback: derive from srt_mode for configs written before file/VOD.
  const legacy = str(row.srt_mode).toLowerCase();
  if (legacy === "listener") return "srt_listener";
  if (legacy === "caller") return "srt_caller";
  return "off";
}

function toDraft(row: InputConfigRow): Draft {
  return {
    player_name: row.player_name ?? "",
    team: row.team ?? "",
    role: row.role ?? "",
    obs_scene: row.obs_scene ?? "",
    source_mode: normalizeMode(row),
    srt_host: str(row.srt_host),
    srt_port: str(row.srt_port),
    srt_latency: str(row.srt_latency) || "200",
    file_path: row.file_path ?? "",
    source_url: row.source_url ?? "",
    loop: Boolean(row.loop),
  };
}

export function ConfigPanel() {
  const [config, setConfig] = useState<MatrixConfig | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [selectedIp, setSelectedIp] = useState<string>("");
  // File picker (browses the backend machine).
  const [browseId, setBrowseId] = useState<string | null>(null);
  const [listing, setListing] = useState<FileListing | null>(null);
  const [browseError, setBrowseError] = useState<string | null>(null);
  const [browseLoading, setBrowseLoading] = useState(false);

  const load = useCallback(async () => {
    try {
      const next = await fetchConfig();
      setConfig(next);
      setSelectedIp(
        (prev) => prev || next.host?.public_ip || next.host?.primary_ip || ""
      );
      setDrafts((prev) => {
        const merged: Record<string, Draft> = {};
        for (const row of next.inputs) {
          merged[row.id] = prev[row.id] ?? toDraft(row);
        }
        return merged;
      });
    } catch (error) {
      setStatus(`error: ${(error as Error).message}`);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function update(id: string, field: keyof Draft, value: string | boolean) {
    setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], [field]: value } }));
  }

  async function navigate(path: string) {
    setBrowseLoading(true);
    try {
      const next = await browseFiles(path);
      setListing(next);
      setBrowseError(null);
    } catch (error) {
      setBrowseError((error as Error).message);
    } finally {
      setBrowseLoading(false);
    }
  }

  async function openBrowse(id: string, start: string) {
    if (browseId === id) {
      setBrowseId(null);
      setListing(null);
      return;
    }
    setBrowseId(id);
    setListing(null);
    setBrowseError(null);
    await navigate(start);
  }

  function pickFile(id: string, path: string) {
    update(id, "file_path", path);
    setBrowseId(null);
    setListing(null);
  }

  async function resize(payload: { input_count?: number; output_count?: number }) {
    try {
      const result = await saveMatrixSize(payload);
      setStatus(
        `Matrix now ${result.input_count} inputs / ${result.output_count} outputs`
      );
      await load();
    } catch (error) {
      setStatus(`error: ${(error as Error).message}`);
    }
  }

  function callerString(draft: Draft): string {
    const ip = selectedIp || "127.0.0.1";
    const port = draft.srt_port || "<port>";
    const latency = draft.srt_latency || "200";
    return `srt://${ip}:${port}?mode=caller&latency=${latency}`;
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Copied caller URL");
    } catch {
      setStatus(text);
    }
  }

  async function saveVcam(payload: ProgramConfigPayload) {
    try {
      await saveProgramConfig(payload);
      setStatus(
        payload.virtualcam_enabled === undefined
          ? "Updated Excalibur virtual camera"
          : payload.virtualcam_enabled
            ? "Excalibur virtual camera enabled"
            : "Excalibur virtual camera disabled"
      );
      await load();
    } catch (error) {
      setStatus(`error: ${(error as Error).message}`);
    }
  }

  async function save(id: string) {
    const draft = drafts[id];
    if (!draft) return;
    setSavingId(id);
    // Keep the legacy srt_mode field in sync so older readers still work.
    const legacySrtMode =
      draft.source_mode === "srt_listener"
        ? "listener"
        : draft.source_mode === "srt_caller"
          ? "caller"
          : "off";
    try {
      await saveInputConfig({
        input_id: id,
        player_name: draft.player_name || null,
        team: draft.team || null,
        role: draft.role || null,
        obs_scene: draft.obs_scene || null,
        source_mode: draft.source_mode,
        srt_mode: legacySrtMode,
        srt_host: draft.srt_host || null,
        srt_port: draft.srt_port ? Number(draft.srt_port) : null,
        srt_latency: draft.srt_latency ? Number(draft.srt_latency) : null,
        file_path: draft.file_path || null,
        source_url: draft.source_url || null,
        loop: draft.loop,
      });
      setStatus(`Saved ${id}`);
      await load();
    } catch (error) {
      setStatus(`error: ${(error as Error).message}`);
    } finally {
      setSavingId(null);
    }
  }

  if (!config) {
    return (
      <details className="panel collapsible-panel" open>
        <summary>
          <h2>Configuration</h2>
        </summary>
        <p className="meta">Loading configuration…</p>
      </details>
    );
  }

  return (
    <details className="panel collapsible-panel" open>
      <summary>
        <h2>Configuration — inputs</h2>
      </summary>
      <p className="meta">
        Set identity and SRT ingest per input. Listener binds a local port;
        caller dials a remote IP and port. Saving (re)starts that input&apos;s
        FFmpeg worker.
      </p>
      <div className="controls-row">
        <span className="meta">IP to give callers:</span>
        <select value={selectedIp} onChange={(e) => setSelectedIp(e.target.value)}>
          {config.host?.public_ip ? (
            <option value={config.host.public_ip}>
              {config.host.public_ip} (public / WAN)
            </option>
          ) : null}
          {(config.host?.addresses ?? []).map((ip) => (
            <option key={ip} value={ip}>
              {ip} {ip === config.host?.primary_ip ? "(LAN, primary)" : "(LAN)"}
            </option>
          ))}
        </select>
      </div>
      {config.host?.public_ip && selectedIp === config.host.public_ip ? (
        <p className="meta">
          Public / WAN IP: callers on the internet also need your router to
          forward the input&apos;s UDP port to this machine, and the firewall to
          allow it. (If your ISP uses CGNAT, inbound won&apos;t work without a
          relay.)
        </p>
      ) : !config.host?.public_ip ? (
        <p className="meta">
          Public IP unavailable (offline or blocked). LAN IPs only work for
          callers on the same network.
        </p>
      ) : null}
      {config.program ? (
        <div className="cfg-card">
          <div className="cfg-head">
            <span className="cfg-id">Program output → OBS</span>
            <span className="tag">
              {config.program.running
                ? "publishing"
                : config.program.active
                  ? "idle"
                  : "standby"}
            </span>
          </div>
          <p className="meta">
            Add one OBS Media Source pointed at this URL. Excalibur swaps the
            routed input behind it; OBS keeps its layout and overlays. This is
            the program-quality path, separate from the agent preview tiles.
          </p>
          <div className="cfg-caller">
            <span className="meta">OBS media source URL:</span>
            <code>{config.program.obs_url}</code>
            <button onClick={() => copy(config.program.obs_url)}>Copy</button>
          </div>
          <p className="meta">
            Current source:{" "}
            <code>{config.program.source_input ?? "none routed"}</code>
          </p>
        </div>
      ) : null}
      {config.virtualcam ? (
        <div className="cfg-card">
          <div className="cfg-head">
            <span className="cfg-id">Program output → OBS device</span>
            <span className="tag">
              {config.virtualcam.running
                ? "feeding device"
                : config.virtualcam.enabled
                  ? "idle"
                  : "off"}
            </span>
          </div>
          <p className="meta">
            Also expose the program feed as a Windows capture device named{" "}
            <code>{config.virtualcam.device_name}</code> so OBS can pick it from
            the <em>Video Capture Device</em> list instead of (or alongside) the
            SRT Media Source above. Requires{" "}
            <a
              href="https://github.com/webcamoid/akvirtualcamera/releases"
              target="_blank"
              rel="noreferrer"
            >
              akvirtualcamera
            </a>{" "}
            installed on this machine.
          </p>
          <label className="cfg-row">
            <input
              type="checkbox"
              checked={config.virtualcam.enabled}
              onChange={(e) =>
                saveVcam({ virtualcam_enabled: e.target.checked })
              }
            />
            <span>
              Expose <code>{config.virtualcam.device_name}</code> as an OBS
              device ({config.virtualcam.width}×{config.virtualcam.height}@
              {config.virtualcam.fps})
            </span>
          </label>
          {!config.virtualcam.manager_available ? (
            <p className="meta" style={{ color: "#f0883e" }}>
              <code>AkVCamManager</code> not found on PATH. Install
              akvirtualcamera, then run these once as Administrator to register
              the device:
            </p>
          ) : (
            <p className="meta">
              One-time registration (run as Administrator after installing
              akvirtualcamera):
            </p>
          )}
          <div className="cfg-caller">
            <code>{config.virtualcam.setup_commands.join("\n")}</code>
            <button
              onClick={() =>
                copy(config.virtualcam.setup_commands.join("\r\n"))
              }
            >
              Copy
            </button>
          </div>
          <p className="meta">
            Current source:{" "}
            <code>{config.virtualcam.source_input ?? "none routed"}</code>
          </p>
        </div>
      ) : null}
      <div className="cfg-card">
        <div className="cfg-head">
          <span className="cfg-id">Matrix size</span>
          <span className="tag">
            {config.input_count} in / {config.output_count} out
          </span>
        </div>
        <p className="meta">
          Add or remove slots. Only the last (highest-numbered) slot can be
          removed, and only when it is not currently routed.
        </p>
        <div className="controls-row">
          <span className="meta">Inputs: {config.input_count}</span>
          <button onClick={() => resize({ input_count: config.input_count + 1 })}>
            + Add input
          </button>
          <button
            onClick={() => resize({ input_count: config.input_count - 1 })}
            disabled={config.input_count <= 1}
          >
            − Remove last input
          </button>
        </div>
        <div className="controls-row">
          <span className="meta">Outputs: {config.output_count}</span>
          <button onClick={() => resize({ output_count: config.output_count + 1 })}>
            + Add output
          </button>
          <button
            onClick={() => resize({ output_count: config.output_count - 1 })}
            disabled={config.output_count <= 1}
          >
            − Remove last output
          </button>
        </div>
      </div>
      {config.inputs.map((row) => {
        const draft = drafts[row.id] ?? toDraft(row);
        const mode = draft.source_mode;
        const isSrt = SRT_MODES.includes(mode);
        const isCaller = mode === "srt_caller";
        const isUrl = URL_MODES.includes(mode);
        const isFile = mode === "file";
        return (
          <div key={row.id} className="cfg-card">
            <div className="cfg-head">
              <span className="cfg-id">{row.id}</span>
              <span className="tag">{row.state}</span>
            </div>
            <div className="cfg-grid">
              <label>
                Player
                <input
                  value={draft.player_name}
                  placeholder="—"
                  onChange={(e) => update(row.id, "player_name", e.target.value)}
                />
              </label>
              <label>
                Team
                <select
                  value={draft.team}
                  onChange={(e) => update(row.id, "team", e.target.value)}
                >
                  <option value="">—</option>
                  <option value="CT">CT</option>
                  <option value="T">T</option>
                </select>
              </label>
              <label>
                Role
                <input
                  value={draft.role}
                  placeholder="—"
                  onChange={(e) => update(row.id, "role", e.target.value)}
                />
              </label>
              <label>
                OBS scene
                <input
                  value={draft.obs_scene}
                  placeholder="OBS scene"
                  onChange={(e) => update(row.id, "obs_scene", e.target.value)}
                />
              </label>
            </div>
            <div className="cfg-grid">
              <label>
                Source
                <select
                  value={draft.source_mode}
                  onChange={(e) => update(row.id, "source_mode", e.target.value)}
                >
                  {SOURCE_MODE_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </label>
              {isSrt ? (
                <>
                  <label>
                    IP / host
                    <input
                      value={draft.srt_host}
                      placeholder={isCaller ? "10.0.0.5" : "—"}
                      disabled={!isCaller}
                      onChange={(e) => update(row.id, "srt_host", e.target.value)}
                    />
                  </label>
                  <label>
                    Port
                    <input
                      value={draft.srt_port}
                      placeholder="9001"
                      onChange={(e) => update(row.id, "srt_port", e.target.value)}
                    />
                  </label>
                  <label>
                    Latency (ms)
                    <input
                      value={draft.srt_latency}
                      placeholder="200"
                      onChange={(e) => update(row.id, "srt_latency", e.target.value)}
                    />
                  </label>
                </>
              ) : null}
              {isUrl ? (
                <label className="cfg-wide">
                  {mode === "rtsp" ? "RTSP URL" : "Stream URL (HLS/HTTP/...)"}
                  <input
                    value={draft.source_url}
                    placeholder={
                      mode === "rtsp"
                        ? "rtsp://127.0.0.1:8554/input_01"
                        : "https://host/stream.m3u8"
                    }
                    onChange={(e) => update(row.id, "source_url", e.target.value)}
                  />
                </label>
              ) : null}
              {isFile ? (
                <>
                  <label className="cfg-wide">
                    File (on backend machine)
                    <div className="file-row">
                      <input
                        value={draft.file_path}
                        placeholder="Browse or paste a path…"
                        onChange={(e) => update(row.id, "file_path", e.target.value)}
                      />
                      <button
                        type="button"
                        onClick={() => openBrowse(row.id, draft.file_path)}
                      >
                        {browseId === row.id ? "Close" : "Browse…"}
                      </button>
                    </div>
                  </label>
                  <label className="cfg-checkbox">
                    <input
                      type="checkbox"
                      checked={draft.loop}
                      onChange={(e) => update(row.id, "loop", e.target.checked)}
                    />
                    Loop playback
                  </label>
                </>
              ) : null}
            </div>
            {isFile && browseId === row.id ? (
              <div className="file-browser">
                <div className="file-browser-bar">
                  <button
                    type="button"
                    onClick={() => navigate(listing?.parent ?? "")}
                    disabled={browseLoading || !listing || listing.is_root}
                  >
                    Up
                  </button>
                  <code>
                    {listing?.is_root
                      ? "Pick a drive or shortcut"
                      : (listing?.path ?? "…")}
                  </code>
                </div>
                {browseError ? (
                  <p className="meta">error: {browseError}</p>
                ) : null}
                <div className="file-list">
                  {browseLoading ? <p className="meta">Loading…</p> : null}
                  {!browseLoading && listing && listing.entries.length === 0 ? (
                    <p className="meta">No folders or media files here.</p>
                  ) : null}
                  {(listing?.entries ?? []).map((entry) =>
                    entry.is_dir ? (
                      <button
                        key={entry.path}
                        type="button"
                        className="file-entry dir"
                        onClick={() => navigate(entry.path)}
                      >
                        {entry.name}
                        {entry.name.endsWith("/") || entry.name.endsWith("\\")
                          ? ""
                          : "/"}
                      </button>
                    ) : (
                      <button
                        key={entry.path}
                        type="button"
                        className="file-entry media"
                        onClick={() => pickFile(row.id, entry.path)}
                      >
                        {entry.name}
                      </button>
                    )
                  )}
                </div>
              </div>
            ) : null}
            {isFile ? (
              <p className="meta">
                Local file/VOD: the path must exist on the machine running the
                matrix backend. Played in real time; enable loop for repeatable
                local testing. Take it to program like any other input.
              </p>
            ) : null}
            {mode === "srt_listener" ? (
              <div className="cfg-caller">
                <span className="meta">Caller connects to:</span>
                <code>{callerString(draft)}</code>
                <button onClick={() => copy(callerString(draft))}>Copy</button>
              </div>
            ) : null}
            <div className="cfg-foot">
              <span className="meta">{row.uri ?? "no feed"}</span>
              <button onClick={() => save(row.id)} disabled={savingId === row.id}>
                {savingId === row.id ? "…" : "Save"}
              </button>
            </div>
          </div>
        );
      })}
      {status ? <p className="meta">{status}</p> : null}
    </details>
  );
}
