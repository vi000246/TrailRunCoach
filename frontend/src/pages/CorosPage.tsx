import { useState, useRef } from 'react'
import { useCorosStatus, useCorosLogin, useCorosSync } from '../api/hooks'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Input } from '../components/ui/input'
import { Label } from '../components/ui/label'
import { Button } from '../components/ui/button'

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

export function CorosPage() {
  const { data: status, isLoading } = useCorosStatus()
  const login = useCorosLogin()
  const sync = useCorosSync()

  const [loginError, setLoginError] = useState<string | null>(null)
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
    setLoginError(null)
    const fd = new FormData(e.currentTarget)
    login.mutate(
      { email: fd.get('email') as string, password: fd.get('password') as string },
      { onError: (err) => setLoginError(err.message) },
    )
  }

  const handleSync = async () => {
    setLog([])
    setSyncing(true)
    try {
      const body = await sync.mutateAsync(since || undefined)
      if (!body) { setSyncing(false); return }
      const reader = (body as ReadableStream).getReader()
      const dec = new TextDecoder()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = dec.decode(value)
        for (const line of chunk.split('\n')) {
          const trimmed = line.startsWith('data:') ? line.slice(5).trim() : ''
          if (!trimmed) continue
          try {
            const evt = JSON.parse(trimmed) as SyncEvent
            appendLog(evt)
          } catch { /* skip */ }
        }
      }
    } catch (e) {
      appendLog({ status: 'error', error: String(e) })
    } finally {
      setSyncing(false)
    }
  }

  const statusColor = (s: string) => {
    if (s === 'complete' || s === 'downloaded') return 'text-[#22c55e]'
    if (s === 'error') return 'text-[#f87171]'
    if (s === 'skipped') return 'text-[#3e4e63]'
    return 'text-[#7d8fa6]'
  }

  if (isLoading) {
    return <div className="p-6 text-sm text-[#3e4e63]">Loading...</div>
  }

  return (
    <div className="p-5 max-w-2xl mx-auto space-y-4">
      <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">Coros Sync</h1>

      {/* Login / Status */}
      <Card>
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent>
          {status?.authenticated ? (
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="text-[#22c55e] text-sm">●</span>
                <span className="text-sm text-[#e8edf5]">{status.email}</span>
              </div>
              {status.token_expires && (
                <p className="text-xs text-[#3e4e63]">
                  Token expires: {new Date(status.token_expires).toLocaleString()}
                </p>
              )}
              {status.last_sync && (
                <p className="text-xs text-[#3e4e63]">
                  Last sync: {new Date(status.last_sync).toLocaleString()}
                </p>
              )}
            </div>
          ) : (
            <form onSubmit={handleLogin} className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" name="email" type="email" required placeholder="you@example.com" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="password">Password</Label>
                  <Input id="password" name="password" type="password" required placeholder="••••••••" />
                </div>
              </div>
              {loginError && (
                <p className="text-xs text-[#f87171]">{loginError}</p>
              )}
              <Button type="submit" size="sm" disabled={login.isPending}>
                {login.isPending ? 'Logging in...' : 'Login'}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>

      {/* Sync controls */}
      {status?.authenticated && (
        <Card>
          <CardHeader>
            <CardTitle>Sync Workouts</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-end gap-3">
              <div className="space-y-1.5 flex-1">
                <Label htmlFor="since">Since date (optional)</Label>
                <Input
                  id="since"
                  type="date"
                  value={since}
                  onChange={e => setSince(e.target.value)}
                  placeholder="YYYY-MM-DD"
                />
                <p className="text-[10px] text-[#3e4e63]">Leave blank to sync all history</p>
              </div>
              <Button
                size="sm"
                onClick={handleSync}
                disabled={syncing}
                className="mb-6"
              >
                {syncing ? 'Syncing...' : 'Start Sync'}
              </Button>
            </div>

            {/* Log */}
            {log.length > 0 && (
              <div
                ref={logRef}
                className="bg-[#07090f] border border-[#1c2333] rounded p-3 h-72 overflow-y-auto font-mono text-[11px] space-y-0.5"
              >
                {log.map((evt, i) => (
                  <div key={i} className={`${statusColor(evt.status)} leading-relaxed`}>
                    {evt.status === 'started' && (
                      <span>▶ Sync started {evt.since} → {evt.until}</span>
                    )}
                    {evt.status === 'checking' && (
                      <span className="text-[#1c2333]">· checking {evt.activity_id} {evt.date}</span>
                    )}
                    {evt.status === 'downloaded' && (
                      <span>✓ {evt.activity_id} {evt.date} [{evt.sport}] {evt.file}</span>
                    )}
                    {evt.status === 'skipped' && (
                      <span>– {evt.activity_id} skipped ({evt.reason})</span>
                    )}
                    {evt.status === 'error' && (
                      <span>✗ {evt.activity_id ?? ''} {evt.error}</span>
                    )}
                    {evt.status === 'complete' && (
                      <span>
                        ■ Done — {evt.total_downloaded} downloaded / {evt.total_checked} checked
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
