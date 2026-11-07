import {
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'

import { useAppData } from '../../contexts/AppDataContext'

import { compatible_file_formats } from '../../types'

export function ChatComposer({ courseId, onSend, busy=false }: { courseId: string; onSend?:(content:string)=>void;busy?:boolean }) {
  const [value, setValue] = useState('')
  const [attached, setAttached] = useState<string[]>([])

  const input = useRef<HTMLInputElement>(null)

  const { sendPrompt, addMaterials } = useAppData()



  function submit(event: FormEvent) {
    event.preventDefault()

    if (!value.trim() || busy) return

    if(onSend) onSend(value.trim())
    else sendPrompt(courseId, value.trim())
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
    const accepted = Array.from(files ?? []).filter((file) =>
      file.name.toLowerCase().endsWith('.txt'),
    )

    if (accepted.length) {
      await addMaterials(courseId, accepted)

      setAttached((names) => [
        ...names,
        ...accepted.map((file) => file.name),
      ])
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
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
          >
            ＋ Adjuntar
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
