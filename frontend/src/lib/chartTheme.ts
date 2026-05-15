export const CHART_COLORS = {
  ctl:     '#7c3aed',
  atl:     '#ef4444',
  tsb:     '#22c55e',
  power:   '#f59e0b',
  hr:      '#ef4444',
  cadence: '#06b6d4',
  pace:    '#3b82f6',
  grid:    '#1c2333',
  axis:    '#3e4e63',
} as const

export const BASE_AXIS_PROPS = {
  tick: { fontSize: 11, fill: '#3e4e63' },
  tickLine: false,
  axisLine: false,
} as const

export const BASE_GRID_PROPS = {
  strokeDasharray: '3 3',
  stroke: '#1c2333',
} as const

export const BASE_TOOLTIP_STYLE = {
  contentStyle: {
    background: '#0e1117',
    border: '1px solid #1c2333',
    fontSize: 12,
    borderRadius: 6,
  },
  labelStyle: { color: '#7d8fa6' },
} as const
