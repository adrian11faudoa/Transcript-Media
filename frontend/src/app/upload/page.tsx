'use client'

import { useState, useCallback, useRef, useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { performChunkedUpload, subscribeToJobProgress } from '@/lib/api'
import type { MediaType, ProgressEvent } from '@/types'
import { STATUS_LABELS, formatFileSize, ACTIVE_STATUSES } from '@/types'

const ACCEPTED_TYPES = ['video/mp4', 'video/quicktime', 'video/x-msvideo',
  'video/x-matroska', 'video/webm', 'video/avi', 'video/x-flv']

const WHISPER_MODELS = [
  { value: 'tiny', label: 'Tiny', desc: 'Fastest · Lower accuracy' },
  { value: 'base', label: 'Base', desc: 'Fast · Good accuracy' },
  { value: 'small', label: 'Small', desc: 'Balanced' },
  { value: 'medium', label: 'Medium', desc: 'Accurate · Slower' },
  { value: 'large-v3', label: 'Large v3', desc: 'Best accuracy · Slowest' },
]

const LANGUAGES = [
  { value: 'auto', label: 'Auto-detect' },
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'ar', label: 'Arabic' },
  { value: 'hi', label: 'Hindi' },
  { value: 'ru', label: 'Russian' },
]

const MEDIA_TYPES: { value: MediaType; label: string; icon: string }[] = [
  { value: 'movie', label: 'Movie', icon: '🎬' },
  { value: 'tv_series', label: 'TV Series', icon: '📺' },
  { value: 'interview', label: 'Interview', icon: '🎙️' },
  { value: 'podcast', label: 'Podcast', icon: '🎧' },
  { value: 'clip', label: 'Clip', icon: '✂️' },
  { value: 'other', label: 'Other', icon: '📁' },
]

interface ProgressStage {
  key: string
  label: string
  pct: number
}

const PIPELINE_STAGES: ProgressStage[] = [
  { key: 'uploading', label: 'Uploading file', pct: 0 },
  { key: 'extracting_audio', label: 'Extracting audio', pct: 30 },
  { key: 'transcribing', label: 'AI transcription', pct: 50 },
  { key: 'diarizing', label: 'Speaker detection', pct: 75 },
  { key: 'generating_transcript', label: 'Building transcript', pct: 88 },
  { key: 'completed', label: 'Complete', pct: 100 },
]

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
      <div
        className="h-full bg-gradient-to-r from-violet-600 to-violet-400 rounded-full transition-all duration-700 ease-out"
        style={{ width: `${value}%` }}
      />
    </div>
  )
}

