import { useParams, useNavigate } from 'react-router'
import { ChevronLeft } from 'lucide-react'
import { useWorkout, useTimeseries, useZones, useWorkoutMmp } from '../api/hooks'
import { MetricCard } from '../components/MetricCard'
import { TimeseriesChart } from '../components/charts/TimeseriesChart'
import { MmpCurveChart } from '../components/charts/MmpCurveChart'
import { ZoneTable } from '../components/ZoneTable'
import { Button } from '../components/ui/button'

function fmt(s?: number | null): string {
  if (!s) return '—'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}

export function ActivityDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const workoutId = parseInt(id!)

  const { data: workout, isLoading: wLoading } = useWorkout(workoutId)
  const { data: ts } = useTimeseries(workoutId)
  const { data: zones } = useZones(workoutId)
  const { data: mmpData } = useWorkoutMmp(workoutId)

  if (wLoading || !workout) {
    return (
      <div className="flex items-center justify-center h-64 text-[#3e4e63] text-sm">
        Loading...
      </div>
    )
  }

  const metrics = workout.metrics || {}
  const np = metrics.np || metrics.avg_power
  const tss = metrics.tss
  const avgHr = metrics.avg_hr

  return (
    <div className="p-5 max-w-6xl mx-auto space-y-4">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={() => navigate('/activities')} className="gap-1">
          <ChevronLeft className="h-4 w-4" />
          Activities
        </Button>
        <span className="text-[#3e4e63]">/</span>
        <span className="text-sm text-[#7d8fa6]">
          {workout.date} · {workout.sport}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <MetricCard label="Duration" value={fmt(workout.duration_s)} />
        <MetricCard label="Norm Power" value={np ? `${Math.round(np)}` : '—'} unit="W" accent />
        <MetricCard label="TSS" value={tss ? Math.round(tss) : '—'} />
        <MetricCard label="Avg HR" value={avgHr ? Math.round(avgHr) : '—'} unit="bpm" />
        <MetricCard label="Source" value={workout.source} />
      </div>

      {ts && <TimeseriesChart data={ts} />}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {mmpData?.curve && Object.keys(mmpData.curve).length > 0 && (
          <MmpCurveChart curve={mmpData.curve} />
        )}
        {zones && (
          <ZoneTable powerZones={zones.power_zones} hrZones={zones.hr_zones} />
        )}
      </div>
    </div>
  )
}
