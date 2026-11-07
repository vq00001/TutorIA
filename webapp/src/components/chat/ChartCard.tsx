import { useEffect, useRef, useState } from 'react'
import {
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  Line,
  Bar,
  Area,
  Scatter,
  Pie,
  Radar,
  RadialBar,
  Funnel,
  Cell,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  LineChart,
  BarChart,
  AreaChart,
  ScatterChart,
  ComposedChart,
  PieChart,
  RadarChart,
  RadialBarChart,
  FunnelChart,
  Treemap,
  Sankey,
  SunburstChart,
} from 'recharts'

import type {
  ChartPayload,
  CartesianChart,
  PieChartPayload,
  RadarChartPayload,
  RadialBarChartPayload,
  FunnelChartPayload,
  TreemapChartPayload,
} from '../../types'


function CartesianRenderer({ chart, width, height, }: { chart: CartesianChart; width: number; height: number }) {
  const series = chart.series.map((item) => {
    const commonProps = {
      key: item.key,
      dataKey: item.key,
      name: item.label,
      stroke: item.color,
      fill: item.color,
      stackId: chart.stacked ? item.stackId ?? 'stack' : undefined,
    }

    switch (chart.type) {
      case 'line':
        return (
          <Line
            {...commonProps}
            type="monotone"
            strokeWidth={2}
            dot={{ r: 3 }}
          />
        )

      case 'bar':
        return (
          <Bar
            {...commonProps}
            radius={[4, 4, 0, 0]}
          />
        )

      case 'area':
        return (
          <Area
            {...commonProps}
            type="monotone"
            fillOpacity={0.25}
          />
        )

      case 'scatter':
        return (
          <Scatter
            {...commonProps}
          />
        )

      case 'composed':
        switch (item.kind ?? 'line') {
          case 'bar':
            return <Bar {...commonProps} />

          case 'area':
            return <Area {...commonProps} fillOpacity={0.2} />

          default:
            return <Line {...commonProps} type="monotone" />
        }

      default:
        return null
    }
  })

  const props = {
    width,
    height,
    data: chart.data,
    layout: chart.layout ?? 'horizontal' as const,
    margin: { top: 20, right: 20, bottom: 20, left: 10 },
  }

  switch (chart.type) {
    case 'line':
      return <LineChart {...props}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" /><XAxis dataKey="label" stroke="var(--text-muted)" /><YAxis stroke="var(--text-muted)" /><Tooltip /><Legend />{series}</LineChart>

    case 'bar':
      return <BarChart {...props}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" /><XAxis dataKey="label" stroke="var(--text-muted)" /><YAxis stroke="var(--text-muted)" /><Tooltip /><Legend />{series}</BarChart>

    case 'area':
      return <AreaChart {...props}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" /><XAxis dataKey="label" stroke="var(--text-muted)" /><YAxis stroke="var(--text-muted)" /><Tooltip /><Legend />{series}</AreaChart>

    case 'scatter':
      return <ScatterChart {...props}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" /><XAxis dataKey="x" type="number" name={chart.xAxisLabel ?? 'x'} stroke="var(--text-muted)" /><YAxis dataKey="y" type="number" name={chart.yAxisLabel ?? 'y'} stroke="var(--text-muted)" /><Tooltip cursor={{ strokeDasharray: '3 3' }} />{series}</ScatterChart>

    case 'composed':
      return <ComposedChart {...props}><CartesianGrid stroke="var(--border)" strokeDasharray="3 3" /><XAxis dataKey="label" stroke="var(--text-muted)" /><YAxis stroke="var(--text-muted)" /><Tooltip /><Legend />{series}</ComposedChart>
  }
}


function PieRenderer({ chart, width, height }: { chart: PieChartPayload; width: number; height: number }) {
  return (
    <PieChart width={width} height={height}>
      <Tooltip />
      <Legend />

      <Pie
        data={chart.data}
        dataKey={chart.valueKey}
        nameKey={chart.nameKey}
        innerRadius={chart.innerRadius ?? 0}
        outerRadius="80%"
        paddingAngle={2}
      >
        {chart.data.map((_, index) => (
          <Cell
            key={index}
            fill={
              chart.colors?.[index % chart.colors.length] ??
              `hsl(${index * 45}, 65%, 55%)`
            }
          />
        ))}
      </Pie>
    </PieChart>
  )
}

