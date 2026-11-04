import { useEffect, useId, useState } from 'react'
import mermaid from 'mermaid'

interface MermaidDiagramProps {
  source: string
}

export function MermaidDiagram({ source }: MermaidDiagramProps) {
  const rawId = useId()

  const diagramId = `mermaid-${rawId.replace(/[^a-zA-Z0-9_-]/g, '')}`

  const [svg, setSvg] = useState('')
  const [error, setError] = useState(false)

  useEffect(() => {
    let isActive = true

    // Configure Mermaid according to the current application theme.
    const theme =
      document.documentElement.dataset.theme === 'mocha'
        ? 'dark'
        : 'default'

    mermaid.initialize({
      startOnLoad: false,
      theme,
      securityLevel: 'strict',
    })

    // Render the Mermaid diagram asynchronously.
    async function renderDiagram() {
      try {
        const result = await mermaid.render(diagramId, source)

        if (isActive) {
          setSvg(result.svg)
          setError(false)
        }
      } catch {
        if (isActive) {
          setError(true)
        }
      }
    }

    void renderDiagram()

    // Prevent state updates if the component unmounts.
    return () => {
      isActive = false
    }
  }, [diagramId, source])

  // Display an error if Mermaid cannot render the diagram.
  if (error) {
    return (
      <pre className="diagram-error">
        No se pudo representar este diagrama.
      </pre>
    )
  }

  return (
    <div className="mermaid-output">
      {svg ? (
        <div dangerouslySetInnerHTML={{ __html: svg }} />
      ) : (
        <span>Preparando diagrama…</span>
      )}
    </div>
  )
}
