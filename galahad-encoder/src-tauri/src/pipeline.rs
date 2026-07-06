//! Streaming pipeline supervisor.
//!
//! Wires capture + audio mixing + FFmpeg into one managed session and drives the
//! always-on reconnect state machine. Frames flow capture -> FFmpeg stdin; mixed
//! PCM flows audio-engine -> localhost TCP -> FFmpeg. The supervisor owns process
//! lifecycle so Start/Stop/Restart never leak FFmpeg or capture threads.

use std::io::Write;
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use crossbeam_channel::bounded;
use serde::Serialize;

use crate::audio::{self, AudioPlan};
use crate::capture::{self, CaptureConfig};
use crate::encoder::EncoderBackend;
use crate::error::{EncoderError, Result};
use crate::ffmpeg::FfmpegPlan;
use crate::srt::SrtDestination;
use crate::stream_state::{StreamEvent, StreamMachine, StreamState};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Fully resolved configuration for one streaming session.
#[derive(Debug, Clone)]
pub struct PipelineConfig {
    pub window_id: isize,
    pub source_label: String,
    pub fps: u32,
    pub out_width: u32,
    pub out_height: u32,
    pub video_kbps: u32,
    pub backend: EncoderBackend,
    pub destination: SrtDestination,
    pub audio_plan: AudioPlan,
    pub audio_kbps: u32,
}

/// Snapshot of pipeline status for the UI and Control Channel telemetry.
#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StreamStatus {
    pub state: StreamState,
    pub source: Option<String>,
    pub fps: u32,
    pub bitrate: u32,
    pub dropped_frames: u64,
    pub encoder: Option<String>,
    pub capture_backend: Option<String>,
    pub last_error: Option<String>,
    pub audio_warnings: Vec<String>,
}

impl Default for StreamStatus {
    fn default() -> Self {
        Self {
            state: StreamState::Idle,
            source: None,
            fps: 0,
            bitrate: 0,
            dropped_frames: 0,
            encoder: None,
            capture_backend: None,
            last_error: None,
            audio_warnings: Vec::new(),
        }
    }
}

struct Inner {
    stop: AtomicBool,
    restart: AtomicBool,
    desired: Mutex<PipelineConfig>,
    status: Mutex<StreamStatus>,
    ffmpeg_path: PathBuf,
}

