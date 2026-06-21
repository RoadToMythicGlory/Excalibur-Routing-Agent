//! Tauri command surface + shared application state.
//!
//! The same start/stop/restart/switch primitives back both the UI commands and
//! the Control Channel dispatcher, so an operator can drive the client remotely.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use crate::audio::AudioPlan;
use crate::capability::{self, Capabilities};
use crate::config::{ClientConfig, Identity};
use crate::control_channel::{ClientMessage, CommandAck, ServerCommand};
use crate::control_runtime::ControlHandle;
use crate::encoder::{self, Codec};
use crate::error::{EncoderError, Result};
use crate::logger::LogBuffer;
use crate::pipeline::{Pipeline, PipelineConfig, StreamStatus};
use crate::presets;
use crate::process_enum::ProcessInfo;
use crate::srt::SrtDestination;
use crate::window_enum::{self, WindowInfo};

pub struct AppStateInner {
    pub config: Mutex<ClientConfig>,
    pub config_path: PathBuf,
    pub caps: Mutex<Capabilities>,
    pub pipeline: Mutex<Option<Pipeline>>,
    pub identity: Identity,
    pub ffmpeg_path: PathBuf,
    pub logs: LogBuffer,
    pub control: Mutex<Option<ControlHandle>>,
    /// Window the player picked (id + label), set from the UI before start.
    pub selected_window: Mutex<Option<(isize, String)>>,
}

#[derive(Clone)]
pub struct AppState(pub Arc<AppStateInner>);

impl AppStateInner {
    fn build_pipeline_config(&self) -> Result<PipelineConfig> {
        let config = self.config.lock().unwrap().clone();
        let caps = self.caps.lock().unwrap().clone();

        let (window_id, label) = self
            .selected_window
            .lock()
            .unwrap()
            .clone()
            .ok_or_else(|| EncoderError::Config("no capture source selected".into()))?;

        let destination = SrtDestination::parse(
            &config.destination_host,
            config.destination_port,
            config.latency_ms,
        )?;

        let preset = presets::resolve_or_default(&config.preset_id);

        // Codec preference: experimental HEVC only if explicitly enabled, else H264.
        let preferred = if config.encoder.experimental_hevc {
            Codec::Hevc
        } else {
            Codec::H264
        };
        let backend = encoder::select(preferred, &caps.encoders, config.encoder.allow_software)
            .or_else(|| {
                encoder::select(Codec::H264, &caps.encoders, config.encoder.allow_software)
            })
            .ok_or_else(|| {
                EncoderError::Encoder(
                    "no compatible hardware encoder found. Update GPU drivers or enable the \
                     software fallback in settings."
                        .into(),
                )
            })?;

        let video_kbps = match backend.codec {
            Codec::H264 => preset.h264_kbps,
            Codec::Hevc => preset.hevc_kbps,
        };

        let audio_plan = build_audio_plan(&config);

        Ok(PipelineConfig {
            window_id,
            source_label: label,
            fps: preset.fps,
            out_width: preset.width,
            out_height: preset.height,
            video_kbps,
            backend,
            destination,
            audio_plan,
            audio_kbps: 160,
        })
    }

    pub fn start(&self) -> Result<()> {
        let config = self.build_pipeline_config()?;
        let pipeline = Pipeline::start(config, self.ffmpeg_path.clone());
        let mut slot = self.pipeline.lock().unwrap();
        if let Some(mut old) = slot.take() {
            old.stop();
        }
        *slot = Some(pipeline);
        log::info!("stream started");
        Ok(())
    }

    pub fn stop(&self) {
        if let Some(mut pipeline) = self.pipeline.lock().unwrap().take() {
            pipeline.stop();
            log::info!("stream stopped");
        }
    }

    pub fn restart(&self) -> Result<()> {
        let needs_new = {
            let slot = self.pipeline.lock().unwrap();
            slot.as_ref().map(|p| p.restart()).is_none()
        };
        if needs_new {
            self.start()
        } else {
            log::info!("stream restart requested");
            Ok(())
        }
    }

    pub fn switch_quality(&self, preset_id: &str) -> Result<()> {
        if presets::find(preset_id).is_none() {
            return Err(EncoderError::Config(format!("unknown preset '{preset_id}'")));
        }
        self.config.lock().unwrap().preset_id = preset_id.to_string();
        self.persist()?;

        let new_config = self.build_pipeline_config()?;
        let mut slot = self.pipeline.lock().unwrap();
        match slot.as_ref() {
            Some(pipeline) => pipeline.update_config(new_config),
            None => {} // not streaming; preset is saved for next start
        }
        log::info!("quality switched to {preset_id}");
        Ok(())
    }

    pub fn status(&self) -> StreamStatus {
        self.pipeline
            .lock()
            .unwrap()
            .as_ref()
            .map(|p| p.status())
            .unwrap_or_default()
    }

    pub fn persist(&self) -> Result<()> {
        let config = self.config.lock().unwrap().clone();
        config.save(&self.config_path)
    }
}

fn build_audio_plan(config: &ClientConfig) -> AudioPlan {
    let game_on = config.audio.game.enabled && !config.audio.game.muted;
    let discord_on = config.audio.discord.enabled && !config.audio.discord.muted;
    let mic_on = config.audio.microphone.enabled && !config.audio.microphone.muted;

    // Without per-process isolation, game + Discord share the system render
    // loopback; combine their gains.
    let mut gains = Vec::new();
    if game_on {
        gains.push(config.audio.game.gain);
    }
    if discord_on {
        gains.push(config.audio.discord.gain);
    }
    let system_gain = if gains.is_empty() {
        1.0
    } else {
        gains.iter().sum::<f32>() / gains.len() as f32
    };

    AudioPlan {
        system_loopback: game_on || discord_on,
        system_gain,
        microphone: mic_on,
        microphone_device_id: config.audio.microphone_device_id.clone(),
        microphone_gain: config.audio.microphone.gain,
    }
}

