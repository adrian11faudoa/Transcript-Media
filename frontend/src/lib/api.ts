/**
 * TranscriptAI API Client
 * Type-safe wrapper around the FastAPI backend.
 */

import type {
  Job,
  Transcript,
  TranscriptSegment,
  SegmentUpdateRequest,
  UploadInitRequest,
  UploadInitResponse,
  ExportRequest,
  SearchResult,
  PaginatedResponse,
  ProgressEvent,
} from '@/types'

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const API_V1 = `${BASE_URL}/api/v1`

class APIError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail)
    this.name = 'APIError'
  }
}

async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = path.startsWith('http') ? path : `${API_V1}${path}`

  const response = await fetch(url, {
    headers: {
      'Content-Type': 'application/json',
      ...options.headers,
    },
    ...options,
  })

  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const json = await response.json()
      detail = json.detail ?? detail
    } catch {}
    throw new APIError(response.status, detail)
  }

  // Handle empty body (204 etc)
  const text = await response.text()
  return text ? JSON.parse(text) : undefined
}

// ─── Upload API ────────────────────────────────────────────────────────────────

export async function initUpload(req: UploadInitRequest): Promise<UploadInitResponse> {
  return apiFetch<UploadInitResponse>('/upload/init', {
    method: 'POST',
    body: JSON.stringify(req),
  })
}

export async function uploadChunk(
  jobId: string,
  chunkIndex: number,
  totalChunks: number,
  chunk: Blob,
  onProgress?: (loaded: number, total: number) => void,
): Promise<void> {
  const formData = new FormData()
  formData.append('chunk_index', String(chunkIndex))
  formData.append('total_chunks', String(totalChunks))
  formData.append('file', chunk, `chunk_${chunkIndex}`)

  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_V1}/upload/chunk/${jobId}`)

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded, e.total)
      }
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve()
      } else {
        reject(new APIError(xhr.status, `Chunk upload failed: ${xhr.statusText}`))
      }
    }

    xhr.onerror = () => reject(new APIError(0, 'Network error during upload'))
    xhr.send(formData)
  })
}

export async function completeUpload(jobId: string): Promise<{ job_id: string; status: string }> {
  return apiFetch(`/upload/complete/${jobId}`, { method: 'POST' })
}

export async function cancelUpload(jobId: string): Promise<void> {
  return apiFetch(`/upload/cancel/${jobId}`, { method: 'DELETE' })
}

// ─── Jobs API ──────────────────────────────────────────────────────────────────

export async function listJobs(
  page = 1,
  pageSize = 20,
): Promise<PaginatedResponse<Job>> {
  return apiFetch<PaginatedResponse<Job>>(
    `/jobs?page=${page}&page_size=${pageSize}`,
  )
}

export async function getJob(jobId: string): Promise<Job> {
  return apiFetch<Job>(`/jobs/${jobId}`)
}

export async function deleteJob(jobId: string): Promise<void> {
  return apiFetch(`/jobs/${jobId}`, { method: 'DELETE' })
}

export function subscribeToJobProgress(
  jobId: string,
  onEvent: (event: ProgressEvent) => void,
  onDone?: () => void,
  onError?: (error: Error) => void,
): () => void {
  const url = `${API_V1}/jobs/${jobId}/progress`
  const eventSource = new EventSource(url)

  eventSource.onmessage = (e) => {
    try {
      const data: ProgressEvent = JSON.parse(e.data)
      onEvent(data)
    } catch {}
  }

  eventSource.addEventListener('done', (e: MessageEvent) => {
    try {
      const data: ProgressEvent = JSON.parse(e.data)
      onEvent(data)
    } catch {}
    eventSource.close()
    onDone?.()
  })

  eventSource.onerror = (e) => {
    eventSource.close()
    onError?.(new Error('SSE connection failed'))
  }

  // Return cleanup function
  return () => eventSource.close()
}

// ─── Transcripts API ──────────────────────────────────────────────────────────

export async function getTranscript(jobId: string): Promise<Transcript> {
  return apiFetch<Transcript>(`/transcripts/${jobId}`)
}

export async function updateSegment(
  jobId: string,
  segmentId: string,
  update: { text?: string; speaker_name?: string; start_time?: number; end_time?: number },
): Promise<TranscriptSegment> {
  return apiFetch<TranscriptSegment>(
    `/transcripts/${jobId}/segments/${segmentId}`,
    {
      method: 'PATCH',
      body: JSON.stringify(update),
    },
  )
}

export async function searchTranscript(
  jobId: string,
  query: string,
): Promise<SearchResult[]> {
  const encoded = encodeURIComponent(query)
  return apiFetch<SearchResult[]>(`/transcripts/${jobId}/search?q=${encoded}`)
}

export async function exportTranscript(
  jobId: string,
  request: ExportRequest,
  filename: string,
): Promise<void> {
  const response = await fetch(`${API_V1}/transcripts/${jobId}/export`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    const json = await response.json().catch(() => ({}))
    throw new APIError(response.status, json.detail ?? 'Export failed')
  }

  // Trigger browser download
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const ext = request.format

  const a = document.createElement('a')
  a.href = url
  a.download = filename.endsWith(`.${ext}`) ? filename : `${filename}.${ext}`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

// ─── Chunked Upload Orchestrator ──────────────────────────────────────────────

export interface ChunkedUploadOptions {
  file: File
  mediaType: string
  language: string
  enableDiarization: boolean
  enableTranslation: boolean
  targetLanguage?: string
  whisperModel?: string
  onProgress: (percent: number, stage: string) => void
  onJobCreated: (jobId: string) => void
  onComplete: (jobId: string) => void
  onError: (error: Error) => void
  signal?: AbortSignal
}

export async function performChunkedUpload(
  options: ChunkedUploadOptions,
): Promise<string> {
  const {
    file, mediaType, language, enableDiarization, enableTranslation,
    targetLanguage, whisperModel, onProgress, onJobCreated, onComplete, onError, signal
  } = options

  // Step 1: Initialize upload session
  const initResp = await initUpload({
    filename: file.name,
    file_size: file.size,
    media_type: mediaType as any,
    language,
    enable_diarization: enableDiarization,
    enable_translation: enableTranslation,
    target_language: targetLanguage,
    whisper_model: whisperModel,
  })

  const { job_id: jobId, chunk_size: chunkSize, total_chunks: totalChunks } = initResp
  onJobCreated(jobId)
  onProgress(2, 'Upload initialized...')

  // Step 2: Upload chunks sequentially (parallel = too many concurrent requests)
  let chunksUploaded = 0

  for (let i = 0; i < totalChunks; i++) {
    if (signal?.aborted) {
      await cancelUpload(jobId).catch(() => {})
      throw new Error('Upload cancelled')
    }

    const start = i * chunkSize
    const end = Math.min(start + chunkSize, file.size)
    const chunk = file.slice(start, end)

    await uploadChunk(jobId, i, totalChunks, chunk)

    chunksUploaded++
    const uploadPercent = (chunksUploaded / totalChunks) * 85 // 0-85%
    onProgress(uploadPercent, `Uploading... ${chunksUploaded}/${totalChunks} chunks`)
  }

  onProgress(88, 'Finalizing upload...')

  // Step 3: Complete upload and queue job
  await completeUpload(jobId)
  onProgress(90, 'Upload complete. Starting transcription...')
  onComplete(jobId)

  return jobId
}

export { APIError }
