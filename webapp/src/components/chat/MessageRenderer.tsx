import { lazy, Suspense } from 'react'
import type { ChatMessage } from '../../types'
import { MarkdownContent } from './MarkdownContent'

const ChartCard = lazy(() =>
  import('./ChartCard').then((module) => ({
    default: module.ChartCard,
  })),
)


export function MessageRenderer({
  message,
}: {
  message: ChatMessage
}) {
  return (
    <div className={`message-content${message.isError ? ' message-error' : ''}`}>
      {message.parts.map((part, index) =>
        part.type === 'markdown' ? (
          <MarkdownContent
            key={index}
            content={part.content}
          />
        ) : (
          <Suspense
            key={index}
            fallback={
              <div className="graph-loading">
                Preparando gráfico…
              </div>
            }
          >
            <ChartCard chart={part.chart} />
          </Suspense>
        ),
      )}

      {message.sourceDocuments && message.sourceDocuments.length > 0 && (
        <details className="message-sources">
          <summary>Fuentes utilizadas ({message.sourceDocuments.length})</summary>
          <ul>
            {message.sourceDocuments.map((source) => (
              <li key={`${source.documentId}-${source.section ?? ''}`}>
                <strong>{source.name}</strong>
                {source.section && <span> · {source.section}</span>}
                {source.snippet && <blockquote>{source.snippet}</blockquote>}
              </li>
            ))}
          </ul>
        </details>
      )}

      {message.awaitingResponse && (
        <div className="response-placeholder">
          <span className="typing-dots">•••</span>
          El tutor está escribiendo…
        </div>
      )}
    </div>
  )
}
