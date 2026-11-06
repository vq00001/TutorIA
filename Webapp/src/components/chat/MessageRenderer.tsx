import { lazy, Suspense } from 'react'
import type { ChatMessage } from '../../types'
import { MarkdownContent } from './MarkdownContent'

const ChartCard = lazy(() => import('./ChartCard').then((module) => ({ default: module.ChartCard })))

export function MessageRenderer({ message }: { message: ChatMessage }) {
  return <div className="message-content">{message.parts.map((part, index) => part.type === 'markdown' ? <MarkdownContent key={index} content={part.content} /> : <Suspense fallback={<div className="graph-loading">Preparando gráfico…</div>} key={index}><ChartCard chart={part.chart} /></Suspense>)}{message.awaitingResponse && <div className="response-placeholder"><span className="typing-dots">•••</span> Generando respuesta con las fuentes del curso…</div>}{message.citations?.map(c => <p key={c.id}>{c.status === 'available' && c.contentUrl ? <a href={c.contentUrl}>{c.label}</a> : 'Fuente retirada'}</p>)}</div>
}
