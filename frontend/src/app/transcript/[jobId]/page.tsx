'use client'

import { useState, useEffect, useRef, useCallback, use } from 'react'
import { useRouter } from 'next/navigation'
import {
  getTranscript, getJob, updateSegment,
  searchTranscript, exportTranscript
} from '@/lib/api'
import type { Transcript, TranscriptSegment, Job, SearchResult, ExportFormat } from '@/types'
import {
  formatDuration, formatFileSize, getSpeakerColor, SPEAKER_COLORS
} from '@/types'

// ─── Speaker Badge ─────────────────────────────────────────────────────────────
function SpeakerBadge({ speaker, color }: { speaker: string; color: string }) {
  return (
    <span
      className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wide"
      style={{ backgroundColor: `${color}22`, color, border: `1px solid ${color}44` }}
    >
      {speaker}
    </span>
  )
}

// ─── Editable Segment ─────────────────────────────────────────────────────────
function SegmentRow({
  segment, jobId, speakers, isHighlighted, onUpdate, onClick
}: {
  segment: TranscriptSegment
  jobId: string
  speakers: string[]
  isHighlighted: boolean
  onUpdate: (seg: TranscriptSegment) => void
  onClick: (time: number) => void
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(segment.text)
  const [speakerName, setSpeakerName] = useState(
    segment.speaker_name || segment.speaker_id || ''
  )
  const [saving, setSaving] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const speakerId = segment.speaker_id ?? 'UNKNOWN'
  const displaySpeaker = segment.speaker_name || segment.speaker_id
  const color = getSpeakerColor(speakerId, speakers)

  const handleSave = async () => {
    if (text === segment.text && speakerName === (segment.speaker_name || segment.speaker_id || '')) {
      setEditing(false)
      return
    }
    setSaving(true)
    try {
      const updated = await updateSegment(jobId, segment.id, {
        text,
        speaker_name: speakerName || undefined,
      })
      onUpdate(updated)
      setEditing(false)
    } catch (err) {
      console.error('Failed to save segment:', err)
    } finally {
      setSaving(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') { setText(segment.text); setEditing(false) }
    if (e.key === 'Enter' && e.metaKey) handleSave()
  }

  useEffect(() => {
    if (editing && textareaRef.current) {
      textareaRef.current.focus()
      textareaRef.current.select()
    }
  }, [editing])

  return (
    <div
      id={`seg-${segment.id}`}
      className={`group flex gap-4 px-6 py-4 transition-colors rounded-xl ${
        isHighlighted
          ? 'bg-amber-500/10 border border-amber-500/20'
          : 'hover:bg-white/[0.02]'
      }`}
    >
      {/* Timestamp */}
      <button
        onClick={() => onClick(segment.start_time)}
        className="flex-shrink-0 text-xs text-zinc-600 hover:text-violet-400 transition-colors mt-1 font-mono tabular-nums w-14 text-right"
      >
        {formatDuration(segment.start_time)}
      </button>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {displaySpeaker && (
          <div className="mb-1.5 flex items-center gap-2">
            <SpeakerBadge speaker={displaySpeaker} color={color} />
            {editing && (
              <input
                value={speakerName}
                onChange={e => setSpeakerName(e.target.value)}
                placeholder="Speaker name"
                className="text-xs bg-white/5 border border-white/10 rounded-md px-2 py-0.5 text-zinc-300 outline-none focus:border-violet-500/50 w-36"
              />
            )}
          </div>
        )}

        {editing ? (
          <div>
            <textarea
              ref={textareaRef}
              value={text}
              onChange={e => setText(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={3}
              className="w-full bg-white/5 border border-violet-500/30 rounded-xl px-3 py-2 text-sm text-white resize-none outline-none focus:border-violet-500"
            />
            <div className="flex items-center gap-2 mt-2">
              <button
                onClick={handleSave}
                disabled={saving}
                className="text-xs px-3 py-1.5 bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white rounded-lg transition-colors"
              >
                {saving ? 'Saving...' : 'Save'}
              </button>
              <button
                onClick={() => { setText(segment.text); setEditing(false) }}
                className="text-xs px-3 py-1.5 bg-white/5 hover:bg-white/10 text-zinc-400 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <span className="text-[10px] text-zinc-600">⌘+Enter to save</span>
            </div>
          </div>
        ) : (
          <p
            className="text-sm text-zinc-300 leading-relaxed cursor-text"
            onDoubleClick={() => setEditing(true)}
          >
            {segment.text}
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="flex-shrink-0 flex items-start gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        {segment.confidence != null && (
          <span className="text-[10px] text-zinc-600 mt-1">
            {(segment.confidence * 100).toFixed(0)}%
          </span>
        )}
        <button
          onClick={() => setEditing(e => !e)}
          className="p-1.5 rounded-lg hover:bg-white/5 text-zinc-600 hover:text-zinc-300 transition-colors"
          title="Edit segment"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
          </svg>
        </button>
      </div>
    </div>
  )
}

// ─── Export Panel ─────────────────────────────────────────────────────────────
function ExportPanel({ jobId, filename }: { jobId: string; filename: string }) {
  const [exporting, setExporting] = useState<ExportFormat | null>(null)

  const formats: { format: ExportFormat; label: string; desc: string; icon: string }[] = [
    { format: 'docx', label: 'Word', desc: 'Formatted document', icon: '📄' },
    { format: 'pdf', label: 'PDF', desc: 'Print-ready', icon: '📋' },
    { format: 'txt', label: 'Text', desc: 'Plain text', icon: '📝' },
    { format: 'srt', label: 'SRT', desc: 'Subtitles', icon: '🎞️' },
    { format: 'vtt', label: 'VTT', desc: 'Web subtitles', icon: '🌐' },
    { format: 'json', label: 'JSON', desc: 'Structured data', icon: '⚡' },
  ]

  const handleExport = async (format: ExportFormat) => {
    setExporting(format)
    try {
      const baseName = filename.replace(/\.[^/.]+$/, '')
      await exportTranscript(jobId, {
        format,
        include_speakers: true,
        include_timestamps: true,
        include_confidence: false,
      }, baseName)
    } catch (err) {
      console.error('Export failed:', err)
    } finally {
      setExporting(null)
    }
  }

  return (
    <div className="grid grid-cols-3 gap-2">
      {formats.map(f => (
        <button
          key={f.format}
          onClick={() => handleExport(f.format)}
          disabled={!!exporting}
          className="flex flex-col items-center gap-1 px-2 py-3 rounded-xl bg-white/[0.03] border border-white/5 hover:border-white/15 hover:bg-white/[0.06] transition-all disabled:opacity-40 group"
        >
          <span className="text-lg">{exporting === f.format ? '⏳' : f.icon}</span>
          <span className="text-xs text-zinc-300 font-medium">{f.label}</span>
          <span className="text-[10px] text-zinc-600">{f.desc}</span>
        </button>
      ))}
    </div>
  )
}

// ─── Main Page ────────────────────────────────────────────────────────────────
export default function TranscriptPage({ params }: { params: Promise<{ jobId: string }> }) {
  const { jobId } = use(params)
  const router = useRouter()

  const [job, setJob] = useState<Job | null>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [segments, setSegments] = useState<TranscriptSegment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Search
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [highlightedSegment, setHighlightedSegment] = useState<string | null>(null)

  // Sidebar tab
  const [sidebarTab, setSidebarTab] = useState<'info' | 'search' | 'export'>('info')

  // Speakers
  const allSpeakers = [...new Set(
    segments.map(s => s.speaker_id).filter(Boolean) as string[]
  )]

  useEffect(() => {
    async function load() {
      try {
        const [j, t] = await Promise.all([getJob(jobId), getTranscript(jobId)])
        setJob(j)
        setTranscript(t)
        setSegments([...t.segments].sort((a, b) => a.segment_index - b.segment_index))
      } catch (err: any) {
        setError(err.message ?? 'Failed to load transcript')
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [jobId])

  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) { setSearchResults([]); return }
    setSearchLoading(true)
    try {
      const results = await searchTranscript(jobId, searchQuery)
      setSearchResults(results)
    } catch {}
    finally { setSearchLoading(false) }
  }, [jobId, searchQuery])

  useEffect(() => {
    const t = setTimeout(handleSearch, 350)
    return () => clearTimeout(t)
  }, [handleSearch])

  const scrollToSegment = (segId: string, startTime: number) => {
    setHighlightedSegment(segId)
    document.getElementById(`seg-${segId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setTimeout(() => setHighlightedSegment(null), 2500)
  }

  const handleSegmentUpdate = (updated: TranscriptSegment) => {
    setSegments(prev => prev.map(s => s.id === updated.id ? updated : s))
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0a0a0f] flex items-center justify-center">
        <div className="text-zinc-600 text-sm">Loading transcript...</div>
      </div>
    )
  }

  if (error || !transcript) {
    return (
      <div className="min-h-screen bg-[#0a0a0f] flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-400 text-sm mb-3">{error ?? 'Transcript not found'}</p>
          <button onClick={() => router.push('/dashboard')}
            className="text-zinc-500 hover:text-zinc-300 text-sm transition-colors">
            ← Back to dashboard
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[#0a0a0f] flex flex-col">
      {/* Header */}
      <header className="border-b border-white/5 bg-[#0a0a0f]/90 backdrop-blur sticky top-0 z-20">
        <div className="flex items-center gap-4 px-6 py-3">
          <button onClick={() => router.push('/dashboard')}
            className="text-zinc-600 hover:text-zinc-400 transition-colors">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-sm font-medium text-white truncate">
              {job?.original_filename ?? 'Transcript'}
            </h1>
            <p className="text-xs text-zinc-600">
              {transcript.word_count.toLocaleString()} words
              {transcript.speaker_count > 0 && ` · ${transcript.speaker_count} speaker${transcript.speaker_count !== 1 ? 's' : ''}`}
              {transcript.detected_language && ` · ${transcript.detected_language.toUpperCase()}`}
            </p>
          </div>
          {/* Sidebar tabs in header */}
          <div className="flex items-center gap-1 bg-white/[0.03] rounded-xl p-1">
            {(['info', 'search', 'export'] as const).map(tab => (
              <button
                key={tab}
                onClick={() => setSidebarTab(tab)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition-all ${
                  sidebarTab === tab
                    ? 'bg-white/10 text-white'
                    : 'text-zinc-600 hover:text-zinc-400'
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Transcript */}
        <div className="flex-1 overflow-y-auto">
          <div className="max-w-3xl mx-auto py-6 space-y-1">
            {segments.map(seg => (
              <SegmentRow
                key={seg.id}
                segment={seg}
                jobId={jobId}
                speakers={allSpeakers}
                isHighlighted={highlightedSegment === seg.id}
                onUpdate={handleSegmentUpdate}
                onClick={(time) => {}}
              />
            ))}
          </div>
        </div>

        {/* Sidebar */}
        <aside className="w-80 border-l border-white/5 bg-[#0a0a0f] overflow-y-auto flex-shrink-0">
          <div className="p-5">
            {sidebarTab === 'info' && (
              <div className="space-y-5">
                <div>
                  <h3 className="text-xs text-zinc-500 uppercase tracking-wider mb-3">File Info</h3>
                  <div className="space-y-2">
                    {[
                      ['Duration', job?.duration_seconds ? formatDuration(job.duration_seconds) : '—'],
                      ['File size', job?.file_size_bytes ? formatFileSize(job.file_size_bytes) : '—'],
                      ['Language', transcript.detected_language?.toUpperCase() ?? 'Unknown'],
                      ['Speakers', String(transcript.speaker_count)],
                      ['Words', transcript.word_count.toLocaleString()],
                      ['Model', job?.whisper_model ?? '—'],
                    ].map(([label, value]) => (
                      <div key={label} className="flex items-center justify-between">
                        <span className="text-xs text-zinc-600">{label}</span>
                        <span className="text-xs text-zinc-300">{value}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {transcript.summary && (
                  <div>
                    <h3 className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Summary</h3>
                    <p className="text-xs text-zinc-400 leading-relaxed">{transcript.summary}</p>
                  </div>
                )}

                {transcript.keywords && transcript.keywords.length > 0 && (
                  <div>
                    <h3 className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Keywords</h3>
                    <div className="flex flex-wrap gap-1.5">
                      {transcript.keywords.map(kw => (
                        <span key={kw}
                          className="px-2 py-0.5 bg-white/[0.04] border border-white/8 rounded-lg text-[11px] text-zinc-400">
                          {kw}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {allSpeakers.length > 0 && (
                  <div>
                    <h3 className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Speakers</h3>
                    <div className="space-y-1.5">
                      {allSpeakers.map(spk => (
                        <div key={spk} className="flex items-center gap-2">
                          <div
                            className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                            style={{ backgroundColor: getSpeakerColor(spk, allSpeakers) }}
                          />
                          <span className="text-xs text-zinc-400">{spk}</span>
                          <span className="text-[10px] text-zinc-600 ml-auto">
                            {segments.filter(s => s.speaker_id === spk).length} segments
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {sidebarTab === 'search' && (
              <div>
                <h3 className="text-xs text-zinc-500 uppercase tracking-wider mb-3">Search</h3>
                <div className="relative mb-4">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={e => setSearchQuery(e.target.value)}
                    placeholder="Search transcript..."
                    className="w-full bg-white/[0.03] border border-white/8 rounded-xl px-3 py-2.5 text-sm text-white placeholder-zinc-600 outline-none focus:border-violet-500/50"
                  />
                  {searchLoading && (
                    <div className="absolute right-3 top-1/2 -translate-y-1/2">
                      <div className="w-3 h-3 border border-violet-500/50 border-t-violet-500 rounded-full animate-spin" />
                    </div>
                  )}
                </div>

                {searchResults.length > 0 && (
                  <div className="space-y-2">
                    <p className="text-[10px] text-zinc-600">{searchResults.length} results</p>
                    {searchResults.map(result => (
                      <button
                        key={result.segment_id}
                        onClick={() => scrollToSegment(result.segment_id, result.start_time)}
                        className="w-full text-left p-3 rounded-xl bg-white/[0.02] border border-white/5 hover:border-white/15 transition-all"
                      >
                        <div className="text-[10px] text-zinc-500 font-mono mb-1">
                          {formatDuration(result.start_time)}
                          {result.speaker_name && ` · ${result.speaker_name}`}
                        </div>
                        <p
                          className="text-xs text-zinc-400 leading-relaxed line-clamp-3"
                          dangerouslySetInnerHTML={{ __html: result.highlight }}
                        />
                      </button>
                    ))}
                  </div>
                )}

                {searchQuery && !searchLoading && searchResults.length === 0 && (
                  <p className="text-xs text-zinc-600 text-center py-6">No results found</p>
                )}
              </div>
            )}

            {sidebarTab === 'export' && (
              <div>
                <h3 className="text-xs text-zinc-500 uppercase tracking-wider mb-3">Export Transcript</h3>
                <ExportPanel jobId={jobId} filename={job?.original_filename ?? 'transcript'} />
                <p className="text-[10px] text-zinc-600 text-center mt-4">
                  Double-click any segment to edit it before exporting
                </p>
              </div>
            )}
          </div>
        </aside>
      </div>

      <style jsx global>{`
        mark {
          background-color: rgba(251, 191, 36, 0.3);
          color: #fbbf24;
          border-radius: 2px;
          padding: 0 1px;
        }
      `}</style>
    </div>
  )
}
