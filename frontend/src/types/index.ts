// Core domain types — matches backend Pydantic schemas

export type JobStatus =
  | 'pending'
  | 'uploading'
  | 'queued'
  | 'extracting_audio'
  | 'enhancing_audio'
  | 'transcribing'
  | 'diarizing'
  | 'generating_transcript'
  | 'completed'
  | 'failed'
  | 'cancelled'

export type ExportFormat = 'docx' | 'pdf' | 'txt' | 'srt' | 'vtt' | 'json'
export type MediaType = 'movie' | 'tv_series' | 'interview' | 'podcast' | 'clip' | 'other'

export interface Job {
  id: string
  original_filename: string
  file_size_bytes: number
  duration_seconds: number | null
  status: JobStatus
  progress_percent: number
  current_stage: string | null
  error_message: string | null
  media_type: MediaType
  language: string
  created_at: string
  completed_at: string | null
  whisper_model?: string
  enable_diarization?: boolean
  video_codec?: string | null
  audio_codec?: string | null
  resolution?: string | null
  fps?: number | null
}

export interface WordTimestamp {
  word: string
  start: number
  end: number
  score?: number
}

export interface TranscriptSegment {
  id: string
  start_time: number
  end_time: number
  text: string
  speaker_id: string | null
  speaker_name: string | null
  confidence: number | null
  words: WordTimestamp[] | null
  segment_index: number
}

export interface Transcript {
  id: string
  job_id: string
  detected_language: string | null
  language_confidence: number | null
  full_text: string
  word_count: number
  speaker_count: number
  duration_seconds: number | null
  summary: string | null
  keywords: string[] | null
  sentiment: string | null
  sentiment_score: number | null
  segments: TranscriptSegment[]
  created_at: string
  updated_at: string
}

export interface ProgressEvent {
  job_id: string
  status: JobStatus
  progress: number
  stage: string
  message: string
  timestamp: string
}

export interface UploadInitRequest {
  filename: string
  file_size: number
  media_type: MediaType
  language: string
  enable_diarization: boolean
  enable_translation: boolean
  target_language?: string
  whisper_model?: string
}

export interface UploadInitResponse {
  job_id: string
  upload_url: string
  chunk_size: number
  total_chunks: number
}

export interface ExportRequest {
  format: ExportFormat
  include_speakers: boolean
  include_timestamps: boolean
  include_confidence: boolean
}

export interface SearchResult {
  segment_id: string
  job_id: string
  start_time: number
  end_time: number
  text: string
  speaker_name: string | null
  highlight: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  pages: number
}

// ─── UI State ─────────────────────────────────────────────────────────────────

export interface UploadState {
  file: File | null
  jobId: string | null
  progress: number
  stage: string
  status: JobStatus | null
  error: string | null
}

export interface SpeakerColors {
  [speakerId: string]: string
}

export const SPEAKER_COLORS = [
  '#3B82F6', // blue
  '#10B981', // emerald
  '#F59E0B', // amber
  '#EF4444', // red
  '#8B5CF6', // violet
  '#EC4899', // pink
  '#06B6D4', // cyan
  '#F97316', // orange
]

export function getSpeakerColor(speakerId: string, allSpeakers: string[]): string {
  const idx = allSpeakers.indexOf(speakerId)
  return SPEAKER_COLORS[idx % SPEAKER_COLORS.length] ?? '#6B7280'
}

export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

export function formatTimestamp(seconds: number): string {
  return `[${formatDuration(seconds)}]`
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

export const STATUS_LABELS: Record<JobStatus, string> = {
  pending: 'Pending',
  uploading: 'Uploading',
  queued: 'Queued',
  extracting_audio: 'Extracting Audio',
  enhancing_audio: 'Enhancing Audio',
  transcribing: 'Transcribing',
  diarizing: 'Identifying Speakers',
  generating_transcript: 'Generating Transcript',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export const TERMINAL_STATUSES: JobStatus[] = ['completed', 'failed', 'cancelled']
export const ACTIVE_STATUSES: JobStatus[] = [
  'queued', 'extracting_audio', 'enhancing_audio', 
  'transcribing', 'diarizing', 'generating_transcript'
]
