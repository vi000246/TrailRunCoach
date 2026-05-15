import type { ZoneEntry } from '../api/client'
import { Card, CardHeader, CardTitle, CardContent } from './ui/card'

function ZoneBar({ fraction }: { fraction: number }) {
  return (
    <div className="h-1.5 w-full bg-[#141922] rounded-full overflow-hidden">
      <div
        className="h-full bg-[#7c3aed] rounded-full"
        style={{ width: `${Math.min(100, fraction * 100).toFixed(1)}%` }}
      />
    </div>
  )
}

function fmtTime(s: number): string {
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

interface SectionProps {
  title: string
  zones: ZoneEntry[]
  unit: string
}

function ZoneSection({ title, zones }: SectionProps) {
  const totalTime = zones.reduce((sum, z) => sum + z.time_s, 0)
  if (!zones.length) return null

  return (
    <div>
      <div className="text-[10px] font-medium text-[#3e4e63] uppercase tracking-wide mb-2">{title}</div>
      <div className="space-y-1">
        {zones.map(z => {
          const fraction = totalTime > 0 ? z.time_s / totalTime : 0

          return (
            <div key={z.zone} className="grid grid-cols-[2rem_5rem_1fr_4rem] items-center gap-2">
              <span className="text-xs font-medium text-[#7d8fa6]">Z{z.zone}</span>
              <span className="text-xs text-[#3e4e63] truncate">{z.name}</span>
              <ZoneBar fraction={fraction} />
              <span className="text-xs text-[#7d8fa6] tabular-nums text-right">
                {fmtTime(z.time_s)}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface Props {
  powerZones: ZoneEntry[]
  hrZones: ZoneEntry[]
}

export function ZoneTable({ powerZones, hrZones }: Props) {
  const hasPower = powerZones.some(z => z.time_s > 0)
  const hasHr = hrZones.some(z => z.time_s > 0)

  if (!hasPower && !hasHr) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-48 text-[#3e4e63] text-sm">
          No zone data available
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Time in Zones</CardTitle>
      </CardHeader>
      <CardContent className="pt-0 space-y-4">
        {hasPower && <ZoneSection title="Power Zones" zones={powerZones} unit="W" />}
        {hasHr && <ZoneSection title="HR Zones" zones={hrZones} unit="bpm" />}
      </CardContent>
    </Card>
  )
}
