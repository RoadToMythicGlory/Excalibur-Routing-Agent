//! Locating the bundled FFmpeg binary and the local log directory.

use std::path::PathBuf;

/// Resolve the FFmpeg executable.
///
/// Order of preference:
///   1. A `binaries/ffmpeg.exe` shipped next to the Galahad executable (the
///      installer places it there via Tauri resources).
///   2. `ffmpeg` on PATH (developer machines / fallback).
pub fn locate_ffmpeg() -> PathBuf {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let candidates = [
                dir.join("binaries").join(ffmpeg_filename()),
                dir.join(ffmpeg_filename()),
                // Tauri may unpack resources under `resources/`.
                dir.join("resources").join("binaries").join(ffmpeg_filename()),
            ];
            for candidate in candidates {
                if candidate.exists() {
                    return candidate;
                }
            }
        }
    }
    PathBuf::from("ffmpeg")
}

fn ffmpeg_filename() -> &'static str {
    if cfg!(windows) {
        "ffmpeg.exe"
    } else {
        "ffmpeg"
    }
}

/// Directory for rolling local logs: `%APPDATA%/Galahad Encoder/logs`.
pub fn log_dir() -> Option<PathBuf> {
    dirs::config_dir().map(|base| base.join("Galahad Encoder").join("logs"))
}
