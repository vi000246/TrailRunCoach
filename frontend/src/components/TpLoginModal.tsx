import { useState } from 'react'
import { api } from '../api/client'
import { useQueryClient } from '@tanstack/react-query'

interface Props {
  onClose: () => void
}

export function TpLoginModal({ onClose }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const qc = useQueryClient()

  const handleLogin = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await api.post('/auth/tp/login', { username, password })
      if (res.data.authenticated) {
        // Start syncing immediately after login
        await api.post('/sync/start')
        qc.invalidateQueries({ queryKey: ['workouts'] })
        qc.invalidateQueries({ queryKey: ['pmc'] })
        onClose()
      }
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Login failed'
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-80 shadow-2xl">
        <h2 className="text-lg font-semibold text-purple-400 mb-4">Connect TrainingPeaks</h2>
        <p className="text-xs text-gray-400 mb-4">
          Uses WKO5's own API connection. Credentials are sent directly to TrainingPeaks and never stored.
        </p>
        <div className="space-y-3">
          <input
            type="email"
            placeholder="TrainingPeaks email"
            value={username}
            onChange={e => setUsername(e.target.value)}
            className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleLogin()}
            className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm focus:outline-none focus:border-purple-500"
          />
          {error && <p className="text-red-400 text-xs">{error}</p>}
          <div className="flex gap-2 pt-1">
            <button
              onClick={handleLogin}
              disabled={loading || !username || !password}
              className="flex-1 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 rounded py-2 text-sm transition"
            >
              {loading ? 'Connecting...' : 'Connect'}
            </button>
            <button
              onClick={onClose}
              className="px-4 bg-gray-700 hover:bg-gray-600 rounded py-2 text-sm transition"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
