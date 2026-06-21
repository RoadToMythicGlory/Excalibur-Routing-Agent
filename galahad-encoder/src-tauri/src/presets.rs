//! Quality presets.
//!
//! Players never tune raw bitrate ladders. They pick a named preset; the client
//! resolves it to resolution / fps / bitrate. Phase 1 targets H.264; HEVC reuses
//! the same geometry with lower bitrate targets but stays experimental.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct QualityPreset {
    /// Stable identifier used in config + control-channel commands, e.g. `1080p30`.
    pub id: &'static str,
    pub label: &'static str,
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    /// Target video bitrate in kbps for H.264.
    pub h264_kbps: u32,
    /// Target video bitrate in kbps for HEVC (experimental, ~30% lower).
    pub hevc_kbps: u32,
}

pub const PRESETS: &[QualityPreset] = &[
    QualityPreset {
        id: "720p30",
        label: "720p30",
        width: 1280,
        height: 720,
        fps: 30,
        h264_kbps: 4000,
        hevc_kbps: 2800,
    },
    QualityPreset {
        id: "1080p30",
        label: "1080p30",
        width: 1920,
        height: 1080,
        fps: 30,
        h264_kbps: 6000,
        hevc_kbps: 4200,
    },
    QualityPreset {
        id: "1080p60",
        label: "1080p60",
        width: 1920,
        height: 1080,
        fps: 60,
        h264_kbps: 8000,
        hevc_kbps: 5600,
    },
];

/// Default preset id used on first run.
pub const DEFAULT_PRESET_ID: &str = "1080p30";

pub fn find(id: &str) -> Option<&'static QualityPreset> {
    PRESETS.iter().find(|p| p.id == id)
}

/// Resolve a preset id, falling back to the default if unknown.
pub fn resolve_or_default(id: &str) -> &'static QualityPreset {
    find(id).unwrap_or_else(|| find(DEFAULT_PRESET_ID).expect("default preset exists"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_are_unique() {
        let mut ids: Vec<&str> = PRESETS.iter().map(|p| p.id).collect();
        ids.sort_unstable();
        let len = ids.len();
        ids.dedup();
        assert_eq!(len, ids.len(), "preset ids must be unique");
    }

    #[test]
    fn default_preset_exists() {
        assert!(find(DEFAULT_PRESET_ID).is_some());
    }

    #[test]
    fn unknown_preset_falls_back() {
        assert_eq!(resolve_or_default("nope").id, DEFAULT_PRESET_ID);
    }

    #[test]
    fn hevc_targets_are_lower_than_h264() {
        for p in PRESETS {
            assert!(p.hevc_kbps < p.h264_kbps, "{} hevc >= h264", p.id);
        }
    }
}
