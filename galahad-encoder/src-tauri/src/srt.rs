//! SRT caller URL construction.
//!
//! Galahad Encoder always connects to the matrix as an SRT *caller*. The player
//! only supplies a destination host + port (and a latency preset); the server
//! owns which matrix input that port maps to. This module is deliberately tiny
//! and pure so it can be unit tested without any native dependencies.

use crate::error::{EncoderError, Result};

/// Default SRT live latency in milliseconds. Matches the receiver examples in
/// `docs/MEDIA.md` (`latency=200`).
pub const DEFAULT_LATENCY_MS: u32 = 200;

/// MPEG-TS packet payload size recommended for SRT (7 * 188).
pub const SRT_PKT_SIZE: u32 = 1316;

/// A validated SRT caller destination.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SrtDestination {
    pub host: String,
    pub port: u16,
    pub latency_ms: u32,
}

impl SrtDestination {
    /// Validate raw user input into a usable destination.
    ///
    /// `host` may be an IPv4/IPv6 literal or a hostname. We intentionally keep
    /// validation lenient (the operator hands players an address) but reject
    /// obvious mistakes: empty host, whitespace, embedded scheme, and port 0.
    pub fn parse(host: &str, port: u32, latency_ms: u32) -> Result<Self> {
        let host = host.trim();
        if host.is_empty() {
            return Err(EncoderError::Config("destination host is empty".into()));
        }
        if host.contains("://") {
            return Err(EncoderError::Config(
                "destination host must be an IP or hostname, not a URL".into(),
            ));
        }
        if host.chars().any(|c| c.is_whitespace()) {
            return Err(EncoderError::Config(
                "destination host contains whitespace".into(),
            ));
        }
        if port == 0 || port > u16::MAX as u32 {
            return Err(EncoderError::Config(format!(
                "destination port {port} is out of range (1-65535)"
            )));
        }
        let latency_ms = if latency_ms == 0 {
            DEFAULT_LATENCY_MS
        } else {
            latency_ms
        };
        Ok(Self {
            host: host.to_string(),
            port: port as u16,
            latency_ms,
        })
    }

    /// Build the SRT caller URL FFmpeg will publish to.
    pub fn to_url(&self) -> String {
        format!(
            "srt://{host}:{port}?mode=caller&latency={latency}&pkt_size={pkt}",
            host = self.host,
            port = self.port,
            latency = self.latency_ms,
            pkt = SRT_PKT_SIZE,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_caller_url() {
        let dest = SrtDestination::parse("1.2.3.4", 9003, 200).unwrap();
        assert_eq!(
            dest.to_url(),
            "srt://1.2.3.4:9003?mode=caller&latency=200&pkt_size=1316"
        );
    }

    #[test]
    fn trims_host_and_defaults_latency() {
        let dest = SrtDestination::parse("  example.lan  ", 9001, 0).unwrap();
        assert_eq!(dest.host, "example.lan");
        assert_eq!(dest.latency_ms, DEFAULT_LATENCY_MS);
    }

    #[test]
    fn rejects_empty_host() {
        assert!(SrtDestination::parse("   ", 9001, 200).is_err());
    }

    #[test]
    fn rejects_scheme_in_host() {
        assert!(SrtDestination::parse("srt://1.2.3.4", 9001, 200).is_err());
    }

    #[test]
    fn rejects_bad_port() {
        assert!(SrtDestination::parse("1.2.3.4", 0, 200).is_err());
        assert!(SrtDestination::parse("1.2.3.4", 70000, 200).is_err());
    }
}
