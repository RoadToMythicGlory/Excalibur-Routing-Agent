//! Persistent client configuration + player / session identity.
//!
//! `playerId` is stable across runs (identifies the human). `sessionId` is fresh
//! per app launch (identifies one connection lifecycle), so the operator can tell
//! "Idan reconnected / restarted PC" apart in the control plane. Neither is tied
//! to a matrix input or slot — routing is server-owned.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::error::Result;
use crate::presets::DEFAULT_PRESET_ID;
use crate::srt::DEFAULT_LATENCY_MS;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioSourceConfig {
    pub enabled: bool,
    pub muted: bool,
    /// 0.0 - 2.0 linear gain. 1.0 is unity.
    pub gain: f32,
}

impl Default for AudioSourceConfig {
    fn default() -> Self {
        Self {
            enabled: true,
            muted: false,
            gain: 1.0,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AudioConfig {
    /// Audio of the selected capture window's process.
    pub game: AudioSourceConfig,
    /// Discord process audio.
    pub discord: AudioSourceConfig,
    /// Microphone (capture device).
    pub microphone: AudioSourceConfig,
    /// Selected microphone endpoint id, if chosen.
    pub microphone_device_id: Option<String>,
    /// Selected Discord process id (resolved from capability scan), if chosen.
    pub discord_process_id: Option<u32>,
}

impl Default for AudioConfig {
    fn default() -> Self {
        Self {
            game: AudioSourceConfig::default(),
            discord: AudioSourceConfig::default(),
            microphone: AudioSourceConfig::default(),
            microphone_device_id: None,
            discord_process_id: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct EncoderConfig {
    /// Allow the CPU software fallback. Off by default for broadcast safety.
    pub allow_software: bool,
    /// Expose / use the experimental HEVC backend.
    pub experimental_hevc: bool,
}

impl Default for EncoderConfig {
    fn default() -> Self {
        Self {
            allow_software: false,
            experimental_hevc: false,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ClientConfig {
    /// Stable player identity (persisted).
    pub player_id: String,
    pub display_name: String,

    /// Destination the operator handed the player.
    pub destination_host: String,
    pub destination_port: u32,
    pub latency_ms: u32,

    /// Quality preset id (see `presets`).
    pub preset_id: String,

    /// Selected capture source (window). Stored loosely so it can be re-resolved
    /// on next launch even though the HWND changes.
    pub source_window_title: Option<String>,
    pub source_process_name: Option<String>,

    pub audio: AudioConfig,
    pub encoder: EncoderConfig,

    /// Control-plane Control Channel endpoint, e.g. `ws://1.2.3.4:8800/ws/encoder`.
    pub control_channel_url: Option<String>,
}

impl Default for ClientConfig {
    fn default() -> Self {
        Self {
            player_id: format!("player-{}", &Uuid::new_v4().to_string()[..8]),
            display_name: String::new(),
            destination_host: String::new(),
            destination_port: 0,
            latency_ms: DEFAULT_LATENCY_MS,
            preset_id: DEFAULT_PRESET_ID.to_string(),
            source_window_title: None,
            source_process_name: None,
            audio: AudioConfig::default(),
            encoder: EncoderConfig::default(),
            control_channel_url: None,
        }
    }
}

impl ClientConfig {
    /// Default on-disk location: `%APPDATA%/Galahad Encoder/config.json`.
    pub fn default_path() -> Result<PathBuf> {
        let base = dirs::config_dir().ok_or_else(|| {
            crate::error::EncoderError::Config("could not resolve config dir".into())
        })?;
        Ok(base.join("Galahad Encoder").join("config.json"))
    }

    pub fn load_or_default(path: &Path) -> Self {
        match std::fs::read(path) {
            Ok(bytes) => serde_json::from_slice(&bytes).unwrap_or_else(|err| {
                log::warn!("config parse failed ({err}); using defaults");
                ClientConfig::default()
            }),
            Err(_) => ClientConfig::default(),
        }
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let bytes = serde_json::to_vec_pretty(self)?;
        std::fs::write(path, bytes)?;
        Ok(())
    }
}

/// Identity emitted on the Control Channel. `session_id` is generated once per
/// process launch and never persisted.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Identity {
    pub player_id: String,
    pub session_id: String,
    pub display_name: String,
}

impl Identity {
    pub fn new(config: &ClientConfig) -> Self {
        Self {
            player_id: config.player_id.clone(),
            session_id: Uuid::new_v4().to_string(),
            display_name: config.display_name.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_generate_player_id() {
        let cfg = ClientConfig::default();
        assert!(cfg.player_id.starts_with("player-"));
        assert_eq!(cfg.preset_id, DEFAULT_PRESET_ID);
    }

    #[test]
    fn round_trips_through_json() {
        let mut cfg = ClientConfig::default();
        cfg.display_name = "Idan".into();
        cfg.destination_host = "1.2.3.4".into();
        cfg.destination_port = 9003;
        let json = serde_json::to_string(&cfg).unwrap();
        let back: ClientConfig = serde_json::from_str(&json).unwrap();
        assert_eq!(back.display_name, "Idan");
        assert_eq!(back.destination_port, 9003);
    }

    #[test]
    fn identity_session_is_unique_per_call() {
        let cfg = ClientConfig::default();
        let a = Identity::new(&cfg);
        let b = Identity::new(&cfg);
        assert_eq!(a.player_id, b.player_id);
        assert_ne!(a.session_id, b.session_id);
    }
}
