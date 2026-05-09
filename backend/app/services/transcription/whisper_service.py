"""
AI Transcription Service using WhisperX.

WhisperX advantages over vanilla Whisper:
  - Forced phoneme alignment → word-level timestamps
  - Speaker diarization via pyannote.audio
  - Batched inference → 70x faster than real-time
  - Better VAD (silero) to reduce hallucinations

Pipeline:
  1. Load WhisperX model (cached after first load)
  2. Transcribe audio in batches
  3. Force-align to get word-level timestamps
  4. Run speaker diarization
  5. Assign speakers to each word/segment
"""
import gc
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Awaitable

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class WordResult:
    word: str
    start: float
    end: float
    score: float
    speaker: Optional[str] = None


@dataclass
class SegmentResult:
    text: str
    start: float
    end: float
    speaker: Optional[str]
    words: List[WordResult]
    avg_logprob: float
    no_speech_prob: float
    confidence: float


@dataclass
class TranscriptionResult:
    segments: List[SegmentResult]
    language: str
    language_probability: float
    duration: float
    word_count: int


class WhisperXService:
    """
    Production WhisperX transcription engine.
    
    Model loading is lazy and cached — the first call loads the model
    into GPU/CPU memory, subsequent calls reuse it. This prevents
    re-loading the 1.5GB model for each job.
    """
    
    _model_cache: Dict[str, Any] = {}
    _align_model_cache: Dict[str, Any] = {}
    
    def __init__(
        self,
        model_name: str = "large-v3",
        device: str = "cpu",
        compute_type: str = "int8",
        batch_size: int = 16,
        hf_token: Optional[str] = None,
    ):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.batch_size = batch_size
        self.hf_token = hf_token

    def _get_model(self):
        """Lazy-load and cache the Whisper model."""
        cache_key = f"{self.model_name}:{self.device}:{self.compute_type}"
        if cache_key not in self.__class__._model_cache:
            logger.info(f"Loading WhisperX model: {self.model_name} on {self.device} ({self.compute_type})")
            try:
                import whisperx
                model = whisperx.load_model(
                    self.model_name,
                    self.device,
                    compute_type=self.compute_type,
                    language=None,  # Auto-detect
                )
                self.__class__._model_cache[cache_key] = model
                logger.info(f"WhisperX model loaded successfully.")
            except ImportError:
                logger.warning("WhisperX not installed, falling back to openai-whisper")
                return self._get_fallback_model(cache_key)
        return self.__class__._model_cache[cache_key]

    def _get_fallback_model(self, cache_key: str):
        """Fallback to vanilla Whisper if WhisperX is not installed."""
        import whisper
        model = whisper.load_model(self.model_name, device=self.device)
        self.__class__._model_cache[cache_key] = ("whisper", model)
        return ("whisper", model)

    def _get_align_model(self, language: str):
        """Lazy-load forced alignment model for a specific language."""
        if language not in self.__class__._align_model_cache:
            try:
                import whisperx
                align_model, metadata = whisperx.load_align_model(
                    language_code=language,
                    device=self.device,
                )
                self.__class__._align_model_cache[language] = (align_model, metadata)
            except Exception as e:
                logger.warning(f"Could not load align model for {language}: {e}")
                return None, None
        return self.__class__._align_model_cache[language]

    async def transcribe(
        self,
        audio_path: str,
        language: Optional[str] = None,
        enable_diarization: bool = True,
        min_speakers: int = 1,
        max_speakers: int = 10,
        progress_callback: Optional[Callable] = None,
    ) -> TranscriptionResult:
        """
        Full transcription pipeline:
          transcribe → align → diarize → assign speakers
        
        All heavy computation runs synchronously in this method,
        intended to be called from a Celery worker thread.
        """
        import soundfile as sf
        
        logger.info(f"Starting transcription: {audio_path}")
        start_ts = time.time()
        
        # Load audio as numpy array
        audio_data, sample_rate = sf.read(audio_path, dtype="float32")
        if audio_data.ndim > 1:
            audio_data = audio_data.mean(axis=1)  # Force mono
        
        duration = len(audio_data) / sample_rate
        logger.info(f"Audio duration: {duration:.1f}s")
        
        # Step 1: Transcribe with Whisper
        if progress_callback:
            await progress_callback(5, "Running AI transcription...")
        
        raw_result = self._run_whisper(audio_data, language)
        detected_language = raw_result.get("language", language or "en")
        lang_prob = raw_result.get("language_probability", 1.0)
        
        logger.info(f"Detected language: {detected_language} ({lang_prob:.2%})")
        
        # Step 2: Forced alignment for word-level timestamps
        if progress_callback:
            await progress_callback(50, "Aligning word timestamps...")
        
        aligned_result = self._run_alignment(raw_result, audio_data, detected_language)
        
        # Step 3: Speaker diarization
        if enable_diarization and self.hf_token:
            if progress_callback:
                await progress_callback(70, "Performing speaker diarization...")
            aligned_result = self._run_diarization(
                aligned_result, audio_path, 
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
        
        # Step 4: Build structured result
        if progress_callback:
            await progress_callback(90, "Building transcript...")
        
        segments = self._build_segments(aligned_result)
        
        elapsed = time.time() - start_ts
        rtf = elapsed / duration  # Real-time factor
        logger.info(f"Transcription complete in {elapsed:.1f}s (RTF: {rtf:.2f}x)")
        
        word_count = sum(len(seg.words) for seg in segments)
        
        return TranscriptionResult(
            segments=segments,
            language=detected_language,
            language_probability=lang_prob,
            duration=duration,
            word_count=word_count,
        )

    def _run_whisper(self, audio: np.ndarray, language: Optional[str]) -> Dict[str, Any]:
        """Run Whisper transcription. Handles both WhisperX and vanilla Whisper."""
        model = self._get_model()
        
        if isinstance(model, tuple) and model[0] == "whisper":
            # Vanilla Whisper fallback
            _, whisper_model = model
            result = whisper_model.transcribe(
                audio,
                language=language,
                word_timestamps=True,
                verbose=False,
            )
            return result
        else:
            # WhisperX
            result = model.transcribe(
                audio,
                batch_size=self.batch_size,
                language=language,
                print_progress=False,
            )
            return result

    def _run_alignment(
        self, 
        result: Dict[str, Any],
        audio: np.ndarray,
        language: str,
    ) -> Dict[str, Any]:
        """Force-align Whisper segments for precise word timestamps."""
        try:
            import whisperx
            align_model, metadata = self._get_align_model(language)
            
            if align_model is None:
                logger.warning(f"Alignment skipped: no model for {language}")
                return result
            
            aligned = whisperx.align(
                result["segments"],
                align_model,
                metadata,
                audio,
                self.device,
                return_char_alignments=False,
            )
            return aligned
        except Exception as e:
            logger.warning(f"Alignment failed: {e}. Using Whisper timestamps.")
            return result

    def _run_diarization(
        self,
        result: Dict[str, Any],
        audio_path: str,
        min_speakers: int,
        max_speakers: int,
    ) -> Dict[str, Any]:
        """
        Speaker diarization using pyannote.audio via WhisperX.
        Requires a HuggingFace token with accepted pyannote terms.
        """
        try:
            import whisperx
            
            diarize_pipeline = whisperx.DiarizationPipeline(
                use_auth_token=self.hf_token,
                device=self.device,
            )
            
            diarize_segments = diarize_pipeline(
                audio_path,
                min_speakers=min_speakers,
                max_speakers=max_speakers,
            )
            
            result = whisperx.assign_word_speakers(diarize_segments, result)
            return result
        except Exception as e:
            logger.warning(f"Diarization failed: {e}. Proceeding without speakers.")
            return result

    def _build_segments(self, result: Dict[str, Any]) -> List[SegmentResult]:
        """Convert WhisperX output into typed SegmentResult objects."""
        segments = []
        
        for i, seg in enumerate(result.get("segments", [])):
            words = []
            for w in seg.get("words", []):
                if not w.get("word", "").strip():
                    continue
                words.append(WordResult(
                    word=w["word"],
                    start=float(w.get("start", seg["start"])),
                    end=float(w.get("end", seg["end"])),
                    score=float(w.get("score", 0.9)),
                    speaker=w.get("speaker"),
                ))
            
            # Infer segment speaker from majority vote of words
            speaker = seg.get("speaker")
            if not speaker and words:
                speaker_votes: Dict[str, int] = {}
                for w in words:
                    if w.speaker:
                        speaker_votes[w.speaker] = speaker_votes.get(w.speaker, 0) + 1
                if speaker_votes:
                    speaker = max(speaker_votes, key=speaker_votes.get)
            
            avg_logprob = float(seg.get("avg_logprob", -0.3))
            no_speech_prob = float(seg.get("no_speech_prob", 0.1))
            
            # Convert log-prob to a 0-1 confidence score
            confidence = max(0.0, min(1.0, 1.0 + avg_logprob / 3.0))
            
            segments.append(SegmentResult(
                text=seg["text"].strip(),
                start=float(seg["start"]),
                end=float(seg["end"]),
                speaker=speaker,
                words=words,
                avg_logprob=avg_logprob,
                no_speech_prob=no_speech_prob,
                confidence=confidence,
            ))
        
        return segments


class FallbackTranscriptionService:
    """
    Simple fallback using openai-whisper when WhisperX is unavailable.
    Lower timestamp accuracy, no diarization, but no extra dependencies.
    """
    
    def __init__(self, model_name: str = "base", device: str = "cpu"):
        self.model_name = model_name
        self.device = device
        self._model = None
    
    def _get_model(self):
        if self._model is None:
            import whisper
            self._model = whisper.load_model(self.model_name, device=self.device)
        return self._model
    
    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        model = self._get_model()
        result = model.transcribe(
            audio_path,
            word_timestamps=True,
            verbose=False,
        )
        
        segments = []
        for i, seg in enumerate(result.get("segments", [])):
            words = [
                WordResult(
                    word=w["word"],
                    start=float(w["start"]),
                    end=float(w["end"]),
                    score=float(w.get("probability", 0.9)),
                )
                for w in seg.get("words", [])
            ]
            segments.append(SegmentResult(
                text=seg["text"].strip(),
                start=float(seg["start"]),
                end=float(seg["end"]),
                speaker=None,
                words=words,
                avg_logprob=float(seg.get("avg_logprob", -0.3)),
                no_speech_prob=float(seg.get("no_speech_prob", 0.1)),
                confidence=0.9,
            ))
        
        duration = float(result.get("segments", [{}])[-1].get("end", 0)) if result.get("segments") else 0
        
        return TranscriptionResult(
            segments=segments,
            language=result.get("language", "en"),
            language_probability=1.0,
            duration=duration,
            word_count=sum(len(s.words) for s in segments),
        )
