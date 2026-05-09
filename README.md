# TranscriptAI — Production AI Video Transcription Platform

A full-stack, production-grade AI transcription system built with FastAPI, Next.js, WhisperX, and Celery. Designed to handle movies, TV series, podcasts, interviews, and short clips with high accuracy and real-time progress tracking.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                   │
│  Next.js 15 + React 19 + TypeScript + TailwindCSS                          │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────────────────┐   │
│  │  Dashboard   │  │ Upload Page  │  │     Transcript Editor            │   │
│  │  Job List    │  │ Drag+Drop    │  │  Segments · Search · Export      │   │
│  └──────────────┘  └──────────────┘  └─────────────────────────────────┘   │
│         │                │                          │                        │
│   Fetch/SSE        Chunked Upload              PATCH/Export                  │
└──────────┼────────────────┼──────────────────────────┼────────────────────┘
           │                │                          │
┌──────────▼────────────────▼──────────────────────────▼────────────────────┐
│                          FASTAPI BACKEND (Python)                           │
│  ┌─────────────┐  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐   │
│  │  /upload    │  │  /jobs      │  │  /transcripts │  │  /export       │   │
│  │  init       │  │  list       │  │  GET · PATCH  │  │  docx/pdf/     │   │
│  │  chunk      │  │  SSE stream │  │  search       │  │  srt/vtt/txt   │   │
│  │  complete   │  │  delete     │  │  segments     │  │  json          │   │
│  └─────────────┘  └─────────────┘  └──────────────┘  └────────────────┘   │
│                         │                                                    │
│                  Redis pub/sub ◄──────────── SSE progress stream            │
└─────────────────────────┼───────────────────────────────────────────────────┘
                          │ Celery task dispatch
┌─────────────────────────▼───────────────────────────────────────────────────┐
│                        WORKER LAYER (Celery)                                 │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                   process_video_job (orchestrator)                   │    │
│  │                                                                      │    │
│  │  1. FFprobe → extract video metadata                                 │    │
│  │  2. FFmpeg  → extract 16kHz mono WAV                                 │    │
│  │  3. librosa → energy-based VAD (speech detection)                   │    │
│  │  4. WhisperX → batch transcription (70x realtime on GPU)            │    │
│  │  5. Forced alignment → word-level timestamps                         │    │
│  │  6. pyannote → speaker diarization (SPEAKER_00, SPEAKER_01, ...)    │    │
│  │  7. Speaker assignment → merge diarization + transcript             │    │
│  │  8. PostgreSQL → persist segments + transcript                      │    │
│  │  9. Keyword extraction + enrichments                                 │    │
│  │  10. Redis publish → SSE progress events                            │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  Queues: transcription | processing | ai | cleanup                          │
└──────────────────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────────────────┐
│                       INFRASTRUCTURE                                         │
│  PostgreSQL 16   Redis 7   Local/S3 Storage   Celery Flower (monitoring)    │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
transcriptai/
├── backend/
│   ├── app/
│   │   ├── main.py                      # FastAPI app factory + lifespan
│   │   ├── core/
│   │   │   ├── config.py               # Pydantic Settings (all env vars)
│   │   │   ├── database.py             # Async SQLAlchemy engine + session
│   │   │   └── redis.py                # aioredis connection pool
│   │   ├── models/
│   │   │   └── models.py               # SQLAlchemy ORM: Job, Transcript, Segment, Export
│   │   ├── schemas/
│   │   │   └── schemas.py              # Pydantic v2 request/response types
│   │   ├── api/routes/
│   │   │   ├── upload.py               # Chunked upload + init/complete
│   │   │   ├── jobs.py                 # Job CRUD + SSE progress stream
│   │   │   ├── transcripts.py          # Transcript view/edit/search/export
│   │   │   ├── export.py               # Export format catalog
│   │   │   └── health.py               # Liveness + readiness probes
│   │   ├── services/
│   │   │   ├── audio/
│   │   │   │   └── ffmpeg_service.py   # FFmpeg extraction + VAD + thumbnail
│   │   │   ├── transcription/
│   │   │   │   └── whisper_service.py  # WhisperX + alignment + diarization
│   │   │   └── export/
│   │   │       └── export_service.py   # TXT/SRT/VTT/DOCX/PDF/JSON exporters
│   │   └── workers/
│   │       ├── celery_app.py           # Celery configuration + routing
│   │       └── tasks.py                # Pipeline tasks + progress publisher
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
│
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx              # Root layout + metadata
│   │   │   ├── globals.css             # Design tokens + animations
│   │   │   ├── page.tsx                # Root → redirect to /dashboard
│   │   │   ├── dashboard/page.tsx      # Job list + stats
│   │   │   ├── upload/page.tsx         # Upload + settings + progress
│   │   │   └── transcript/[jobId]/
│   │   │       └── page.tsx            # Transcript editor + search + export
│   │   ├── lib/
│   │   │   └── api.ts                  # Type-safe API client + upload orchestrator
│   │   └── types/
│   │       └── index.ts                # Domain types + utilities
│   ├── next.config.ts
│   ├── tailwind.config.ts
│   ├── tsconfig.json
│   ├── package.json
│   └── Dockerfile
│
└── docker-compose.yml                  # Full local dev stack
```

---

## Processing Pipeline (Detailed)

### Stage 1: Upload (0–5%)
- Client initializes session → receives `job_id` and chunk parameters
- File split into 10MB chunks, uploaded sequentially via XHR
- Server streams chunks to staging directory
- On complete: chunks assembled → job queued in Celery

### Stage 2: Audio Extraction (5–35%)
- FFprobe extracts video metadata (duration, codecs, resolution, FPS)
- FFmpeg extracts audio track with filter chain:
  - `aresample=16000` → Whisper's native sample rate
  - `aformat=sample_fmts=s16:channel_layouts=mono` → mono int16
  - `loudnorm=I=-23:LRA=7:TP=-2` → EBU R128 normalization
  - Optional: `afftdn=nf=-25` → FFT noise reduction
- librosa VAD detects speech-active regions (100ms frames)

### Stage 3: AI Transcription (35–70%)
- WhisperX loads `large-v3` model (cached in memory after first load)
- Batched inference (batch_size=16) — 70x faster than real-time on GPU
- Language auto-detection with confidence score
- Forced phoneme alignment for word-level timestamps

### Stage 4: Speaker Diarization (70–85%)
- pyannote.audio pipeline via WhisperX integration
- Identifies speaker segments: SPEAKER_00, SPEAKER_01, …
- Word-level speaker assignment via majority vote

### Stage 5: Transcript Generation (85–100%)
- Segments persisted to PostgreSQL (with word timestamps as JSONB)
- Keyword extraction (frequency-based; swap for KeyBERT in production)
- Full-text denormalized for fast search
- Progress event published → SSE streams to client

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
- (Optional) NVIDIA GPU + nvidia-docker for GPU acceleration

```bash
# 1. Clone
git clone https://github.com/yourorg/transcriptai.git
cd transcriptai

