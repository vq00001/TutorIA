import type { MaterialFile } from '../../types'
function sizeLabel(bytes: number) { return bytes >= 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB` }
export function MaterialList({ materials, onRemove }: { materials: MaterialFile[]; onRemove: (id: string) => void }) {
  if (!materials.length) return <div className="empty-materials"><span>▤</span><strong>Aún no hay materiales</strong><p>Sube tus apuntes para tenerlos a mano en tus sesiones.</p></div>
  return <div className="material-list">{materials.map((material) => <article className="material-row" key={material.id}><span className="file-icon">TXT</span><div className="material-meta"><strong>{material.name}</strong><small>{sizeLabel(material.size)} · Añadido el {material.addedAt.toLocaleDateString('es-CL')}</small></div><span className="material-ready">Listo</span><button type="button" className="icon-button" aria-label={`Quitar ${material.name}`} onClick={() => onRemove(material.id)}>×</button></article>)}</div>
}
