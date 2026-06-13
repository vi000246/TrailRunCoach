import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  useCorosStatus, useCorosLogin, useCorosSync,
  useTpStatus, useTpLogin,
} from '../api/hooks'
import { SyncInventoryPanel } from '../components/SyncInventoryPanel'
import { ProviderSyncCard } from '../components/ProviderSyncCard'

export function SyncPage() {
  const qc = useQueryClient()
  const refreshInventory = () => qc.invalidateQueries({ queryKey: ['sync_inventory'] })

  // COROS
  const coros = useCorosStatus()
  const corosLogin = useCorosLogin()
  const corosSync = useCorosSync()
  const [corosErr, setCorosErr] = useState<string | null>(null)

  // TrainingPeaks
  const tp = useTpStatus()
  const tpLogin = useTpLogin()
  const [tpErr, setTpErr] = useState<string | null>(null)

  const tpSync = (since?: string): Promise<ReadableStream | null | undefined> =>
    fetch(`/api/v1/sync/start?athlete_id=1${since ? `&since=${since}` : ''}`, { method: 'POST' })
      .then(r => r.body)

  return (
    <div className="p-5 max-w-2xl mx-auto space-y-4">
      <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">同步</h1>

      <SyncInventoryPanel />

      <ProviderSyncCard
        title="COROS"
        idLabel="Email"
        authenticated={!!coros.data?.authenticated}
        accountLabel={coros.data?.email}
        lastSync={coros.data?.last_sync}
        loginPending={corosLogin.isPending}
        loginError={corosErr}
        onLogin={(id, password) => {
          setCorosErr(null)
          corosLogin.mutate({ email: id, password }, { onError: e => setCorosErr(e.message) })
        }}
        onSync={(since) => corosSync.mutateAsync(since)}
        onSynced={refreshInventory}
      />

      <ProviderSyncCard
        title="TrainingPeaks"
        idLabel="Username"
        authenticated={!!tp.data?.authenticated}
        accountLabel="TrainingPeaks"
        lastSync={tp.data?.last_sync}
        loginPending={tpLogin.isPending}
        loginError={tpErr}
        onLogin={(id, password) => {
          setTpErr(null)
          tpLogin.mutate({ username: id, password }, { onError: e => setTpErr(e.message) })
        }}
        onSync={tpSync}
        onSynced={refreshInventory}
      />
    </div>
  )
}
