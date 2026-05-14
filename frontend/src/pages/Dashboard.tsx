import { useState } from 'react'
import { useDashboard, useScan, useSync } from '../api/hooks'
import { DashboardGrid } from '../components/DashboardGrid'
import { TpLoginModal } from '../components/TpLoginModal'

export function Dashboard() {
  const { data: dashboard, isLoading } = useDashboard()
  const scan = useScan()
  const sync = useSync()
  const [showLogin, setShowLogin] = useState(false)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-950 text-gray-400">
        Loading...
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      {showLogin && <TpLoginModal onClose={() => setShowLogin(false)} />}
      <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-purple-400">WKO5 Coach</h1>
        <div className="flex gap-3">
          <button
            onClick={() => scan.mutate()}
            disabled={scan.isPending}
            className="px-3 py-1 text-sm bg-gray-700 hover:bg-gray-600 rounded transition disabled:opacity-50"
          >
            {scan.isPending ? 'Scanning...' : '⟳ Scan Files'}
          </button>
          <button
            onClick={() => sync.isPending ? null : setShowLogin(true)}
            disabled={sync.isPending}
            className="px-3 py-1 text-sm bg-blue-700 hover:bg-blue-600 rounded transition disabled:opacity-50"
          >
            {sync.isPending ? 'Syncing...' : '↓ Sync TrainingPeaks'}
          </button>
        </div>
      </header>
      <main className="p-4">
        {dashboard?.layout?.widgets && (
          <DashboardGrid widgets={dashboard.layout.widgets} />
        )}
      </main>
    </div>
  )
}