/// Owns the supervisor thread for an active stream.
pub struct Pipeline {
    inner: Arc<Inner>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Pipeline {
    /// Start streaming with the given config. The supervisor runs until `stop`.
    pub fn start(config: PipelineConfig, ffmpeg_path: PathBuf) -> Self {
        let inner = Arc::new(Inner {
            stop: AtomicBool::new(false),
            restart: AtomicBool::new(false),
            desired: Mutex::new(config),
            status: Mutex::new(StreamStatus::default()),
            ffmpeg_path,
        });
        let thread_inner = inner.clone();
        let thread = std::thread::Builder::new()
            .name("pipeline-supervisor".into())
            .spawn(move || supervise(thread_inner))
            .ok();
        Pipeline { inner, thread }
    }

    pub fn status(&self) -> StreamStatus {
        self.inner.status.lock().unwrap().clone()
    }

    pub fn restart(&self) {
        self.inner.restart.store(true, Ordering::SeqCst);
    }

    /// Update the desired config (e.g. quality switch) and trigger a restart.
    pub fn update_config(&self, config: PipelineConfig) {
        *self.inner.desired.lock().unwrap() = config;
        self.inner.restart.store(true, Ordering::SeqCst);
    }

    pub fn stop(&mut self) {
        self.inner.stop.store(true, Ordering::SeqCst);
        if let Some(handle) = self.thread.take() {
            let _ = handle.join();
        }
        let mut status = self.inner.status.lock().unwrap();
        status.state = StreamState::Idle;
    }
}

impl Drop for Pipeline {
    fn drop(&mut self) {
        self.stop();
    }
}

fn set_state(inner: &Inner, state: StreamState) {
    inner.status.lock().unwrap().state = state;
}

fn set_error(inner: &Inner, error: Option<String>) {
    inner.status.lock().unwrap().last_error = error;
}

fn supervise(inner: Arc<Inner>) {
    let mut machine = StreamMachine::default();
    machine.apply(StreamEvent::Start);
    set_state(&inner, StreamState::Starting);

    while !inner.stop.load(Ordering::SeqCst) {
        inner.restart.store(false, Ordering::SeqCst);
        let config = inner.desired.lock().unwrap().clone();

        match run_session(&inner, &config, &mut machine) {
            Ok(SessionEnd::UserStop) => break,
            Ok(SessionEnd::Restart) => {
                // Quality switch / explicit restart: loop straight back in.
                set_state(&inner, StreamState::Starting);
                machine.apply(StreamEvent::Start);
                continue;
            }
            Ok(SessionEnd::Ended) | Err(_) => {
                if inner.stop.load(Ordering::SeqCst) {
                    break;
                }
                machine.apply(StreamEvent::RecoverableError);
                set_state(&inner, StreamState::Reconnecting);
                machine.apply(StreamEvent::RetryTick);
                let delay = machine.current_backoff();
                // Sleep in small slices so Stop is responsive.
                let mut waited = Duration::ZERO;
                while waited < delay && !inner.stop.load(Ordering::SeqCst) {
                    std::thread::sleep(Duration::from_millis(100));
                    waited += Duration::from_millis(100);
                }
            }
        }
    }

    machine.apply(StreamEvent::Stop);
    set_state(&inner, StreamState::Idle);
}

enum SessionEnd {
    /// User pressed Stop.
    UserStop,
    /// Explicit restart / quality switch requested.
    Restart,
    /// Session ended on its own (process exit, write failure).
    Ended,
}

fn run_session(
    inner: &Arc<Inner>,
    config: &PipelineConfig,
    machine: &mut StreamMachine,
) -> Result<SessionEnd> {
    // --- Capture ---
    let (frame_tx, frame_rx) = bounded::<capture::FrameBuffer>(4);
    let mut capture_session = capture::start(
        CaptureConfig {
            window_id: config.window_id,
            fps: config.fps,
        },
        frame_tx,
    )?;
    let cap_w = capture_session.width;
    let cap_h = capture_session.height;

    {
        let mut status = inner.status.lock().unwrap();
        status.source = Some(config.source_label.clone());
        status.capture_backend = Some("wgc".into());
        status.encoder = Some(config.backend.ffmpeg_name.to_string());
        status.fps = config.fps;
        status.bitrate = config.video_kbps + if config.audio_plan.any_enabled() { config.audio_kbps } else { 0 };
        status.dropped_frames = 0;
        status.last_error = None;
    }

    // --- Audio TCP transport ---
    let audio_enabled = config.audio_plan.any_enabled();
    let (audio_listener, audio_url) = if audio_enabled {
        let listener = TcpListener::bind("127.0.0.1:0")
            .map_err(|e| EncoderError::Pipeline(format!("audio socket bind failed: {e}")))?;
        let port = listener
            .local_addr()
            .map_err(|e| EncoderError::Pipeline(format!("audio socket addr failed: {e}")))?
            .port();
        (Some(listener), format!("tcp://127.0.0.1:{port}"))
    } else {
        (None, String::new())
    };

    // --- FFmpeg ---
    let plan = FfmpegPlan {
        capture_width: cap_w,
        capture_height: cap_h,
        out_width: config.out_width,
        out_height: config.out_height,
        fps: config.fps,
        video_kbps: config.video_kbps,
        backend: config.backend,
        destination: config.destination.clone(),
        audio_enabled,
        audio_input: audio_url,
        audio_sample_rate: audio::SAMPLE_RATE,
        audio_channels: audio::CHANNELS,
        audio_kbps: config.audio_kbps,
    };
    let args = plan.build_args();
    log::info!("ffmpeg {}", args.join(" "));

    let mut command = Command::new(&inner.ffmpeg_path);
    command
        .args(&args)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    let mut child: Child = command
        .spawn()
        .map_err(|e| EncoderError::Pipeline(format!("failed to launch ffmpeg: {e}")))?;

    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| EncoderError::Pipeline("ffmpeg stdin unavailable".into()))?;

    // Drain ffmpeg stderr to the log so capture/encode warnings are visible.
    if let Some(stderr) = child.stderr.take() {
        std::thread::spawn(move || {
            use std::io::{BufRead, BufReader};
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(|l| l.ok()) {
                log::warn!("ffmpeg: {line}");
            }
        });
    }

    let session_alive = Arc::new(AtomicBool::new(true));
    let frames_written = Arc::new(AtomicU64::new(0));

