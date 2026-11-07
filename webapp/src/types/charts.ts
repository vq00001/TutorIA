// tipos de charts que se pueden renderizar en el chat

export interface ChartPoint {
  label: string
  [series: string]: string | number
}

export type ChartType =
  | 'line'
  | 'bar'
  | 'area'
  | 'scatter'
  | 'composed'
  | 'pie'
  | 'radar'
  | 'radialBar'
  | 'funnel'
  | 'treemap'
  | 'sankey'
  | 'sunburst'


export interface ChartSeries {
  key: string
  label: string
  color: string
  kind?: 'line' | 'bar' | 'area'
  stackId?: string
}

export interface ChartBase {
  title: string
  type: ChartType
  description?: string
}

export interface CartesianChart extends ChartBase {
  type: 'line' | 'bar' | 'area' | 'scatter' | 'composed'
  data: ChartPoint[]
  series: ChartSeries[]
  xAxisLabel?: string
  yAxisLabel?: string
  layout?: 'horizontal' | 'vertical'
  stacked?: boolean
}

export interface PieChartPayload extends ChartBase {
  type: 'pie'
  data: ChartPoint[]
  nameKey: string
  valueKey: string
  colors?: string[]
  innerRadius?: number
}

export interface RadarChartPayload extends ChartBase {
  type: 'radar'
  data: ChartPoint[]
  series: ChartSeries[]
}

export interface RadialBarChartPayload extends ChartBase {
  type: 'radialBar'
  data: ChartPoint[]
  series: ChartSeries[]
}

export interface FunnelChartPayload extends ChartBase {
  type: 'funnel'
  data: ChartPoint[]
  nameKey: string
  valueKey: string
  colors?: string[]
}

export interface TreemapNode {
  name: string
  size?: number
  children?: TreemapNode[]
}

export interface TreemapChartPayload extends ChartBase {
  type: 'treemap'
  data: TreemapNode[]
}

export interface SankeyNode {
  name: string
}

export interface SankeyLink {
  source: number
  target: number
  value: number
}

export interface SankeyChartPayload extends ChartBase {
  type: 'sankey'
  data: {
    nodes: SankeyNode[]
    links: SankeyLink[]
  }
}

export interface SunburstNode {
  name: string
  value?: number
  children?: SunburstNode[]
}

export interface SunburstChartPayload extends ChartBase {
  type: 'sunburst'
  data: SunburstNode
}

export type ChartPayload =
  | CartesianChart
  | PieChartPayload
  | RadarChartPayload
  | RadialBarChartPayload
  | FunnelChartPayload
  | TreemapChartPayload
  | SankeyChartPayload
  | SunburstChartPayload

