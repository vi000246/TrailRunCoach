import { useState } from 'react'
import { useAthleteSettings, useUpdateSettings, useRecomputePmc } from '../api/hooks'
import type { PowerZone, HrZone } from '../api/client'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Input } from '../components/ui/input'
import { Label } from '../components/ui/label'
import { Button } from '../components/ui/button'
import { Separator } from '../components/ui/separator'

function ZoneRow({ z, unitKey }: {
  z: PowerZone | HrZone
  unitKey: 'min_w' | 'min_bpm'
}) {
  const maxKey = unitKey === 'min_w' ? 'max_w' : 'max_bpm'
  const unit = unitKey === 'min_w' ? 'W' : 'bpm'
  const row = z as PowerZone & HrZone
  return (
    <tr className="border-t border-[#131824]">
      <td className="py-1.5 px-3 text-xs text-[#7d8fa6]">Z{z.zone}</td>
      <td className="py-1.5 px-3 text-xs text-[#e8edf5]">{z.label}</td>
      <td className="py-1.5 px-3 text-xs text-[#7d8fa6] tabular-nums">{row[unitKey]} {unit}</td>
      <td className="py-1.5 px-3 text-xs text-[#7d8fa6] tabular-nums">
        {row[maxKey] != null ? `${row[maxKey]} ${unit}` : '∞'}
      </td>
    </tr>
  )
}

function ZonesTable({ zones, unitKey, title }: {
  zones: (PowerZone | HrZone)[]
  unitKey: 'min_w' | 'min_bpm'
  title: string
}) {
  if (!zones?.length) return null
  return (
    <div>
      <div className="text-[10px] font-medium text-[#3e4e63] uppercase tracking-wide mb-2">{title}</div>
      <table className="w-full">
        <thead>
          <tr>
            {['Zone', 'Label', 'Min', 'Max'].map(h => (
              <th key={h} className="py-1 px-3 text-left text-[10px] font-medium text-[#3e4e63] uppercase tracking-wide">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {zones.map(z => <ZoneRow key={z.zone} z={z} unitKey={unitKey} />)}
        </tbody>
      </table>
    </div>
  )
}

export function ConfigPage() {
  const { data: settings, isLoading } = useAthleteSettings()
  const update = useUpdateSettings()
  const recompute = useRecomputePmc()
  const [toast, setToast] = useState<string | null>(null)

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3000)
  }

  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    const ftpVal = fd.get('ftp') as string
    const lthrVal = fd.get('lthr') as string
    const weightVal = fd.get('weight') as string
    update.mutate(
      {
        ftp_w: ftpVal ? parseFloat(ftpVal) : undefined,
        lthr: lthrVal ? parseInt(lthrVal, 10) : undefined,
        weight_kg: weightVal ? parseFloat(weightVal) : undefined,
      },
      {
        onSuccess: () => {
          recompute.mutate()
          showToast('Saved — PMC recomputing...')
        },
        onError: () => showToast('Save failed'),
      },
    )
  }

  return (
    <div className="p-5 max-w-2xl mx-auto space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-sm font-semibold text-[#7d8fa6] uppercase tracking-wide">Config</h1>
        {toast && <span className="text-xs text-[#22c55e]">{toast}</span>}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Athlete Settings</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="text-[#3e4e63] text-sm">Loading...</div>
          ) : (
            <form key={settings?.effective_date ?? 'empty'} onSubmit={handleSubmit} className="space-y-4">
              <div className="grid grid-cols-3 gap-4">
                <div className="space-y-1.5">
                  <Label htmlFor="ftp">FTP (W)</Label>
                  <Input
                    id="ftp"
                    name="ftp"
                    type="number"
                    min={1}
                    max={600}
                    defaultValue={settings?.ftp_w ?? ''}
                    placeholder="e.g. 250"
                    className="w-full"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="lthr">LTHR (bpm)</Label>
                  <Input
                    id="lthr"
                    name="lthr"
                    type="number"
                    min={60}
                    max={220}
                    defaultValue={settings?.lthr ?? ''}
                    placeholder="e.g. 162"
                    className="w-full"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="weight">Weight (kg)</Label>
                  <Input
                    id="weight"
                    name="weight"
                    type="number"
                    step="0.1"
                    min={30}
                    max={200}
                    defaultValue={settings?.weight_kg ?? ''}
                    placeholder="e.g. 70.5"
                    className="w-full"
                  />
                </div>
              </div>
              <Button type="submit" disabled={update.isPending} size="sm">
                {update.isPending ? 'Saving...' : 'Save & Recompute PMC'}
              </Button>
            </form>
          )}
        </CardContent>
      </Card>

      {(settings?.power_zones?.length || settings?.hr_zones?.length) ? (
        <Card>
          <CardHeader>
            <CardTitle>Training Zones</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            {settings?.power_zones?.length ? (
              <ZonesTable
                zones={settings.power_zones}
                unitKey="min_w"
                title={`Power Zones — FTP ${settings.ftp_w} W`}
              />
            ) : null}
            {settings?.power_zones?.length && settings?.hr_zones?.length ? (
              <Separator />
            ) : null}
            {settings?.hr_zones?.length ? (
              <ZonesTable
                zones={settings.hr_zones}
                unitKey="min_bpm"
                title={`HR Zones — LTHR ${settings.lthr} bpm`}
              />
            ) : null}
          </CardContent>
        </Card>
      ) : null}
    </div>
  )
}