function RadarRenderer({ chart, width, height, }: { chart: RadarChartPayload; width: number; height: number }) {
  return (
    <RadarChart width={width} height={height} data={chart.data}>
      <PolarGrid />
      <PolarAngleAxis dataKey="label" />
      <PolarRadiusAxis />
      <Tooltip />
      <Legend />

      {chart.series.map((item) => (
        <Radar
          key={item.key}
          name={item.label}
          dataKey={item.key}
          stroke={item.color}
          fill={item.color}
          fillOpacity={0.3}
        />
      ))}
    </RadarChart>
  )
}

function RadialBarRenderer({ chart, width, height, }: { chart: RadialBarChartPayload; width: number; height: number }) {
  return (
    <RadialBarChart
      width={width}
      height={height}
      data={chart.data}
      innerRadius="20%"
      outerRadius="90%"
      startAngle={90}
      endAngle={-270}
    >
      <Tooltip />
      <Legend />

      {chart.series.map((item) => (
        <RadialBar
          key={item.key}
          name={item.label}
          dataKey={item.key}
          fill={item.color}
          background
        />
      ))}
    </RadialBarChart>
  )
}

function FunnelRenderer({ chart, width, height, }: { chart: FunnelChartPayload; width: number; height: number }) {
  return (
    <FunnelChart width={width} height={height}>
      <Tooltip />

      <Funnel
        data={chart.data}
        dataKey={chart.valueKey}
        nameKey={chart.nameKey}
        isAnimationActive
      >
        {chart.data.map((_, index) => (
          <Cell
            key={index}
            fill={
              chart.colors?.[index % chart.colors.length] ??
              `hsl(${index * 45}, 65%, 55%)`
            }
          />
        ))}
      </Funnel>
    </FunnelChart>
  )
}


function TreemapRenderer({
  chart,
  width,
  height,
}: {
  chart: TreemapChartPayload
  width: number
  height: number
}) {
  return (
    <Treemap
      width={width}
      height={height}
      data={chart.data}
      dataKey="size"
      nameKey="name"
      type="flat"
      aspectRatio={4 / 3}
      stroke="var(--surface)"
      fill="var(--mauve)"
    />
  )
}

function SankeyRenderer({ chart, width, height, }: { chart: Extract<ChartPayload, { type: 'sankey' }>; width: number; height: number }) {
  return (
    <Sankey
      width={width}
      height={height}
      data={chart.data}
      nodePadding={20}
      link={{ stroke: 'var(--mauve)' }}
    >
      <Tooltip />
    </Sankey>
  )
}

function SunburstRenderer({ chart, width, height, }: { chart: Extract<ChartPayload, { type: 'sunburst' }>; width: number; height: number }) {
  return (
    <SunburstChart
      data={chart.data}
      width={width}
      height={height}
      fill="var(--mauve)"
    />
  )
}


export function ChartCard({ chart, }: { chart: ChartPayload }) {
  const chartAreaRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 0, height: 190 })

  useEffect(() => {
    const element = chartAreaRef.current
    if (!element) return

    const observer = new ResizeObserver(([entry]) => {
      setSize({
        width: Math.floor(entry.contentRect.width),
        height: Math.floor(entry.contentRect.height),
      })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  function renderChart() {
    switch (chart.type) {
      case 'line':
      case 'bar':
      case 'area':
      case 'scatter':
      case 'composed':
        return size.width > 0 ? <CartesianRenderer chart={chart} width={size.width} height={size.height} /> : null

      case 'pie':
        return size.width > 0 ? <PieRenderer chart={chart} width={size.width} height={size.height} /> : null

      case 'radar':
        return size.width > 0 ? <RadarRenderer chart={chart} width={size.width} height={size.height} /> : null

      case 'radialBar':
        return size.width > 0 ? <RadialBarRenderer chart={chart} width={size.width} height={size.height} /> : null

      case 'funnel':
        return size.width > 0 ? <FunnelRenderer chart={chart} width={size.width} height={size.height} /> : null

      case 'treemap':
        return size.width > 0 ? <TreemapRenderer chart={chart} width={size.width} height={size.height} /> : null

      case 'sankey':
        return size.width > 0 ? <SankeyRenderer chart={chart} width={size.width} height={size.height} /> : null

      case 'sunburst':
        return size.width > 0 ? <SunburstRenderer chart={chart} width={size.width} height={size.height} /> : null

      default:
        return null
    }
  }

  return (
    <figure className="chart-card">
      <figcaption>
        <strong>{chart.title}</strong>

        {chart.description && (
          <small>{chart.description}</small>
        )}
      </figcaption>

      <div className="chart-area" ref={chartAreaRef}>
        {renderChart() ?? <p>Unsupported chart type.</p>}
      </div>
    </figure>
  )
}

