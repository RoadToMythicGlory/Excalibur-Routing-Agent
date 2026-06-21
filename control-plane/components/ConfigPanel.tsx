"use client";

import { useCallback, useEffect, useState } from "react";
import {
  fetchConfig,
  saveInputConfig,
  type InputConfigRow,
  type MatrixConfig,
} from "@/lib/api";

interface Draft {
  player_name: string;
  team: string;
  role: string;
  obs_scene: string;
  srt_mode: string;
  srt_host: string;
  srt_port: string;
  srt_latency: string;
}

function str(value: string | number | null): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

function toDraft(row: InputConfigRow): Draft {
  return {
    player_name: row.player_name ?? "",
    team: row.team ?? "",
    role: row.role ?? "",
    obs_scene: row.obs_scene ?? "",
    srt_mode: str(row.srt_mode) || "off",
    srt_host: str(row.srt_host),
    srt_port: str(row.srt_port),
    srt_latency: str(row.srt_latency) || "200",
  };
}

export function ConfigPanel() {
  const [config, setConfig] = useState<MatrixConfig | null>(null);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [status, setStatus] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [selectedIp, setSelectedIp] = useState<string>("");

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

  function update(id: string, field: keyof Draft, value: string) {
    setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], [field]: value } }));
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

  async function save(id: string) {
    const draft = drafts[id];
    if (!draft) return;
    setSavingId(id);
    try {
      await saveInputConfig({
        input_id: id,
        player_name: draft.player_name || null,
        team: draft.team || null,
        role: draft.role || null,
        obs_scene: draft.obs_scene || null,
        srt_mode: draft.srt_mode || "off",
        srt_host: draft.srt_host || null,
        srt_port: draft.srt_port ? Number(draft.srt_port) : null,
        srt_latency: draft.srt_latency ? Number(draft.srt_latency) : null,
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
      {config.inputs.map((row) => {
        const draft = drafts[row.id] ?? toDraft(row);
        const isCaller = draft.srt_mode === "caller";
        const isOff = draft.srt_mode === "off";
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
                SRT mode
                <select
                  value={draft.srt_mode}
                  onChange={(e) => update(row.id, "srt_mode", e.target.value)}
                >
                  <option value="off">Off</option>
                  <option value="listener">Listener (bind port)</option>
                  <option value="caller">Caller (dial IP:port)</option>
                </select>
              </label>
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
                  disabled={isOff}
                  onChange={(e) => update(row.id, "srt_port", e.target.value)}
                />
              </label>
              <label>
                Latency (ms)
                <input
                  value={draft.srt_latency}
                  placeholder="200"
                  disabled={isOff}
                  onChange={(e) => update(row.id, "srt_latency", e.target.value)}
                />
              </label>
            </div>
            {draft.srt_mode === "listener" ? (
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
