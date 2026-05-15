import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { Button } from './ui/button'
import { useCorosStatus, useCorosSync } from '../api/hooks'

export function CorosSyncButton() {
  const { data: status } = useCorosStatus()
  const sync = useCorosSync()
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  const handleSync = async () => {
    setSyncMsg('Syncing...')
    try {
      const body = await sync.mutateAsync(undefined)
      if (!body) { setSyncMsg(null); return }
      const reader = (body as ReadableStream).getReader()
      const dec = new TextDecoder()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = dec.decode(value)
        for (const line of chunk.split('\n').filter(l => l.startsWith('data:'))) {
          try {
            const evt = JSON.parse(line.slice(5))
            if (evt.status === 'complete') {
              setSyncMsg(`Done — ${evt.total_downloaded} new`)
              setTimeout(() => setSyncMsg(null), 4000)
            } else if (evt.status === 'downloading') {
              setSyncMsg(`↓ ${evt.activity_id}`)
            } else if (evt.status === 'error') {
              setSyncMsg(`Error: ${evt.error}`)
            }
          } catch { /* skip malformed lines */ }
        }
      }
    } catch (e) {
      setSyncMsg(`Failed`)
      setTimeout(() => setSyncMsg(null), 3000)
    }
  }

  if (!status?.authenticated) return null

  return (
    <div className="flex items-center gap-3">
      {syncMsg && (
        <span className="text-xs text-[#7d8fa6]">{syncMsg}</span>
      )}
      {status.last_sync && !syncMsg && (
        <span className="text-xs text-[#3e4e63]">
          synced {new Date(status.last_sync).toLocaleDateString()}
        </span>
      )}
      <span className="text-xs text-[#22c55e]">● {status.email}</span>
      <Button
        variant="secondary"
        size="sm"
        onClick={handleSync}
        disabled={sync.isPending}
        className="gap-1.5"
      >
        <RefreshCw className={`h-3 w-3 ${sync.isPending ? 'animate-spin' : ''}`} />
        Sync
      </Button>
    </div>
  )
}
