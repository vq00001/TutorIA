import { useRef, useState, type DragEvent } from 'react'

export function MaterialDropzone({ onFiles }: { onFiles: (files: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  function send(files: FileList | null) { if (files) onFiles(Array.from(files)) }
  function drop(event: DragEvent<HTMLDivElement>) { event.preventDefault(); setDragging(false); send(event.dataTransfer.files) }
  return <div className={`dropzone ${dragging ? 'dropzone-active' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={drop}>
    <input ref={input} type="file" accept=".txt,text/plain" multiple className="visually-hidden" onChange={(event) => { send(event.target.files); event.currentTarget.value = '' }} />
    <div className="dropzone-icon">↑</div><h2>Arrastra tus apuntes aquí</h2><p>o selecciónalos desde tu dispositivo</p><button type="button" className="secondary-button" onClick={() => input.current?.click()}>Elegir archivos</button><small>Archivos de texto · formato .txt</small>
  </div>
}
