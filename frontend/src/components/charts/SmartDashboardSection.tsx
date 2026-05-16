import { useNavigate } from 'react-router'
import { useDashboardSummary } from '../../api/hooks'

const TSB_COLORS: Record<string, string> = {
  fresh: 'text-green-400',
  optimal: 'text-blue-400',
  tired: 'text-yellow-400',
  overreached: 'text-red-400',
}

const TSB_LABELS: Record<string, string> = {
  fresh: '新鮮',
  optimal: '最佳',
  tired: '疲勞',
  overreached: '過度訓練',
}

function fmtDuration(s: number | null | undefined): string {
  if (!s) return '-'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

interface StatCardProps {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
}

function StatCard({ label, value, sub }: StatCardProps) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 flex flex-col gap-1">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="text-lg font-semibold text-gray-100">{value}</div>
      {sub && <div className="text-xs text-gray-500">{sub}</div>}
    </div>
  )
}

interface Props {
  athleteId?: number
}

export function SmartDashboardSection({ athleteId = 1 }: Props) {
  const navigate = useNavigate()
  const { data, isLoading } = useDashboardSummary(athleteId)

  if (isLoading) {
    return <div className="text-xs text-gray-600 py-2">Loading summary...</div>
  }
  if (!data) return null

  const tsbColor = data.tsb_state ? TSB_COLORS[data.tsb_state] : 'text-gray-300'
  const tsbLabel = data.tsb_state ? TSB_LABELS[data.tsb_state] : '-'

  const ctlTrendStr = data.ctl_trend !== null
    ? `${data.ctl_trend > 0 ? '+' : ''}${data.ctl_trend} 昨日`
    : undefined

  return (
    <div className="mb-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">訓練狀態摘要</span>
        <button
          onClick={() => navigate('/ai')}
          className="text-xs px-3 py-1 bg-blue-700 hover:bg-blue-600 rounded transition"
        >
          Ask AI Coach →
        </button>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
        <StatCard
          label="狀態 (TSB)"
          value={
            <span className={tsbColor}>
              {data.tsb !== null ? data.tsb.toFixed(1) : '-'}
            </span>
          }
          sub={tsbLabel}
        />
        <StatCard
          label="體能 (CTL)"
          value={data.ctl !== null ? data.ctl.toFixed(1) : '-'}
          sub={ctlTrendStr}
        />
        <StatCard
          label="週 TSS"
          value={data.weekly_tss}
          sub={`${data.weekly_count} 次訓練`}
        />
        <StatCard
          label="週訓練時間"
          value={`${data.weekly_hours.toFixed(1)}h`}
        />
        <StatCard
          label="最近訓練"
          value={data.last_workout?.date ?? '-'}
          sub={
            data.last_workout
              ? `${data.last_workout.sport} · ${fmtDuration(data.last_workout.duration_s)}`
              : undefined
          }
        />
      </div>
    </div>
  )
}
