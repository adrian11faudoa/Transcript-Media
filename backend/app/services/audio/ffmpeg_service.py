"""
Audio extraction and enhancement pipeline using FFmpeg + librosa.

Pipeline:
  1. Probe video metadata (duration, codecs, streams)
  2. Extract audio track → 16kHz mono WAV (Whisper format)
  3. Apply noise reduction (optional, via noisereduce)
  4. Detect speech segments (VAD via silero or webrtcvad)
  5. Return cleaned audio path + metadata
"""
import asyncio
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class VideoMetadata:
    """Metadata extracted from a video file via FFprobe."""
    duration_seconds: float
    video_codec: Optional[str]
    audio_codec: Optional[str]
    resolution: Optional[str]
    fps: Optional[float]
    sample_rate: Optional[int]
    channels: Optional[int]
    bitrate: Optional[int]
    has_audio: bool
    has_video: bool
    format_name: str
    raw_streams: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class AudioProcessingResult:
    """Result from the audio extraction + enhancement pipeline."""
    audio_path: str          # Path to the final WAV file
    duration_seconds: float
    sample_rate: int
    channels: int
    speech_segments: List[Tuple[float, float]]  # [(start, end), ...] in seconds
    has_speech: bool
    noise_reduced: bool


class FFmpegAudioService:
    """
    Wraps FFmpeg for production-grade audio extraction.
    
    Key design decisions:
    - Always output 16kHz mono WAV — Whisper's native format
    - Use async subprocess to avoid blocking the event loop
    - Segment detection via energy-based VAD before sending to Whisper
    - Chunked processing for very long videos (>2h) to avoid memory pressure
    """

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        sample_rate: int = 16000,
        channels: int = 1,
    ):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self.sample_rate = sample_rate
        self.channels = channels

    async def probe_video(self, video_path: str) -> VideoMetadata:
        """
        Extract comprehensive metadata from a video file using FFprobe.
        """
        cmd = [
            self.ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            video_path,
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise ValueError(f"FFprobe failed: {stderr.decode()}")
        
        data = json.loads(stdout.decode())
        fmt = data.get("format", {})
        streams = data.get("streams", [])
        
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
        
        # Parse FPS (stored as fraction string like "24000/1001")
        fps = None
        if video_stream:
            fps_str = video_stream.get("r_frame_rate", "0/1")
            try:
                num, den = fps_str.split("/")
                fps = float(num) / float(den) if float(den) != 0 else None
            except (ValueError, ZeroDivisionError):
                fps = None

        resolution = None
        if video_stream:
            w = video_stream.get("width")
            h = video_stream.get("height")
            if w and h:
                resolution = f"{w}x{h}"

        return VideoMetadata(
            duration_seconds=float(fmt.get("duration", 0)),
            video_codec=video_stream.get("codec_name") if video_stream else None,
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
            resolution=resolution,
            fps=fps,
            sample_rate=int(audio_stream.get("sample_rate", 0)) if audio_stream else None,
            channels=int(audio_stream.get("channels", 0)) if audio_stream else None,
            bitrate=int(fmt.get("bit_rate", 0)) if fmt.get("bit_rate") else None,
            has_audio=audio_stream is not None,
            has_video=video_stream is not None,
            format_name=fmt.get("format_name", ""),
            raw_streams=streams,
        )

    async def extract_audio(
        self,
        video_path: str,
        output_dir: str,
        normalize_audio: bool = True,
        noise_reduce: bool = False,
        progress_callback=None,
    ) -> AudioProcessingResult:
        """
        Extract and preprocess audio from video.
        
        FFmpeg filter chain:
          - aresample: resample to 16kHz
          - aformat: convert to mono f32le
          - loudnorm (optional): EBU R128 loudness normalization
          - afftdn (optional): FFT-based noise reduction
        """
        metadata = await self.probe_video(video_path)
        
        if not metadata.has_audio:
            raise ValueError("Video file has no audio stream.")
        
        audio_filename = f"audio_{Path(video_path).stem}.wav"
        audio_path = os.path.join(output_dir, audio_filename)
        
        # Build FFmpeg filter chain
        filters = [
            f"aresample={self.sample_rate}",
            "aformat=sample_fmts=s16:channel_layouts=mono",
        ]
        
        if normalize_audio:
            # EBU R128 loudness normalization — improves Whisper accuracy
            filters.append("loudnorm=I=-23:LRA=7:TP=-2")
        
        if noise_reduce:
            # FFT-based denoiser — helps with background noise
            filters.append("afftdn=nf=-25")
        
        filter_chain = ",".join(filters)
        
        cmd = [
            self.ffmpeg,
            "-y",                    # Overwrite output
            "-i", video_path,
            "-vn",                   # No video
            "-af", filter_chain,
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            "-f", "wav",
            "-threads", "0",         # Auto thread count
            audio_path,
        ]
        
        logger.info(f"Extracting audio: {' '.join(cmd)}")
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        # Stream stderr to parse progress
        stderr_output = []
        async for line in self._read_stderr_lines(proc):
            stderr_output.append(line)
            if progress_callback and "time=" in line:
                current_time = self._parse_ffmpeg_time(line, metadata.duration_seconds)
                if current_time is not None:
                    pct = min(current_time / metadata.duration_seconds * 100, 99)
                    await progress_callback(pct)
        
        await proc.wait()
        
        if proc.returncode != 0:
            stderr_text = "\n".join(stderr_output[-20:])
            raise RuntimeError(f"FFmpeg audio extraction failed:\n{stderr_text}")
        
        if not os.path.exists(audio_path):
            raise RuntimeError("Audio file was not created by FFmpeg.")
        
        file_size = os.path.getsize(audio_path)
        logger.info(f"Audio extracted: {audio_path} ({file_size / 1024 / 1024:.1f} MB)")
        
        # Detect speech segments using energy-based VAD
        speech_segments = await self._detect_speech_segments(audio_path)
        
        return AudioProcessingResult(
            audio_path=audio_path,
            duration_seconds=metadata.duration_seconds,
            sample_rate=self.sample_rate,
            channels=self.channels,
            speech_segments=speech_segments,
            has_speech=len(speech_segments) > 0,
            noise_reduced=noise_reduce,
        )

    async def extract_audio_chunk(
        self,
        video_path: str,
        output_path: str,
        start_time: float,
        end_time: float,
    ) -> str:
        """
        Extract a specific time range from video audio.
        Used for chunked processing of very long videos.
        """
        duration = end_time - start_time
        
        cmd = [
            self.ffmpeg, "-y",
            "-ss", str(start_time),
            "-t", str(duration),
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            output_path,
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            raise RuntimeError(f"Chunk extraction failed: {stderr.decode()}")
        
        return output_path

    async def _detect_speech_segments(
        self,
        audio_path: str,
        min_speech_duration: float = 0.5,
        energy_threshold_db: float = -40.0,
    ) -> List[Tuple[float, float]]:
        """
        Energy-based speech activity detection.
        
        Splits audio into 100ms frames, computes RMS energy,
        and identifies contiguous speech regions.
        
        For production, replace with Silero VAD or pyannote VAD
        for much better accuracy, especially for quiet speech.
        """
        try:
            import librosa
            import soundfile as sf
        except ImportError:
            logger.warning("librosa not available, skipping VAD")
            return []
        
        try:
            audio, sr = librosa.load(audio_path, sr=self.sample_rate, mono=True)
            
            # Frame-level energy analysis
            frame_length = int(0.1 * sr)  # 100ms frames
            hop_length = frame_length // 2  # 50ms hop
            
            rms = librosa.feature.rms(
                y=audio,
                frame_length=frame_length,
                hop_length=hop_length,
            )[0]
            
            # Convert threshold from dB to linear
            threshold = librosa.db_to_amplitude(energy_threshold_db)
            is_speech = rms > threshold
            
            # Convert frame indices to time
            times = librosa.frames_to_time(
                np.arange(len(is_speech)),
                sr=sr,
                hop_length=hop_length,
            )
            
            # Find contiguous speech segments
            segments = []
            in_speech = False
            seg_start = 0.0
            
            for i, (t, speech) in enumerate(zip(times, is_speech)):
                if speech and not in_speech:
                    seg_start = t
                    in_speech = True
                elif not speech and in_speech:
                    duration = t - seg_start
                    if duration >= min_speech_duration:
                        segments.append((seg_start, t))
                    in_speech = False
            
            # Don't forget the last segment
            if in_speech:
                duration = times[-1] - seg_start
                if duration >= min_speech_duration:
                    segments.append((seg_start, times[-1]))
            
            logger.info(f"Detected {len(segments)} speech segments")
            return segments
            
        except Exception as e:
            logger.warning(f"VAD failed: {e}. Processing full audio.")
            return []

    async def _read_stderr_lines(self, proc):
        """Async generator to read FFmpeg stderr line by line."""
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            yield line.decode("utf-8", errors="replace").strip()

    def _parse_ffmpeg_time(self, line: str, total_duration: float) -> Optional[float]:
        """Parse 'time=HH:MM:SS.ms' from FFmpeg progress output."""
        try:
            if "time=" not in line:
                return None
            time_part = line.split("time=")[1].split()[0]
            if time_part == "N/A":
                return None
            parts = time_part.split(":")
            hours, minutes, seconds = float(parts[0]), float(parts[1]), float(parts[2])
            return hours * 3600 + minutes * 60 + seconds
        except (IndexError, ValueError):
            return None

    async def get_video_thumbnail(
        self,
        video_path: str,
        output_path: str,
        timestamp: float = 5.0,
        width: int = 1280,
    ) -> str:
        """Extract a thumbnail frame from the video."""
        cmd = [
            self.ffmpeg, "-y",
            "-ss", str(timestamp),
            "-i", video_path,
            "-vframes", "1",
            "-vf", f"scale={width}:-1",
            "-q:v", "2",
            output_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return output_path