/// Build a telemetry message from current status + identity.
pub fn telemetry_message(inner: &AppStateInner) -> ClientMessage {
    let status = inner.status();
    let streaming = matches!(
        status.state,
        crate::stream_state::StreamState::Streaming | crate::stream_state::StreamState::Degraded
    );
    ClientMessage::Telemetry(crate::control_channel::Telemetry {
        player_id: inner.identity.player_id.clone(),
        session_id: inner.identity.session_id.clone(),
        display_name: inner.identity.display_name.clone(),
        source: status.source,
        streaming,
        state: status.state,
        fps: status.fps,
        bitrate: status.bitrate,
        dropped_frames: status.dropped_frames,
        encoder: status.encoder,
        capture_backend: status.capture_backend,
        last_error: status.last_error,
    })
}

/// Dispatch an operator command and emit an ack on the Control Channel.
pub fn dispatch_command(inner: &Arc<AppStateInner>, cmd: ServerCommand) {
    let action = cmd.action_name().to_string();
    let result: Result<Option<String>> = match cmd {
        ServerCommand::StartStream => inner.start().map(|_| None),
        ServerCommand::StopStream => {
            inner.stop();
            Ok(None)
        }
        ServerCommand::RestartStream => inner.restart().map(|_| None),
        ServerCommand::SwitchQuality { preset } => inner.switch_quality(&preset).map(|_| None),
        ServerCommand::RequestLogUpload => {
            if let Some(handle) = inner.control.lock().unwrap().as_ref() {
                handle.send(ClientMessage::LogUpload {
                    session_id: inner.identity.session_id.clone(),
                    lines: inner.logs.snapshot(),
                });
            }
            Ok(Some("log upload sent".into()))
        }
        ServerCommand::PushConfig { config } => apply_pushed_config(inner, config),
    };

    let ack = match result {
        Ok(detail) => CommandAck {
            action,
            ok: true,
            detail,
        },
        Err(e) => CommandAck {
            action,
            ok: false,
            detail: Some(e.to_string()),
        },
    };
    if let Some(handle) = inner.control.lock().unwrap().as_ref() {
        handle.send(ClientMessage::CommandAck(ack));
    }
}

fn apply_pushed_config(inner: &Arc<AppStateInner>, value: serde_json::Value) -> Result<Option<String>> {
    // Merge only known, safe fields from the pushed config.
    {
        let mut config = inner.config.lock().unwrap();
        if let Some(preset) = value.get("presetId").and_then(|v| v.as_str()) {
            if presets::find(preset).is_some() {
                config.preset_id = preset.to_string();
            }
        }
        if let Some(host) = value.get("destinationHost").and_then(|v| v.as_str()) {
            config.destination_host = host.to_string();
        }
        if let Some(port) = value.get("destinationPort").and_then(|v| v.as_u64()) {
            config.destination_port = port as u32;
        }
        if let Some(latency) = value.get("latencyMs").and_then(|v| v.as_u64()) {
            config.latency_ms = latency as u32;
        }
    }
    inner.persist()?;
    Ok(Some("config applied".into()))
}

// ---------------------------------------------------------------------------
// Tauri commands
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn get_capabilities(state: tauri::State<AppState>) -> Capabilities {
    state.0.caps.lock().unwrap().clone()
}

#[tauri::command]
pub fn refresh_capabilities(state: tauri::State<AppState>) -> Capabilities {
    let caps = capability::detect();
    *state.0.caps.lock().unwrap() = caps.clone();
    caps
}

#[tauri::command]
pub fn list_windows() -> Vec<WindowInfo> {
    window_enum::list_windows()
}

#[tauri::command]
pub fn list_microphones() -> Vec<crate::audio::MicrophoneInfo> {
    crate::audio::list_microphones()
}

#[tauri::command]
pub fn discord_processes() -> Vec<ProcessInfo> {
    crate::process_enum::discord_processes()
}

#[tauri::command]
pub fn get_config(state: tauri::State<AppState>) -> ClientConfig {
    state.0.config.lock().unwrap().clone()
}

#[tauri::command]
pub fn save_config(state: tauri::State<AppState>, config: ClientConfig) -> Result<()> {
    *state.0.config.lock().unwrap() = config;
    state.0.persist()
}

#[tauri::command]
pub fn get_identity(state: tauri::State<AppState>) -> Identity {
    state.0.identity.clone()
}

#[tauri::command]
pub fn select_window(state: tauri::State<AppState>, window_id: i64, label: String) {
    *state.0.selected_window.lock().unwrap() = Some((window_id as isize, label));
}

#[tauri::command]
pub fn get_status(state: tauri::State<AppState>) -> StreamStatus {
    state.0.status()
}

#[tauri::command]
pub fn start_stream(state: tauri::State<AppState>) -> Result<()> {
    state.0.start()
}

#[tauri::command]
pub fn stop_stream(state: tauri::State<AppState>) {
    state.0.stop();
}

#[tauri::command]
pub fn restart_stream(state: tauri::State<AppState>) -> Result<()> {
    state.0.restart()
}

#[tauri::command]
pub fn switch_quality(state: tauri::State<AppState>, preset: String) -> Result<()> {
    state.0.switch_quality(&preset)
}

#[tauri::command]
pub fn get_logs(state: tauri::State<AppState>) -> Vec<String> {
    state.0.logs.snapshot()
}