    // --- Audio engine + writer ---
    let mut audio_engine = None;
    if audio_enabled {
        let (pcm_tx, pcm_rx) = crossbeam_channel::unbounded::<Vec<u8>>();
        let engine = audio::start(config.audio_plan.clone(), pcm_tx)?;
        inner.status.lock().unwrap().audio_warnings = engine.warnings.clone();

        // Accept FFmpeg's connection to our audio socket (non-blocking w/ stop).
        if let Some(listener) = audio_listener {
            listener
                .set_nonblocking(true)
                .map_err(|e| EncoderError::Pipeline(format!("audio socket nonblock: {e}")))?;
            let alive = session_alive.clone();
            let stop_flag = inner.clone();
            std::thread::spawn(move || {
                let deadline = Instant::now() + Duration::from_secs(10);
                loop {
                    if stop_flag.stop.load(Ordering::SeqCst) || Instant::now() > deadline {
                        return;
                    }
                    match listener.accept() {
                        Ok((mut stream, _)) => {
                            let _ = stream.set_nodelay(true);
                            while alive.load(Ordering::SeqCst) {
                                match pcm_rx.recv_timeout(Duration::from_millis(500)) {
                                    Ok(bytes) => {
                                        if stream.write_all(&bytes).is_err() {
                                            alive.store(false, Ordering::SeqCst);
                                            return;
                                        }
                                    }
                                    Err(crossbeam_channel::RecvTimeoutError::Timeout) => {}
                                    Err(_) => return,
                                }
                            }
                            return;
                        }
                        Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                            std::thread::sleep(Duration::from_millis(50));
                        }
                        Err(_) => return,
                    }
                }
            });
        }
        audio_engine = Some(engine);
    }

    // --- Video writer: capture frames -> ffmpeg stdin ---
    let writer_alive = session_alive.clone();
    let writer_frames = frames_written.clone();
    let writer = std::thread::Builder::new()
        .name("video-writer".into())
        .spawn(move || {
            while writer_alive.load(Ordering::SeqCst) {
                match frame_rx.recv_timeout(Duration::from_millis(500)) {
                    Ok(frame) => {
                        if stdin.write_all(&frame.data).is_err() {
                            writer_alive.store(false, Ordering::SeqCst);
                            break;
                        }
                        writer_frames.fetch_add(1, Ordering::Relaxed);
                    }
                    Err(crossbeam_channel::RecvTimeoutError::Timeout) => {}
                    Err(_) => break,
                }
            }
        })
        .ok();

    // Surface "streaming" in the UI immediately, but do NOT yet credit this
    // session as a healthy bring-up. `StreamEvent::Up` resets the reconnect
    // backoff counter, so emitting it the instant FFmpeg *spawns* (rather than
    // once it has actually stayed up) defeats the exponential backoff entirely:
    // an SRT peer that connects then drops after ~1-2s -- e.g. the matrix
    // listener briefly re-listening -- would reset the counter every cycle and
    // hammer reconnects forever at ~2s intervals instead of backing off. We only
    // count the session as healthy after it survives `STABLE_UPTIME`.
    set_state(inner, StreamState::Streaming);
    const STABLE_UPTIME: Duration = Duration::from_secs(5);
    let mut credited_up = false;

    // --- Monitor loop ---
    let started = Instant::now();
    let end = loop {
        if inner.stop.load(Ordering::SeqCst) {
            break SessionEnd::UserStop;
        }
        if inner.restart.load(Ordering::SeqCst) {
            break SessionEnd::Restart;
        }
        if !credited_up && started.elapsed() >= STABLE_UPTIME {
            // Stayed up long enough to be a real connection: reset backoff.
            machine.apply(StreamEvent::Up);
            credited_up = true;
        }
        if !session_alive.load(Ordering::SeqCst) {
            set_error(inner, Some("stream writer disconnected".into()));
            break SessionEnd::Ended;
        }
        match child.try_wait() {
            Ok(Some(exit)) => {
                set_error(inner, Some(format!("ffmpeg exited: {exit}")));
                break SessionEnd::Ended;
            }
            Ok(None) => {}
            Err(e) => {
                set_error(inner, Some(format!("ffmpeg wait error: {e}")));
                break SessionEnd::Ended;
            }
        }

        // Telemetry: approximate dropped frames from expected vs written.
        let elapsed = started.elapsed().as_secs_f64();
        let expected = (elapsed * config.fps as f64) as u64;
        let written = frames_written.load(Ordering::Relaxed);
        let dropped = expected.saturating_sub(written);
        inner.status.lock().unwrap().dropped_frames = dropped;

        std::thread::sleep(Duration::from_millis(200));
    };

    // --- Teardown (ordered, no orphans) ---
    session_alive.store(false, Ordering::SeqCst);
    let _ = child.kill();
    let _ = child.wait();
    capture_session.stop();
    if let Some(mut engine) = audio_engine.take() {
        engine.stop();
    }
    if let Some(writer) = writer {
        let _ = writer.join();
    }

    Ok(end)
}
