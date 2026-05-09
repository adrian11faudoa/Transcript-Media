'use client'

import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { listJobs, deleteJob } from '@/lib/api'
import type { Job, PaginatedResponse } from '@/types'
import {
  formatDuration, formatFileSize, STATUS_LABELS, ACTIVE_STATUSES
} from '@/types'

const STATUS_COLORS: Record<string, string> = {
  completed:    'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  failed:       'bg-red-500/15 text-red-400 border-red-500/30',
  cancelled:    'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
  transcribing: 'bg-violet-500/15 text-violet-400 border-violet-500/30',
  queued:       'bg-amber-500/15 text-amber-400 border-amber-500/30',
  uploading:    'bg-blue-500/15 text-blue-400 border-blue-500/30',
  default:      'bg-blue-500/15 text-blue-400 border-blue-500/30',
}

function getStatusColor(status: string) {
  return STATUS_COLORS[status] ?? STATUS_COLORS.default
}

function JobRow({ job, onDelete }: { job: Job; onDelete: () => void }) {
  const isActive = ACTIVE_STATUSES.includes(job.status as any)

  return (
    <tr className="border-b border-white/5 hover:bg-white/[0.02] transition-colors group">
      <td className="py-4 px-6">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center flex-shrink-0">
            <svg className="w-4 h-4 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M15 10l4.553-2.276A1 1 0 0121 8.723v6.554a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
          </div>
          <div className="min-w-0">
            <p className="text-sm text-white font-medium truncate max-w-[240px]">
              {job.original_filename}
            </p>
            <p className="text-xs text-zinc-500 mt-0.5">
              {formatFileSize(job.file_size_bytes)}
              {job.duration_seconds && ` · ${formatDuration(job.duration_seconds)}`}
            </p>
          </div>
        </div>
      </td>

      <td className="py-4 px-6">
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium border ${getStatusColor(job.status)}`}>
            {isActive && (
              <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
            )}
            {STATUS_LABELS[job.status] ?? job.status}
          </span>
        </div>
        {isActive && (
          <div className="mt-1.5 w-32">
            <div className="h-0.5 bg-white/5 rounded-full overflow-hidden">
              <div
                className="h-full bg-violet-500 rounded-full transition-all duration-500"
                style={{ width: `${job.progress_percent}%` }}
              />
            </div>
          </div>
        )}
      </td>

      <td className="py-4 px-6">
        <span className="text-xs text-zinc-400 uppercase tracking-wider">
          {job.language === 'auto' ? 'Auto-detect' : job.language.toUpperCase()}
        </span>
      </td>

      <td className="py-4 px-6">
        <span className="text-xs text-zinc-500">
          {new Date(job.created_at).toLocaleDateString(undefined, {
            month: 'short', day: 'numeric', year: 'numeric',
          })}
        </span>
      </td>

      <td className="py-4 px-6">
        <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
          {job.status === 'completed' && (
            <Link
              href={`/transcript/${job.id}`}
              className="text-xs px-3 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white transition-colors"
            >
              View
            </Link>
          )}
          <button
            onClick={onDelete}
            className="text-xs px-3 py-1.5 rounded-lg bg-white/5 hover:bg-red-500/20 hover:text-red-400 text-zinc-400 transition-colors"
          >
            Delete
          </button>
        </div>
      </td>
    </tr>
  )
}

export default function DashboardPage() {
  const [data, setData] = useState<PaginatedResponse<Job> | null>(null)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)

  const fetchJobs = useCallback(async () => {
    try {
      const result = await listJobs(page, 20)
      setData(result)
    } catch (err) {
      console.error('Failed to load jobs:', err)
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    fetchJobs()
    // Poll for active jobs
    const interval = setInterval(fetchJobs, 5000)
    return () => clearInterval(interval)
  }, [fetchJobs])

  const handleDelete = async (jobId: string) => {
    if (!confirm('Delete this job and its transcript?')) return
    await deleteJob(jobId)
    fetchJobs()
  }

  const jobs = data?.items ?? []
  const completedCount = jobs.filter(j => j.status === 'completed').length
  const activeCount = jobs.filter(j => ACTIVE_STATUSES.includes(j.status as any)).length
  const totalDuration = jobs
    .filter(j => j.duration_seconds)
    .reduce((sum, j) => sum + (j.duration_seconds ?? 0), 0)

  return (
    <div className="min-h-screen bg-[#0a0a0f]">
      {/* Header */}
      <header className="border-b border-white/5 bg-[#0a0a0f]/80 backdrop-blur sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-600 to-violet-800 flex items-center justify-center">
              <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <span className="font-semibold text-white tracking-tight">TranscriptAI</span>
          </div>
          <Link
            href="/upload"
            className="inline-flex items-center gap-2 px-4 py-2 bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium rounded-xl transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
            </svg>
            New Transcription
          </Link>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8">
        {/* Stats */}
        <div className="grid grid-cols-4 gap-4 mb-8">
          {[
            { label: 'Total Files', value: data?.total ?? 0 },
            { label: 'Completed', value: completedCount, color: 'text-emerald-400' },
            { label: 'Processing', value: activeCount, color: 'text-violet-400' },
            { label: 'Total Duration', value: formatDuration(totalDuration) },
          ].map(stat => (
            <div key={stat.label} className="bg-white/[0.03] border border-white/5 rounded-2xl p-5">
              <p className="text-xs text-zinc-500 uppercase tracking-wider mb-1">{stat.label}</p>
              <p className={`text-2xl font-semibold ${stat.color ?? 'text-white'}`}>{stat.value}</p>
            </div>
          ))}
        </div>

        {/* Jobs Table */}
        <div className="bg-white/[0.02] border border-white/5 rounded-2xl overflow-hidden">
          <div className="px-6 py-4 border-b border-white/5 flex items-center justify-between">
            <h2 className="text-sm font-medium text-white">Recent Jobs</h2>
            <button
              onClick={fetchJobs}
              className="text-xs text-zinc-500 hover:text-zinc-300 transition-colors"
            >
              Refresh
            </button>
          </div>

          {loading ? (
            <div className="py-16 text-center text-zinc-600 text-sm">Loading...</div>
          ) : jobs.length === 0 ? (
            <div className="py-16 text-center">
              <p className="text-zinc-600 text-sm mb-4">No transcription jobs yet</p>
              <Link
                href="/upload"
                className="text-violet-400 hover:text-violet-300 text-sm transition-colors"
              >
                Upload your first video →
              </Link>
            </div>
          ) : (
            <table className="w-full">
              <thead>
                <tr className="border-b border-white/5">
                  {['File', 'Status', 'Language', 'Created', 'Actions'].map(h => (
                    <th key={h} className="py-3 px-6 text-left text-xs text-zinc-600 uppercase tracking-wider font-medium">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {jobs.map(job => (
                  <JobRow
                    key={job.id}
                    job={job}
                    onDelete={() => handleDelete(job.id)}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </main>
    </div>
  )
}
