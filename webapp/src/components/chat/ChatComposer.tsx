import {
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'

import { useAppData } from '../../contexts/AppDataContext'

import { compatible_file_formats, isSupportedFile } from '../../types'

export function ChatComposer({ courseId }: { courseId: string }) {
  const [value, setValue] = useState('')
  const [attached, setAttached] = useState<string[]>([])
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState(false)

  const input = useRef<HTMLInputElement>(null)

  const { sendPrompt, addMaterials } = useAppData()

  function submit(event: FormEvent) {
    event.preventDefault()

    if (!value.trim()) return

    sendPrompt(courseId, value.trim())
    setValue('')
    setAttached([])
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  async function attach(files: FileList | null) {
    const selected = Array.from(files ?? [])
    const accepted = selected.filter(isSupportedFile)
    const invalidFilesNotice = selected.length !== accepted.length
      ? `Formatos admitidos: ${compatible_file_formats}.`
      : ''

    if (!accepted.length) {
      setError(invalidFilesNotice)
      return
    }

    setError(invalidFilesNotice)
    setUploading(true)
    try {
      const created = await addMaterials(courseId, accepted)
      setAttached((names) => [...names, ...created.map((material) => material.name)])
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : 'No se pudieron subir los archivos.')
    } finally {
      setUploading(false)
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      {error && <p className="upload-notice" role="alert">{error}</p>}
      {attached.length > 0 && (
        <div className="attachment-chips">
          {attached.map((name, index) => (
            <span key={`${name}-${index}`}>▤ {name}</span>
          ))}
        </div>
      )}

      <textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={keyDown}
        placeholder="Pregúntale algo a tu tutor…"
        rows={3}
        aria-label="Escribe tu pregunta"
      />

      <div className="composer-toolbar">
        <div>
          <input
            ref={input}
            className="visually-hidden"
            type="file"
            accept={compatible_file_formats}
            multiple
            onChange={(event) => {
              void attach(event.target.files)
              event.currentTarget.value = ''
            }}
          />

          <button
            type="button"
            className="tool-button"
            onClick={() => input.current?.click()}
            disabled={uploading}
          >
            {uploading ? 'Subiendo…' : '＋ Adjuntar'}
          </button>

        </div>

        <button
          className="send-button"
          type="submit"
          disabled={!value.trim()}
        >
          Enviar ↑
        </button>
      </div>
    </form>
  )
}