# 2. Configure environment
cp backend/.env.example backend/.env
# Edit backend/.env — set HF_TOKEN for speaker diarization

# 3. Start all services
docker-compose up -d

# 4. Open the app
open http://localhost:3000
```

### GPU Acceleration (Recommended)
```bash
# In backend/.env:
WHISPER_DEVICE=cuda
WHISPER_MODEL=large-v3
WHISPER_COMPUTE_TYPE=float16

# Ensure nvidia-docker is installed:
docker-compose up -d worker-transcription
```

---

## API Reference

### Upload Flow
```
POST /api/v1/upload/init          → { job_id, upload_url, chunk_size, total_chunks }
POST /api/v1/upload/chunk/{id}    → { received_chunks, complete }
POST /api/v1/upload/complete/{id} → { job_id, status: "queued" }
DELETE /api/v1/upload/cancel/{id} → cancel upload
```

### Jobs
```
GET    /api/v1/jobs               → paginated job list
GET    /api/v1/jobs/{id}          → job detail
GET    /api/v1/jobs/{id}/progress → SSE stream (text/event-stream)
DELETE /api/v1/jobs/{id}          → delete job + files
```

### Transcripts
```
GET    /api/v1/transcripts/{id}                    → full transcript + segments
PATCH  /api/v1/transcripts/{id}/segments/{seg_id}  → edit segment
GET    /api/v1/transcripts/{id}/search?q=query     → search results
POST   /api/v1/transcripts/{id}/export             → download file
```

---

## Scaling for Production

### Horizontal Scaling
```yaml
# Scale AI workers independently
docker-compose up --scale worker-transcription=4

# Add GPU workers on separate nodes
WHISPER_DEVICE=cuda docker-compose up worker-transcription
```

### Model Selection by Use Case
| Model     | Speed      | Accuracy | VRAM  | Best For              |
|-----------|------------|----------|-------|-----------------------|
| `tiny`    | 32x RT     | 73%      | 1 GB  | Dev / quick preview   |
| `base`    | 16x RT     | 82%      | 1 GB  | Short clips           |
| `small`   | 6x RT      | 91%      | 2 GB  | Podcasts / interviews |
| `medium`  | 2x RT      | 95%      | 5 GB  | TV series / movies    |
| `large-v3`| 1x RT      | 99%      | 10 GB | Production / movies   |

### Database Indexes
Key indexes are already defined on:
- `transcription_jobs(status, created_at)` — job list queries
- `transcript_segments(transcript_id, start_time)` — timeline queries
- `transcript_exports(job_id, format)` — export cache lookups

---

## Advanced Features (Extension Points)

### Translation
Extend `WhisperXService.transcribe()` with a translation model:
```python
# Using Helsinki-NLP/opus-mt via HuggingFace
from transformers import pipeline
translator = pipeline("translation", model=f"Helsinki-NLP/opus-mt-{src}-{tgt}")
```

### Summarization
Replace `_run_ai_enrichments()` with a local summarization model:
```python
from transformers import pipeline
summarizer = pipeline("summarization", model="facebook/bart-large-cnn")
summary = summarizer(full_text[:4096], max_length=150, min_length=30)
```

### Real Keyword Extraction (KeyBERT)
```python
from keybert import KeyBERT
kw_model = KeyBERT()
keywords = kw_model.extract_keywords(text, top_n=15)
```

### Batch Processing
Queue multiple jobs at once:
```python
# In tasks.py
from celery import group
batch = group(process_video_job.s(str(job_id)) for job_id in job_ids)
result = batch.apply_async()
```

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_MODEL` | `large-v3` | Model size (tiny/base/small/medium/large-v3) |
| `WHISPER_DEVICE` | `auto` | auto/cpu/cuda |
| `WHISPER_COMPUTE_TYPE` | `float16` | float32/float16/int8 |
| `HF_TOKEN` | — | HuggingFace token for pyannote diarization |
| `MAX_UPLOAD_SIZE_MB` | `5000` | Max video file size in MB |
| `ENABLE_DIARIZATION` | `true` | Enable speaker detection |
| `MAX_CONCURRENT_JOBS` | `4` | Parallel transcription jobs |
| `JOB_TIMEOUT_SECONDS` | `7200` | Max processing time per job |
| `STORAGE_BACKEND` | `local` | local/s3/gcs |

---

## License

MIT License — see LICENSE file for details.
