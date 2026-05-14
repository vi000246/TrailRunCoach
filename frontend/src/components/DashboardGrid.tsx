import { useState } from 'react'
import GridLayout from 'react-grid-layout'
import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'
import { PmcWidget } from './widgets/PmcWidget'
import { MmpCurveWidget } from './widgets/MmpCurveWidget'
import { WorkoutListWidget } from './widgets/WorkoutListWidget'

interface WidgetConfig {
  id: string
  type: string
  position: { col: number; row: number; w: number; h: number }
  config: Record<string, unknown>
}

function renderWidget(
  w: WidgetConfig,
  selectedWorkout: number | null,
  onSelect: (id: number) => void,
) {
  switch (w.type) {
    case 'pmc_chart':
      return <PmcWidget />
    case 'mmp_curve':
      return <MmpCurveWidget workoutId={selectedWorkout} />
    case 'workout_list':
      return <WorkoutListWidget lastN={(w.config.last_n as number) ?? 10} onSelect={onSelect} />
    default:
      return <div className="text-gray-500 text-sm p-2">Unknown widget: {w.type}</div>
  }
}

export function DashboardGrid({ widgets }: { widgets: WidgetConfig[] }) {
  const [selectedWorkout, setSelectedWorkout] = useState<number | null>(null)
  const layout = widgets.map(w => ({
    i: w.id,
    x: w.position.col,
    y: w.position.row,
    w: w.position.w,
    h: w.position.h,
    minW: 3,
    minH: 2,
  }))

  return (
    <GridLayout
      className="layout"
      layout={layout}
      cols={12}
      rowHeight={80}
      width={1200}
      draggableHandle=".drag-handle"
    >
      {widgets.map(w => (
        <div
          key={w.id}
          className="bg-gray-900 border border-gray-700 rounded-lg overflow-hidden flex flex-col"
        >
          <div className="drag-handle h-6 bg-gray-800 flex items-center px-2 cursor-grab">
            <span className="text-xs text-gray-400">{w.type.replace(/_/g, ' ')}</span>
          </div>
          <div className="flex-1 p-2 min-h-0">
            {renderWidget(w, selectedWorkout, setSelectedWorkout)}
          </div>
        </div>
      ))}
    </GridLayout>
  )
}
