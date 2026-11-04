import { useEffect, useId, useState } from 'react'
import mermaid from 'mermaid'

export function MermaidDiagram({ source }: { source: string }) {
  const rawId = useId()
  const id = `mermaid-${rawId.replace(/[^a-zA-Z0-9_-]/g, '')}`
  const [svg, setSvg] = useState('')
  const [error, setError] = useState(false)
  useEffect(() => {
    let active = true
    mermaid.initialize({ startOnLoad: false, theme: document.documentElement.dataset.theme === 'mocha' ? 'dark' : 'default', securityLevel: 'strict' })
    void mermaid.render(id, source).then((result) => { if (active) setSvg(result.svg) }).catch(() => { if (active) setError(true) })
    return () => { active = false }
  }, [id, source])
  if (error) return <pre className="diagram-error">No se pudo representar este diagrama.</pre>
  return <div className="mermaid-output">{svg ? <div dangerouslySetInnerHTML={{ __html: svg }} /> : <span>Preparando diagrama…</span>}</div>
}
