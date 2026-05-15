import { useState } from 'react'
import { useDashboard, useScan, useCorosStatus, useCorosLogin, useCorosSync } from '../api/hooks'
import { DashboardGrid } from '../components/DashboardGrid'
import { TabNav } from '../components/TabNav'
import { SeasonTab } from '../tabs/SeasonTab'
import { ConfigTab } from '../tabs/ConfigTab'
import { useTabStore } from '../store/tabStore'

function CorosPanel() {
  const { data: status, isLoading } = useCorosStatus()
  const login = useCorosLogin()
  const sync = useCorosSync()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  const handleLogin = (e: React.FormEvent) => {
    e.preventDefault()
    login.mutate({ email, password })
  }

  const handleSync = async () => {
    setSyncMsg('Syncing...')
    try {
      const body = await sync.mutateAsync(undefined)
      if (!body) { setSyncMsg('Done'); return }
      const reader = body.getReader()
      const dec = new TextDecoder()
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        const chunk = dec.decode(value)
        const lines = chunk.split('\n').filter(l => l.startsWith('data:'))
        for (const line of lines) {
          try {
            const evt = JSON.parse(line.slice(5))
            if (evt.status === 'complete') {
              setSyncMsg(`Done — ${evt.total_downloaded} downloaded, ${evt.total_checked} checked`)
            } else if (evt.status === 'downloading' || evt.status === 'downloaded') {
              setSyncMsg(`Downloading ${evt.activity_id}...`)
            } else if (evt.status === 'error') {
              setSyncMsg(`Error: ${evt.error}`)
            }
          } catch { /* skip malformed lines */ }
        }
      }
    } catch (e) {
      setSyncMsg(`Failed: ${e}`)
    }
  }

  if (isLoading) return null

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 mb-4">
      <h2 className="text-sm font-semibold text-gray-400 mb-3">Coros Sync</h2>
      {status?.authenticated ? (
        <div className="flex items-center gap-3 flex-wrap">
          <span className="text-xs text-green-400">● {status.email}</span>
          {status.last_sync && (
            <span className="text-xs text-gray-500">Last sync: {status.last_sync.slice(0, 10)}</span>
          )}
          <button
            onClick={handleSync}
            disabled={sync.isPending}
            className="px-3 py-1 text-xs bg-purple-700 hover:bg-purple-600 rounded transition disabled:opacity-50"
          >
            {sync.isPending ? 'Syncing...' : '↓ Sync Coros'}
          </button>
          {syncMsg && <span className="text-xs text-gray-400">{syncMsg}</span>}
        </div>
      ) : (
        <form onSubmit={handleLogin} className="flex items-end gap-2 flex-wrap">
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Email</label>
            <input
              type="email"
              value={email}
              onChange={e => setEmail(e.target.value)}
              required
              className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-48"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-xs text-gray-500">Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              required
              className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-36"
            />
          </div>
          <button
            type="submit"
            disabled={login.isPending}
            className="px-3 py-1 text-sm bg-purple-700 hover:bg-purple-600 rounded transition disabled:opacity-50"
          >
            {login.isPending ? 'Logging in...' : 'Login'}
          </button>
          {login.isError && (
            <span className="text-xs text-red-400">Login failed</span>
          )}
        </form>
      )}
    </div>
  )
}

export function Dashboard() {
  const { data: dashboard, isLoading } = useDashboard()
  const scan = useScan()
  const activeTab = useTabStore(s => s.activeTab)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-950 text-gray-400">
        Loading...
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-purple-400">WKO5 Coach</h1>
        <button
          onClick={() => scan.mutate()}
          disabled={scan.isPending}
          className="px-3 py-1 text-sm bg-gray-700 hover:bg-gray-600 rounded transition disabled:opacity-50"
        >
          {scan.isPending ? 'Scanning...' : '⟳ Scan Files'}
        </button>
      </header>
      <TabNav />
      <main className="p-4">
        {activeTab === 'season' && (
          <>
            <CorosPanel />
            <SeasonTab />
            {dashboard?.layout?.widgets && (
              <div className="mt-4">
                <DashboardGrid widgets={dashboard.layout.widgets} />
              </div>
            )}
          </>
        )}
        {activeTab === 'config' && <ConfigTab />}
        {activeTab === 'activities' && (
          <div className="flex items-center justify-center h-64 text-gray-600 text-sm">
            Activities — coming soon
          </div>
        )}
        {activeTab === 'ai' && (
          <div className="flex items-center justify-center h-64 text-gray-600 text-sm">
            AI — coming soon
          </div>
        )}
      </main>
    </div>
  )
}
