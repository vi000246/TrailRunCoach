import { useState } from 'react'
import { useAthleteSettings, useUpdateSettings, useRecomputePmc, useBackfillTss } from '../api/hooks'
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
  const backfill = useBackfillTss()
  const [toast, setToast] = useState<string | null>(null)
  const [backfillResult, setBackfillResult] = useState<string | null>(null)

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

  const handleRunLoadSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault()
    const fd = new FormData(e.currentTarget)
    const paceVal = fd.get('threshold_pace') as string
    const ctlVal = fd.get('initial_ctl') as string
    const atlVal = fd.get('initial_atl') as string
    update.mutate(
      {
        threshold_pace_s_per_km: paceVal ? parseFloat(paceVal) : undefined,
        initial_ctl_run: ctlVal ? parseFloat(ctlVal) : undefined,
        initial_atl_run: atlVal ? parseFloat(atlVal) : undefined,
      },
      {
        onSuccess: () => showToast('Run load settings saved'),
        onError: () => showToast('Save failed'),
      },
    )
  }

  const handleBackfill = () => {
    setBackfillResult(null)
    backfill.mutate(undefined, {
      onSuccess: (r) => {
        setBackfillResult(
          `完成：power TSS ${r.recomputed_power_tss} 筆，pace rTSS ${r.computed_rtss_pace} 筆，跳過 ${r.skipped_no_data} 筆（無資料），${r.skipped_already_has_tss} 筆已有 TSS`
        )
      },
      onError: () => setBackfillResult('Backfill 失敗'),
    })
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

      <Card>
        <CardHeader>
          <CardTitle>Run Training Load Settings</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <form key={`run-load-${settings?.effective_date ?? 'empty'}`} onSubmit={handleRunLoadSubmit} className="space-y-4">
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-1.5">
                <Label htmlFor="threshold_pace">閾值配速 (s/km)</Label>
                <Input
                  id="threshold_pace"
                  name="threshold_pace"
                  type="number"
                  step="0.1"
                  min={120}
                  max={600}
                  defaultValue={settings?.threshold_pace_s_per_km ?? ''}
                  placeholder="e.g. 285"
                  className="w-full"
                />
                <p className="text-[10px] text-[#3e4e63]">用於計算無功率計跑步的 rTSS</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="initial_ctl">初始 CTL（長期負荷）</Label>
                <Input
                  id="initial_ctl"
                  name="initial_ctl"
                  type="number"
                  step="0.1"
                  min={0}
                  max={300}
                  defaultValue={settings?.initial_ctl_run ?? ''}
                  placeholder="e.g. 65.0"
                  className="w-full"
                />
                <p className="text-[10px] text-[#3e4e63]">從 WKO5 複製以對齊歷史數據</p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="initial_atl">初始 ATL（短期負荷）</Label>
                <Input
                  id="initial_atl"
                  name="initial_atl"
                  type="number"
                  step="0.1"
                  min={0}
                  max={300}
                  defaultValue={settings?.initial_atl_run ?? ''}
                  placeholder="e.g. 72.0"
                  className="w-full"
                />
                <p className="text-[10px] text-[#3e4e63]">從 WKO5 複製以對齊歷史數據</p>
              </div>
            </div>
            <Button type="submit" disabled={update.isPending} size="sm">
              {update.isPending ? 'Saving...' : 'Save Run Load Settings'}
            </Button>
          </form>

          <Separator />

          <div className="space-y-2">
            <div className="text-[10px] font-medium text-[#3e4e63] uppercase tracking-wide">補算歷史 TSS</div>
            <p className="text-xs text-[#7d8fa6]">
              補算所有缺少 TSS 的跑步活動：有功率計的用 power TSS，僅 GPS 的用 pace rTSS。
            </p>
            <div className="flex items-center gap-3">
              <Button
                variant="secondary"
                size="sm"
                onClick={handleBackfill}
                disabled={backfill.isPending}
              >
                {backfill.isPending ? '計算中...' : 'Backfill TSS'}
              </Button>
              {backfillResult && (
                <span className="text-xs text-[#22c55e]">{backfillResult}</span>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
