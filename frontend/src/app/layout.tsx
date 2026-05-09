import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' })

export const metadata: Metadata = {
  title: 'TranscriptAI — AI Video Transcription',
  description: 'Production-grade AI transcription for movies, podcasts, interviews and more.',
  keywords: ['transcription', 'AI', 'video', 'whisper', 'subtitles'],
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="bg-[#0a0a0f] text-white antialiased">
        {children}
      </body>
    </html>
  )
}
