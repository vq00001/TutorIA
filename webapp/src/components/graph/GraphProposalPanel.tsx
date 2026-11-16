import { useState, type FormEvent } from 'react'
import { applyCourseGraph, previewCourseGraph, type GraphProposal } from '../../services/databaseApi'

export function GraphProposalPanel({ courseId }: { courseId: string }) {
  const [source, setSource] = useState<{ title: string; text: string } | null>(null)
  const [target, setTarget] = useState('')
  const [proposal, setProposal] = useState<GraphProposal | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function generate(event: FormEvent) {
    event.preventDefault()
    if (!source) return
    if (!source.text.trim() || source.text.length > 200_000) {
      setError('El texto debe contener entre 1 y 200 000 caracteres.')
      return
    }
    setBusy(true); setError(''); setProposal(null)
    try {
      setProposal(await previewCourseGraph(courseId, source.text, source.title, target ? Number(target) : null))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo generar el grafo.')
    } finally { setBusy(false) }
  }

  async function apply() {
    if (!proposal) return
    setBusy(true); setError('')
    try {
      await applyCourseGraph(courseId, proposal)
      window.location.reload()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'No se pudo guardar el grafo.')
      setBusy(false)
    }
  }

  return <section className="graph-proposal">
    <h2>Proponer grafo inicial</h2>
    <p>Sube un archivo de texto o Markdown. Revisa los conceptos y relaciones antes de guardarlos en el curso.</p>
    <form onSubmit={(event) => { void generate(event) }}>
      <label>Fuente (.txt o .md) <input type="file" accept=".txt,.md,text/plain,text/markdown" required onChange={(event) => {
        const file = event.target.files?.[0]
        setProposal(null); setError(''); setSource(null)
        if (!file) return
        if (!/\.(txt|md)$/i.test(file.name) || file.size > 800_000) {
          setError('Elige un archivo .txt o .md de hasta 800 KB.')
          return
        }
        void file.text().then((text) => setSource({ title: file.name, text }))
      }} /></label>
      <label>Conceptos aproximados (opcional) <input type="number" min="1" max="400" value={target} onChange={(event) => setTarget(event.target.value)} /></label>
      <button type="submit" disabled={busy || !source}>{busy ? 'Generando propuesta…' : 'Generar propuesta'}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {proposal && <div>
      <h3>Propuesta: {proposal.nodes.length} conceptos y {proposal.edges.length} relaciones</h3>
      <ol>{proposal.nodes.map((node) => <li key={node.ref}><strong>{node.ref}: {node.label}</strong> — {node.description}</li>)}</ol>
      <h3>Dependencias propuestas</h3>
      <ol>{proposal.edges.map((edge, index) => <li key={`${edge.sourceRef}-${edge.targetRef}-${index}`}>{edge.sourceRef} → {edge.targetRef}: {edge.reason}</li>)}</ol>
      <button type="button" disabled={busy} onClick={() => { void apply() }}>Guardar esta propuesta</button>
    </div>}
  </section>
}
