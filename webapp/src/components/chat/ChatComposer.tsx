import {
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'

import { useAppData } from '../../contexts/AppDataContext'

import { compatible_file_formats } from '../../types'

export function ChatComposer({ courseId }: { courseId: string }) {
  const [value, setValue] = useState('')
  const [attached, setAttached] = useState<File[]>([])
  const [isSending, setIsSending] = useState(false)

  const input = useRef<HTMLInputElement>(null)
  const sending = useRef(false)

  const { sendPrompt } = useAppData()

  async function submit(event: FormEvent) {
    event.preventDefault()

    if (!value.trim() || sending.current) return

    sending.current = true
    setIsSending(true)
    try {
      await sendPrompt(courseId, value.trim(), attached)
      setValue('')
      setAttached([])
    } finally {
      sending.current = false
      setIsSending(false)
    }
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  async function attach(files: FileList | null) {
    const accepted = Array.from(files ?? []).filter((file) =>
      /\.(txt|md)$/i.test(file.name),
    )

    if (accepted.length) {
      setAttached((current) => [...current, ...accepted])
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      {attached.length > 0 && (
        <div className="attachment-chips">
          {attached.map((file, index) => (
            <span key={`${file.name}-${index}`}>▤ {file.name}</span>
          ))}
        </div>
      )}

      <textarea
        value={value}
        disabled={isSending}
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
            disabled={isSending}
            onChange={(event) => {
              void attach(event.target.files)
              event.currentTarget.value = ''
            }}
          />

          <button
            type="button"
            className="tool-button"
            onClick={() => input.current?.click()}
          >
            ＋ Adjuntar
          </button>

        </div>

        <button
          className="send-button"
          type="submit"
          disabled={!value.trim() || isSending}
        >
          {isSending ? 'Enviando…' : 'Enviar ↑'}
        </button>
      </div>
    </form>
  )
}
