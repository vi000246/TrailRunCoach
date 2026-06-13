import { useState, useRef } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from './ui/card'
import { Input } from './ui/input'
import { Label } from './ui/label'
import { Button } from './ui/button'

interface SyncEvent {
  status: string
  activity_id?: string
  date?: string
  file?: string
  sport?: string
  error?: string
  reason?: string
  total_downloaded?: number
  total_checked?: number
  since?: string
  until?: string
}

interface Props {
  title: string
  authenticated: boolean
  accountLabel?: string | null
  lastSync?: string | null
  /** Field label for the login identifier (e.g. "Email" for COROS, "Username" for TP). */
  idLabel?: string
  loginPending: boolean
  loginError: string | null
  onLogin: (id: string, password: string) => void
  /** Triggers a sync; resolves to the SSE body stream (or null/undefined). */
  onSync: (since?: string) => Promise<ReadableStream | null | undefined>
  /** Called once a sync completes, so the parent can refresh the inventory. */
  onSynced?: () => void
}

const statusColor = (s: string) => {
  if (s === 'complete' || s === 'downloaded') return 'text-[#22c55e]'
  if (s === 'error') return 'text-[#f87171]'
  if (s === 'skipped') return 'text-[#3e4e63]'
  return 'text-[#7d8fa6]'
}

export function ProviderSyncCard({
  title, authenticated, accountLabel, lastSync, idLabel = 'Email',
  loginPending, loginError, onLogin, onSync, onSynced,
}: Props) {
  const [since, setSince] = useState('')
  const [log, setLog] = useState<SyncEvent[]>([])
  const [syncing, setSyncing] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  const appendLog = (evt: SyncEvent) => {
    setLog(prev => {
      const next = [...prev, evt]
      setTimeout(() => logRef.current?.scrollTo({ top: 99999, behavior: 'smooth' }), 10)
      return next
    })
  }

  const handleLogin = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    onLogin(fd.get('id') as string, fd.get('password') as string)
  }

  const handleSync = async () => {
    setLog([])
    setSyncing(true)
    try {
      const body = await onSync(since || undefined)
      if (!body) { setSyncing(false); return }
      const reader = (body as ReadableStream).getReader()
      const dec = new TextDecoder()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        for (const line of dec.decode(value).split('\n')) {
          const trimmed = line.startsWith('data:') ? line.slice(5).trim() : ''
          if (!trimmed) continue
          try {
            const evt = JSON.parse(trimmed) as SyncEvent
            appendLog(evt)
            if (evt.status === 'complete') onSynced?.()
          } catch { /* skip */ }
        }
      }
    } catch (e) {
      appendLog({ status: 'error', error: String(e) })
    } finally {
      setSyncing(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {authenticated ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="text-[#22c55e] text-sm">●</span>
              <span className="text-sm text-[#e8edf5]">{accountLabel ?? '已連線'}</span>
            </div>
            {lastSync && (
              <p className="text-xs text-[#3e4e63]">Last sync: {new Date(lastSync).toLocaleString()}</p>
            )}
          </div>
        ) : (
          <form onSubmit={handleLogin} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor={`${title}-id`}>{idLabel}</Label>
                <Input id={`${title}-id`} name="id" required placeholder="…" />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${title}-pw`}>Password</Label>
                <Input id={`${title}-pw`} name="password" type="password" required placeholder="••••••••" />
              </div>
            </div>
            {loginError && <p className="text-xs text-[#f87171]">{loginError}</p>}
            <Button type="submit" size="sm" disabled={loginPending}>
              {loginPending ? 'Logging in...' : 'Login'}
            </Button>
          </form>
        )}

        {authenticated && (
          <div className="space-y-3 pt-2 border-t border-[#1c2333]">
            <div className="flex items-end gap-3">
              <div className="space-y-1.5 flex-1">
                <Label htmlFor={`${title}-since`}>Since date (optional)</Label>
                <Input id={`${title}-since`} type="date" value={since}
                  onChange={e => setSince(e.target.value)} placeholder="YYYY-MM-DD" />
                <p className="text-[10px] text-[#3e4e63]">Leave blank to sync all history</p>
              </div>
              <Button size="sm" onClick={handleSync} disabled={syncing} className="mb-6">
                {syncing ? 'Syncing...' : 'Start Sync'}
              </Button>
            </div>
            {log.length > 0 && (
              <div ref={logRef}
                className="bg-[#07090f] border border-[#1c2333] rounded p-3 h-72 overflow-y-auto font-mono text-[11px] space-y-0.5">
                {log.map((evt, i) => (
                  <div key={i} className={`${statusColor(evt.status)} leading-relaxed`}>
                    {evt.status === 'started' && <span>▶ Sync started {evt.since} → {evt.until}</span>}
                    {evt.status === 'checking' && <span className="text-[#1c2333]">· checking {evt.activity_id} {evt.date}</span>}
                    {evt.status === 'downloaded' && <span>✓ {evt.activity_id} {evt.date} [{evt.sport}] {evt.file}</span>}
                    {evt.status === 'skipped' && <span>– {evt.activity_id} skipped ({evt.reason})</span>}
                    {evt.status === 'error' && <span>✗ {evt.activity_id ?? ''} {evt.error}</span>}
                    {evt.status === 'complete' && <span>■ Done — {evt.total_downloaded} downloaded / {evt.total_checked} checked</span>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