export default function UploadPage() {
  const router = useRouter()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const [dragging, setDragging] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [uploadProgress, setUploadProgress] = useState(0)
  const [progressEvent, setProgressEvent] = useState<ProgressEvent | null>(null)
  const [phase, setPhase] = useState<'idle' | 'uploading' | 'processing' | 'done' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)

  // Settings
  const [mediaType, setMediaType] = useState<MediaType>('other')
  const [language, setLanguage] = useState('auto')
  const [whisperModel, setWhisperModel] = useState('large-v3')
  const [enableDiarization, setEnableDiarization] = useState(true)

  // Subscribe to SSE when jobId is set and we're in processing phase
  useEffect(() => {
    if (!jobId || phase !== 'processing') return

    const cleanup = subscribeToJobProgress(
      jobId,
      (event) => {
        setProgressEvent(event)
        if (event.status === 'completed') {
          setPhase('done')
          setTimeout(() => router.push(`/transcript/${jobId}`), 1200)
        }
        if (event.status === 'failed') {
          setPhase('error')
          setError(event.message || 'Transcription failed')
        }
      },
    )

    return cleanup
  }, [jobId, phase, router])

  const handleFile = useCallback((f: File) => {
    if (!ACCEPTED_TYPES.includes(f.type) && !f.name.match(/\.(mp4|mov|avi|mkv|webm|m4v|flv)$/i)) {
      setError('Please upload a video file (MP4, MOV, AVI, MKV, WEBM)')
      return
    }
    setFile(f)
    setError(null)
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [handleFile])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(true)
  }, [])

  const handleDragLeave = useCallback(() => setDragging(false), [])

  const handleStart = async () => {
    if (!file) return
    setPhase('uploading')
    setError(null)
    setUploadProgress(0)

    const abort = new AbortController()
    abortRef.current = abort

    try {
      const id = await performChunkedUpload({
        file,
        mediaType,
        language,
        enableDiarization,
        enableTranslation: false,
        whisperModel,
        signal: abort.signal,
        onProgress: (pct, stage) => {
          setUploadProgress(pct)
        },
        onJobCreated: (id) => setJobId(id),
        onComplete: () => {
          setPhase('processing')
        },
        onError: (err) => {
          setPhase('error')
          setError(err.message)
        },
      })
    } catch (err: any) {
      if (err.message !== 'Upload cancelled') {
        setPhase('error')
        setError(err.message ?? 'Upload failed')
      }
    }
  }

  const handleCancel = () => {
    abortRef.current?.abort()
    setPhase('idle')
    setUploadProgress(0)
    setProgressEvent(null)
  }

  const currentProgress =
    phase === 'uploading'
      ? uploadProgress
      : progressEvent?.progress ?? 0

  const currentStageLabel =
    phase === 'uploading'
      ? `Uploading... ${uploadProgress.toFixed(0)}%`
      : phase === 'processing'
      ? progressEvent?.message ?? 'Processing...'
      : phase === 'done'
      ? 'Complete!'
      : ''

  const activeStageKey = phase === 'uploading'
    ? 'uploading'
    : progressEvent?.stage ?? ''

  const isProcessing = phase === 'uploading' || phase === 'processing'

  return (
    <div className="min-h-screen bg-[#0a0a0f] flex flex-col">
      {/* Header */}
      <header className="border-b border-white/5 px-6 py-4 flex items-center gap-4">
        <button
          onClick={() => router.push('/dashboard')}
          className="text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
        </button>
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md bg-violet-600 flex items-center justify-center">
            <svg className="w-3.5 h-3.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
            </svg>
          </div>
          <span className="text-sm font-medium text-white">New Transcription</span>
        </div>
      </header>

      <div className="flex-1 max-w-4xl mx-auto w-full px-6 py-10">
        {/* Progress overlay when processing */}
        {isProcessing && (
          <div className="mb-8 bg-white/[0.03] border border-white/8 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <p className="text-white text-sm font-medium">{file?.name}</p>
                <p className="text-zinc-500 text-xs mt-0.5">{currentStageLabel}</p>
              </div>
              <button
                onClick={handleCancel}
                className="text-xs text-zinc-600 hover:text-red-400 transition-colors"
              >
                Cancel
              </button>
            </div>
            <ProgressBar value={currentProgress} />

            {/* Pipeline stages */}
            <div className="flex gap-2 mt-5">
              {PIPELINE_STAGES.slice(0, -1).map((stage, i) => {
                const isActive = stage.key === activeStageKey
                const isDone = currentProgress >= stage.pct + 15
                return (
                  <div key={stage.key} className="flex-1 text-center">
                    <div className={`h-0.5 rounded-full mb-2 transition-all duration-500 ${
                      isDone ? 'bg-violet-500' : isActive ? 'bg-violet-500/50' : 'bg-white/5'
                    }`} />
                    <p className={`text-[10px] ${
                      isActive ? 'text-violet-400' : isDone ? 'text-zinc-500' : 'text-zinc-700'
                    }`}>{stage.label}</p>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {phase === 'done' && (
          <div className="mb-8 bg-emerald-500/10 border border-emerald-500/20 rounded-2xl p-6 text-center">
            <div className="w-10 h-10 rounded-full bg-emerald-500/20 flex items-center justify-center mx-auto mb-3">
              <svg className="w-5 h-5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="text-emerald-400 font-medium text-sm">Transcription complete!</p>
            <p className="text-zinc-500 text-xs mt-1">Redirecting to transcript...</p>
          </div>
        )}

        {phase === 'error' && (
          <div className="mb-8 bg-red-500/10 border border-red-500/20 rounded-2xl p-5">
            <p className="text-red-400 text-sm font-medium">Transcription failed</p>
            <p className="text-red-400/70 text-xs mt-1">{error}</p>
            <button
              onClick={() => { setPhase('idle'); setError(null) }}
              className="mt-3 text-xs text-zinc-500 hover:text-zinc-300 underline transition-colors"
            >
              Try again
            </button>
          </div>
        )}

        <div className="grid grid-cols-3 gap-6">
          {/* Drop zone */}
          <div className="col-span-2">
            <div
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onClick={() => !isProcessing && fileInputRef.current?.click()}
              className={`
                relative border-2 border-dashed rounded-2xl transition-all duration-200 cursor-pointer
                flex flex-col items-center justify-center text-center
                ${file ? 'h-44' : 'h-64'}
                ${dragging
                  ? 'border-violet-500 bg-violet-500/5'
                  : file
                  ? 'border-white/10 bg-white/[0.02]'
                  : 'border-white/10 hover:border-white/20 bg-white/[0.02] hover:bg-white/[0.04]'
                }
                ${isProcessing ? 'pointer-events-none opacity-60' : ''}
              `}
            >
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                accept=".mp4,.mov,.avi,.mkv,.webm,.m4v,.flv"
                onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
              />

              {file ? (
                <div className="flex items-center gap-4 px-6 w-full">
                  <div className="w-12 h-12 rounded-xl bg-violet-600/20 flex items-center justify-center flex-shrink-0">
                    <svg className="w-6 h-6 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                        d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                  </div>
                  <div className="text-left min-w-0 flex-1">
                    <p className="text-white text-sm font-medium truncate">{file.name}</p>
                    <p className="text-zinc-500 text-xs mt-0.5">{formatFileSize(file.size)}</p>
                  </div>
                  {!isProcessing && (
                    <button
                      onClick={(e) => { e.stopPropagation(); setFile(null) }}
                      className="text-zinc-600 hover:text-zinc-400 transition-colors flex-shrink-0"
                    >
                      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  )}
                </div>
              ) : (
                <>
                  <div className="w-14 h-14 rounded-2xl bg-white/5 flex items-center justify-center mb-4">
                    <svg className="w-7 h-7 text-zinc-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                        d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                    </svg>
                  </div>
                  <p className="text-zinc-400 text-sm font-medium mb-1">
                    Drop your video here
                  </p>
                  <p className="text-zinc-600 text-xs">
                    MP4, MOV, AVI, MKV, WEBM up to 5GB
                  </p>
                  <p className="text-zinc-700 text-xs mt-3">or click to browse</p>
                </>
              )}
            </div>

            {/* Media type selector */}
            <div className="mt-4">
              <label className="text-xs text-zinc-500 uppercase tracking-wider mb-2 block">
                Content Type
              </label>
              <div className="flex flex-wrap gap-2">
                {MEDIA_TYPES.map(mt => (
                  <button
                    key={mt.value}
                    onClick={() => setMediaType(mt.value)}
                    disabled={isProcessing}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium border transition-all ${
                      mediaType === mt.value
                        ? 'border-violet-500/50 bg-violet-600/20 text-violet-300'
                        : 'border-white/5 bg-white/[0.03] text-zinc-500 hover:text-zinc-300'
                    } disabled:opacity-40`}
                  >
                    <span>{mt.icon}</span>
                    {mt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Settings panel */}
          <div className="space-y-5">
            <div>
              <label className="text-xs text-zinc-500 uppercase tracking-wider mb-2 block">
                Language
              </label>
              <select
                value={language}
                onChange={(e) => setLanguage(e.target.value)}
                disabled={isProcessing}
                className="w-full bg-white/[0.03] border border-white/8 rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-violet-500/50 disabled:opacity-40 appearance-none"
              >
                {LANGUAGES.map(l => (
                  <option key={l.value} value={l.value} className="bg-zinc-900">
                    {l.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-xs text-zinc-500 uppercase tracking-wider mb-2 block">
                AI Model
              </label>
              <div className="space-y-1.5">
                {WHISPER_MODELS.map(m => (
                  <button
                    key={m.value}
                    onClick={() => setWhisperModel(m.value)}
                    disabled={isProcessing}
                    className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs border transition-all disabled:opacity-40 ${
                      whisperModel === m.value
                        ? 'border-violet-500/40 bg-violet-600/15 text-violet-300'
                        : 'border-white/5 bg-white/[0.02] text-zinc-500 hover:text-zinc-300'
                    }`}
                  >
                    <span className="font-medium">{m.label}</span>
                    <span className="text-zinc-600 text-[10px]">{m.desc}</span>
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center justify-between bg-white/[0.02] border border-white/5 rounded-xl px-4 py-3">
              <div>
                <p className="text-xs text-zinc-300 font-medium">Speaker Detection</p>
                <p className="text-[10px] text-zinc-600 mt-0.5">Identify different speakers</p>
              </div>
              <button
                onClick={() => setEnableDiarization(d => !d)}
                disabled={isProcessing}
                className={`w-10 h-5 rounded-full transition-colors relative disabled:opacity-40 ${
                  enableDiarization ? 'bg-violet-600' : 'bg-white/10'
                }`}
              >
                <div className={`absolute top-0.5 h-4 w-4 bg-white rounded-full shadow transition-transform ${
                  enableDiarization ? 'translate-x-5' : 'translate-x-0.5'
                }`} />
              </button>
            </div>

            <button
              onClick={handleStart}
              disabled={!file || isProcessing || phase === 'done'}
              className="w-full py-3 bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-medium rounded-xl transition-colors"
            >
              {isProcessing ? 'Processing...' : 'Start Transcription'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
