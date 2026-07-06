import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { Button, Card, Field, Pill, Toggle } from "./ui";
import {
  PRESETS,
  type Capabilities,
  type ClientConfig,
  type Identity,
  type StreamStatus,
  type WindowInfo,
} from "./types";

const LATENCY_OPTIONS = [
  { value: 120, label: "Low (120 ms)" },
  { value: 200, label: "Normal (200 ms)" },
  { value: 400, label: "High / unstable network (400 ms)" },
];

const STATE_TONE: Record<string, string> = {
  idle: "muted",
  starting: "info",
  streaming: "ok",
  degraded: "warn",
  reconnecting: "warn",
  failed: "bad",
};

export function App() {
  const [config, setConfig] = useState<ClientConfig | null>(null);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [windows, setWindows] = useState<WindowInfo[]>([]);
  const [selectedWindowId, setSelectedWindowId] = useState<number | null>(null);
  const [status, setStatus] = useState<StreamStatus | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const logRef = useRef<HTMLPreElement | null>(null);

  // Initial load.
  useEffect(() => {
    (async () => {
      try {
        const [cfg, capabilities, id, wins] = await Promise.all([
          api.getConfig(),
          api.getCapabilities(),
          api.getIdentity(),
          api.listWindows(),
        ]);
        setConfig(cfg);
        setCaps(capabilities);
        setIdentity(id);
        setWindows(wins);
      } catch (e) {
        setMessage(`Startup failed: ${String(e)}`);
      }
    })();
  }, []);

  // Status + log polling.
  useEffect(() => {
    const timer = setInterval(async () => {
      try {
        const [s, l] = await Promise.all([api.getStatus(), api.getLogs()]);
        setStatus(s);
        setLogs(l);
      } catch {
        /* transient */
      }
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [logs]);

  const updateConfig = useCallback(
    (mutator: (draft: ClientConfig) => void) => {
      setConfig((prev) => {
        if (!prev) return prev;
        const next = structuredClone(prev) as ClientConfig;
        mutator(next);
        api.saveConfig(next).catch((e) => setMessage(`Save failed: ${String(e)}`));
        return next;
      });
    },
    []
  );

  const canEncode = useMemo(() => {
    if (!caps || !config) return false;
    if (caps.encoders.length === 0) return false;
    const hasHardware = caps.encoderBackends.some((b) => b.hardware);
    return hasHardware || config.encoder.allowSoftware;
  }, [caps, config]);

  const destinationValid = useMemo(() => {
    if (!config) return false;
    return (
      config.destinationHost.trim().length > 0 &&
      config.destinationPort > 0 &&
      config.destinationPort <= 65535
    );
  }, [config]);

  const hevcAvailable = useMemo(
    () => caps?.encoderBackends.some((b) => b.codec === "hevc") ?? false,
    [caps]
  );

  const isStreaming = status
    ? ["starting", "streaming", "degraded", "reconnecting"].includes(status.state)
    : false;

  const refreshWindows = useCallback(async () => {
    setWindows(await api.listWindows());
  }, []);

  const selectWindow = useCallback(
    async (win: WindowInfo) => {
      setSelectedWindowId(win.id);
      const label = `${win.processName} — ${win.title}`;
      await api.selectWindow(win.id, label);
      updateConfig((c) => {
        c.sourceWindowTitle = win.title;
        c.sourceProcessName = win.processName;
      });
    },
    [updateConfig]
  );

  useEffect(() => {
    if (!config || !caps || selectedWindowId !== null || windows.length === 0) {
      return;
    }
    if (caps.capture === "browser-preview" || caps.capture === "none") {
      return;
    }

    const savedWindow = windows.find((win) => {
      const processMatches =
        !config.sourceProcessName || win.processName === config.sourceProcessName;
      const titleMatches =
        !config.sourceWindowTitle || win.title === config.sourceWindowTitle;
      return processMatches && titleMatches;
    });

    if (savedWindow && (config.sourceProcessName || config.sourceWindowTitle)) {
      void selectWindow(savedWindow);
    }
  }, [caps, config, selectedWindowId, selectWindow, windows]);

  const onStart = useCallback(async () => {
    try {
      setMessage(null);
      await api.startStream();
    } catch (e) {
      setMessage(`Start failed: ${String(e)}`);
    }
  }, []);

  const onStop = useCallback(async () => {
    try {
      await api.stopStream();
    } catch (e) {
      setMessage(`Stop failed: ${String(e)}`);
    }
  }, []);

  const onPreset = useCallback(
    async (presetId: string) => {
      updateConfig((c) => {
        c.presetId = presetId;
      });
      if (isStreaming) {
        try {
          await api.switchQuality(presetId);
        } catch (e) {
          setMessage(`Switch quality failed: ${String(e)}`);
        }
      }
    },
    [isStreaming, updateConfig]
  );

  if (!config || !caps || !identity) {
    return (
      <div className="app loading">
        <img src="/logo.png" alt="Galahad Client Agent" className="brand-logo" />
        <p>{message ?? "Detecting capabilities…"}</p>
      </div>
    );
  }

  const captureUnavailable = caps.capture === "browser-preview" || caps.capture === "none";
  const startBlocker = captureUnavailable
    ? caps.capture === "browser-preview"
      ? "Open the Tauri desktop app to capture and stream."
      : "Desktop capture is unavailable on this system."
    : !destinationValid
    ? "Enter destination IP and port."
    : selectedWindowId === null
    ? "Select a source window."
    : !canEncode
    ? "No usable encoder."
    : !caps.ffmpegAvailable
    ? "FFmpeg is missing."
    : null;
  const startDisabled = startBlocker !== null;

  return (
    <div className="app">
      <header className="app-bar">
        <img src="/logo.png" alt="Galahad Client Agent" className="brand-logo" />
        <div className="app-bar-right">
          <Pill tone={STATE_TONE[status?.state ?? "idle"] ?? "muted"}>
            {(status?.state ?? "idle").toUpperCase()}
          </Pill>
        </div>
      </header>

      {message ? <div className="banner">{message}</div> : null}

      <main className="grid">
        {/* Destination */}
        <Card
          title="Destination"
          subtitle="Your operator gives you an IP and port. That's all you need."
          accent
        >
          <div className="row">
            <Field label="Server IP">
              <input
                value={config.destinationHost}
                placeholder="1.2.3.4"
                onChange={(e) =>
                  updateConfig((c) => (c.destinationHost = e.target.value))
                }
              />
            </Field>
            <Field label="Port">
              <input
                value={config.destinationPort || ""}
                placeholder="9003"
                inputMode="numeric"
                onChange={(e) =>
                  updateConfig(
                    (c) =>
                      (c.destinationPort = Number(e.target.value.replace(/\D/g, "")) || 0)
                  )
                }
              />
            </Field>
          </div>
          <Field label="Latency preset">
            <select
              value={config.latencyMs}
              onChange={(e) =>
                updateConfig((c) => (c.latencyMs = Number(e.target.value)))
              }
            >
              {LATENCY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          {!destinationValid ? (
            <p className="hint warn">Enter the IP and port from your operator.</p>
          ) : null}
        </Card>

        {/* Identity */}
        <Card title="Identity" subtitle="Shown to the operator so they know who's who.">
          <Field label="Display name">
            <input
              value={config.displayName}
              placeholder="e.g. Idan"
              onChange={(e) =>
                updateConfig((c) => (c.displayName = e.target.value))
              }
            />
          </Field>
          <div className="kv">
            <span>Player ID</span>
            <code>{identity.playerId}</code>
          </div>
          <div className="kv">
            <span>Session ID</span>
            <code className="muted">{identity.sessionId.slice(0, 8)}…</code>
          </div>
        </Card>

        {/* Source */}
        <Card
          title="Source"
          subtitle="Pick the game or app window to broadcast."
        >
          {captureUnavailable ? (
            <div className="source-unavailable">
              <span className="window-proc">
                {caps.capture === "browser-preview"
                  ? "Browser preview mode"
                  : "No capture backend"}
              </span>
              <span className="window-title">
                {caps.capture === "browser-preview"
                  ? "Run npm run tauri:dev to select a real game window."
                  : "Window capture is only supported by the desktop client."}
              </span>
            </div>
          ) : (
            <>
              <div className="source-head">
                <span className="hint">
                  {selectedWindowId !== null
                    ? "Selected source is highlighted."
                    : "Select a window below."}
                </span>
                <Button variant="ghost" onClick={refreshWindows}>
                  Refresh
                </Button>
              </div>
              <div className="window-list">
                {windows.length === 0 ? (
                  <p className="hint">No windows found. Open your game, then Refresh.</p>
                ) : (
                  windows.map((w) => (
                    <button
                      key={w.id}
                      className={`window-item${
                        selectedWindowId === w.id ? " selected" : ""
                      }`}
                      onClick={() => selectWindow(w)}
                    >
                      <span className="window-proc">{w.processName}</span>
                      <span className="window-title">{w.title}</span>
                    </button>
                  ))
                )}
              </div>
            </>
          )}
        </Card>

        {/* Audio */}
        <Card title="Audio" subtitle="Game + Discord + microphone are mixed into the stream.">
          <AudioRow
            label="Game / app audio"
            cfg={config.audio.game}
            onToggle={(v) => updateConfig((c) => (c.audio.game.enabled = v))}
            onMute={(v) => updateConfig((c) => (c.audio.game.muted = v))}
            onGain={(v) => updateConfig((c) => (c.audio.game.gain = v))}
          />
          <AudioRow
            label={`Discord${caps.discordAudio ? "" : " (not detected)"}`}
            cfg={config.audio.discord}
            onToggle={(v) => updateConfig((c) => (c.audio.discord.enabled = v))}
            onMute={(v) => updateConfig((c) => (c.audio.discord.muted = v))}
            onGain={(v) => updateConfig((c) => (c.audio.discord.gain = v))}
          />
          <AudioRow
            label="Microphone"
            cfg={config.audio.microphone}
            onToggle={(v) => updateConfig((c) => (c.audio.microphone.enabled = v))}
            onMute={(v) => updateConfig((c) => (c.audio.microphone.muted = v))}
            onGain={(v) => updateConfig((c) => (c.audio.microphone.gain = v))}
          />
          <Field label="Microphone device">
            <select
              value={config.audio.microphoneDeviceId ?? ""}
              onChange={(e) =>
                updateConfig(
                  (c) =>
                    (c.audio.microphoneDeviceId = e.target.value || null)
                )
              }
            >
              <option value="">System default</option>
              {caps.microphones.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name}
                </option>
              ))}
            </select>
          </Field>
          {!caps.processAudio ? (
            <p className="hint warn">
              Per-app audio isolation unavailable on this PC; using system audio
              (game + Discord) + mic.
            </p>
          ) : null}
        </Card>

        {/* Quality */}
        <Card title="Quality" subtitle="Higher settings need more upload bandwidth.">
          <div className="preset-row">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                className={`preset${config.presetId === p.id ? " selected" : ""}`}
                onClick={() => onPreset(p.id)}
              >
                {p.label}
              </button>
            ))}
          </div>
          <Toggle
            label="Allow software encoder (CPU heavy)"
            checked={config.encoder.allowSoftware}
            onChange={(v) => updateConfig((c) => (c.encoder.allowSoftware = v))}
          />
          <Toggle
            label="Experimental HEVC (saves bandwidth)"
            disabled={!hevcAvailable}
            checked={config.encoder.experimentalHevc}
            onChange={(v) => updateConfig((c) => (c.encoder.experimentalHevc = v))}
          />
          {!canEncode ? (
            <p className="hint warn">
              No hardware encoder detected. Update GPU drivers or enable the
              software encoder above.
            </p>
          ) : null}
        </Card>

        {/* Status */}
        <Card title="Status">
          <div className="kv">
            <span>State</span>
            <Pill tone={STATE_TONE[status?.state ?? "idle"] ?? "muted"}>
              {(status?.state ?? "idle").toUpperCase()}
            </Pill>
          </div>
          <div className="kv">
            <span>Auto reconnect</span>
            <Pill tone="ok">ENABLED</Pill>
          </div>
          <div className="kv">
            <span>Source</span>
            <code>{status?.source ?? "—"}</code>
          </div>
          <div className="kv">
            <span>Encoder</span>
            <code>{status?.encoder ?? "—"}</code>
          </div>
          <div className="kv">
            <span>FPS / bitrate</span>
            <code>
              {status?.fps ?? 0} / {status?.bitrate ?? 0} kbps
            </code>
          </div>
          <div className="kv">
            <span>Dropped frames</span>
            <code>{status?.droppedFrames ?? 0}</code>
          </div>
          {status?.lastError ? (
            <p className="hint warn">{status.lastError}</p>
          ) : null}
          {status?.audioWarnings?.map((w, i) => (
            <p key={i} className="hint">
              {w}
            </p>
          ))}
        </Card>

        {/* Capabilities */}
        <Card title="This PC" subtitle="Detected at startup.">
          <div className="kv">
            <span>Capture</span>
            <code>{caps.capture}</code>
          </div>
          <div className="kv">
            <span>Encoders</span>
            <code>{caps.encoders.join(", ") || "none"}</code>
          </div>
          <div className="kv">
            <span>Process audio</span>
            <code>{caps.processAudio ? "yes" : "no (system mix)"}</code>
          </div>
          <div className="kv">
            <span>Discord</span>
            <code>{caps.discordAudio ? "running" : "not detected"}</code>
          </div>
          <div className="kv">
            <span>Microphones</span>
            <code>{caps.microphones.length}</code>
          </div>
          <div className="kv">
            <span>FFmpeg</span>
            <code>{caps.ffmpegAvailable ? "ready" : "missing"}</code>
          </div>
          <div className="kv">
            <span>OS</span>
            <code>
              {caps.os.name} {caps.os.build ?? ""}
            </code>
          </div>
        </Card>

        {/* Diagnostics */}
        <Card title="Diagnostics" subtitle="Local log (also sent on operator request).">
          <pre className="logs" ref={logRef}>
            {logs.length ? logs.join("\n") : "No logs yet."}
          </pre>
          <Button
            variant="ghost"
            onClick={() => navigator.clipboard.writeText(logs.join("\n"))}
          >
            Copy log
          </Button>
        </Card>
      </main>

      <footer className="controls">
        {isStreaming ? (
          <Button variant="danger" onClick={onStop}>
            Stop
          </Button>
        ) : (
          <Button variant="primary" onClick={onStart} disabled={startDisabled}>
            Start Streaming
          </Button>
        )}
        {startBlocker && !isStreaming ? <span className="hint">{startBlocker}</span> : null}
      </footer>
    </div>
  );
}

function AudioRow({
  label,
  cfg,
  onToggle,
  onMute,
  onGain,
}: {
  label: string;
  cfg: { enabled: boolean; muted: boolean; gain: number };
  onToggle: (v: boolean) => void;
  onMute: (v: boolean) => void;
  onGain: (v: number) => void;
}) {
  return (
    <div className="audio-row">
      <Toggle label={label} checked={cfg.enabled} onChange={onToggle} />
      <input
        className="slider"
        type="range"
        min={0}
        max={2}
        step={0.05}
        value={cfg.gain}
        disabled={!cfg.enabled || cfg.muted}
        onChange={(e) => onGain(Number(e.target.value))}
      />
      <button
        className={`mute${cfg.muted ? " active" : ""}`}
        disabled={!cfg.enabled}
        onClick={() => onMute(!cfg.muted)}
      >
        {cfg.muted ? "Muted" : "Mute"}
      </button>
    </div>
  );
}
