import { useEffect, useRef, useState } from 'react'
import Graph from 'graphology'
import Sigma from 'sigma'
import type { GraphologyEdgeExport, GraphologyExport, GraphologyNodeExport } from '../../types'

const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' ? value as Record<string, unknown> : {}
/** Reusable Sigma renderer. Placement and data fetching belong to the parent page. */
export function KnowledgeGraph({ data, className = '' }: { data: GraphologyExport; className?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    const container = ref.current
    if (!container) return
    let renderer: Sigma | undefined
    try {
      setError('')
      const graph = new Graph({ type: 'directed', multi: true })
      data.nodes.forEach((node, index) => {
        const tuple = Array.isArray(node) ? node as [string, Record<string, unknown>?] : undefined
        const object = node as GraphologyNodeExport
        const key = String(tuple?.[0] ?? object.key)
        const attrs = record(tuple?.[1] ?? object.attributes)
        const angle = 2 * Math.PI * index / Math.max(data.nodes.length, 1)
        graph.addNode(key, { ...attrs, label: String(attrs.label ?? key), x: Number(attrs.x ?? .5 + Math.cos(angle) * .35), y: Number(attrs.y ?? .5 + Math.sin(angle) * .35), size: Number(attrs.size ?? 12), color: String(attrs.color ?? '#8839ef') })
      })
      data.edges.forEach((edge, index) => {
        const tuple = Array.isArray(edge) ? edge as [string, string, string, Record<string, unknown>?] : undefined
        const object = edge as GraphologyEdgeExport
        const source = String(tuple?.[1] ?? object.source); const target = String(tuple?.[2] ?? object.target)
        const key = String(tuple?.[0] ?? object.key ?? `edge-${index}`); const attrs = record(tuple?.[3] ?? object.attributes)
        if (graph.hasNode(source) && graph.hasNode(target)) graph.addEdgeWithKey(key, source, target, { size: Number(attrs.size ?? 1.5), color: String(attrs.color ?? '#ccd0da'), ...attrs })
      })
      renderer = new Sigma(graph, container, { renderEdgeLabels: false, defaultNodeColor: '#8839ef', defaultEdgeColor: '#ccd0da', labelColor: { color: getComputedStyle(document.documentElement).getPropertyValue('--text').trim() || '#4c4f69' }, labelSize: 12, labelWeight: '500', stagePadding: 34 })
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'No se pudo cargar el grafo.') }
    return () => renderer?.kill()
  }, [data])
  return <div className={`knowledge-graph ${className}`}><div className="graph-canvas" ref={ref} />{error && <div className="graph-error">No se pudo representar el grafo. {error}</div>}</div>
}
