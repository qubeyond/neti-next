import { useEffect, useRef, useState, type RefObject } from 'react'
import { PigeonIcon } from './pigeon-icon'
import { errorText } from './api'
import { Modal, displayDuration } from './ui'

export type Draft = { text: string; quote: string; files: File[] }
export const emptyDraft: Draft = { text: '', quote: '', files: [] }

type Props = {
  draft: Draft
  update: (patch: Partial<Draft>) => void
  inputRef: RefObject<HTMLTextAreaElement | null>
  disabled: boolean
  pending: boolean
  compact: boolean
  attachmentLimit?: number
  acceptFiles?: string
  onSubmit: () => void
  onCancel?: () => void
  cancelling: boolean
  onTranscribe: (audio: Blob) => Promise<void>
}

export function Composer({ draft, update, inputRef, disabled, pending, compact, attachmentLimit = 5, acceptFiles, onSubmit, onCancel, cancelling, onTranscribe }: Props) {
  const fileInput = useRef<HTMLInputElement>(null)
  const cameraInput = useRef<HTMLInputElement>(null)
  const attachmentTrigger = useRef<HTMLButtonElement>(null)
  const attachmentMenu = useRef<HTMLDivElement>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const mounted = useRef(true)
  const latestFiles = useRef(draft.files)
  const [recording, setRecording] = useState<number | null>(null)
  const [startingMic, setStartingMic] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [warning, setWarning] = useState('')
  const [transcribing, setTranscribing] = useState(false)
  const [attachmentOpen, setAttachmentOpen] = useState(false)
  const [attachmentBelow, setAttachmentBelow] = useState(false)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (recorder.current?.state === 'recording') recorder.current.stop()
      stream.current?.getTracks().forEach((track) => track.stop())
    }
  }, [])

  useEffect(() => {
    if (recording === null) return
    const timer = window.setInterval(() => {
      const duration = Date.now() - recording
      setElapsed(duration)
      if (duration >= 120000 && recorder.current?.state === 'recording') recorder.current.stop()
    }, 250)
    return () => clearInterval(timer)
  }, [recording])

  useEffect(() => { latestFiles.current = draft.files }, [draft.files])

  useEffect(() => {
    if (!attachmentOpen || compact) return
    attachmentMenu.current?.querySelector('button')?.focus()
    function dismiss(event: PointerEvent) {
      const node = event.target as Node
      if (!attachmentMenu.current?.contains(node) && !attachmentTrigger.current?.contains(node)) setAttachmentOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape') closeAttachments()
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape)
    }
  }, [attachmentOpen, compact])

  useEffect(() => {
    const input = inputRef.current
    if (!input) return
    input.style.height = 'auto'
    input.style.height = Math.min(input.scrollHeight, 168) + 'px'
  }, [draft.text, inputRef])

  function attach(added: File[]) {
    const current = latestFiles.current
    const valid = added.filter((file) => file.size > 0 && file.size <= 16 * 1024 * 1024)
    const next = [...current, ...valid].slice(0, attachmentLimit)
    latestFiles.current = next
    update({ files: next })
    setWarning(valid.length !== added.length ? 'Файл должен быть непустым и не больше 16 МБ.' :
      current.length + valid.length > attachmentLimit ?
        attachmentLimit === 1 ? 'Для трассировки можно прикрепить один файл.' : 'К сообщению можно прикрепить до пяти файлов.' :
        '')
  }

  function closeAttachments() {
    setAttachmentOpen(false)
    window.requestAnimationFrame(() => attachmentTrigger.current?.focus())
  }

  async function toggleRecording() {
    if (recorder.current?.state === 'recording') {
      recorder.current.stop()
      return
    }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setWarning('Запись аудио доступна в браузере через HTTPS или localhost. Можно прикрепить готовый аудиофайл.')
      return
    }
    setWarning('')
    setStartingMic(true)
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!mounted.current) {
        media.getTracks().forEach((track) => track.stop())
        return
      }
      stream.current = media
      const device = new MediaRecorder(media)
      recorder.current = device
      const chunks: Blob[] = []
      device.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data) }
      device.onstop = () => {
        media.getTracks().forEach((track) => track.stop())
        recorder.current = null
        stream.current = null
        if (!mounted.current) return
        setRecording(null)
        if (!chunks.length) {
          setWarning('Аудио не записалось. Попробуйте ещё раз.')
          return
        }
        const mime = (device.mimeType || chunks[0]?.type || 'audio/webm').split(';')[0]
        setTranscribing(true)
        void onTranscribe(new Blob(chunks, { type: mime })).catch((cause: unknown) => {
          if (mounted.current) setWarning(errorText(cause))
        }).finally(() => {
          if (mounted.current) setTranscribing(false)
        })
      }
      device.start(500)
      setElapsed(0)
      setRecording(Date.now())
    } catch {
      stream.current?.getTracks().forEach((track) => track.stop())
      stream.current = null
      if (mounted.current) setWarning('Микрофон недоступен. Проверьте разрешения браузера.')
    } finally {
      if (mounted.current) setStartingMic(false)
    }
  }

  const exceedsTextLimit = Array.from(draft.text).length > 2000
  const attachmentsDisabled = disabled || draft.files.length >= attachmentLimit
  const submitEnabled = !disabled && !pending && recording === null && !startingMic && !transcribing && !exceedsTextLimit && Boolean(draft.text.trim() || draft.files.length)
  const attachmentOptions = <>
    <button type="button" className="soft-button" disabled={attachmentsDisabled} onClick={() => { fileInput.current?.click(); closeAttachments() }}>
      <PigeonIcon name="file"/> Загрузить с устройства
    </button>
    <button type="button" className="soft-button" disabled={attachmentsDisabled} onClick={() => { cameraInput.current?.click(); closeAttachments() }}>
      <PigeonIcon name="camera"/> Сделать фото
    </button>
  </>

  return (
    <div className="composer-area">
      <form className="composer" onSubmit={(event) => { event.preventDefault(); if (submitEnabled) onSubmit() }}
        onDragOver={(event) => { if (!disabled && event.dataTransfer.types.includes('Files')) event.preventDefault() }}
        onDrop={(event) => { if (!disabled && event.dataTransfer.files.length) { event.preventDefault(); attach(Array.from(event.dataTransfer.files)) } }}>
        {draft.quote && <div className="draft-quote"><blockquote>{draft.quote}</blockquote><button type="button" className="icon-button" disabled={disabled} aria-label="Убрать цитату" onClick={() => update({ quote: '' })}><PigeonIcon name="close"/></button></div>}
        {!!draft.files.length && <div className="draft-files">
          {draft.files.map((file, index) => <span className="draft-file" key={file.name + index}><PigeonIcon name="file"/>
            <span>{file.name}</span><button type="button" className="mini-button" disabled={disabled} aria-label={'Убрать ' + file.name} onClick={() => update({ files: draft.files.filter((_, item) => item !== index) })}><PigeonIcon name="close"/></button>
          </span>)}
        </div>}
        <textarea ref={inputRef} aria-label="Сообщение агенту" placeholder="Задайте вопрос о вузе…" value={draft.text} rows={2}
          onChange={(event) => update({ text: event.target.value })}
          onKeyDown={(event) => {
            if (event.key !== 'Enter' || event.shiftKey || event.nativeEvent.isComposing) return
            event.preventDefault()
            if (submitEnabled) onSubmit()
          }} />
        <div className="composer-controls">
          <div className="compose-tools attachment-anchor">
            <input hidden multiple={attachmentLimit > 1} ref={fileInput} type="file" aria-label="Файлы" accept={acceptFiles} onChange={(event) => { attach(Array.from(event.target.files ?? [])); event.target.value = '' }} />
            <input hidden ref={cameraInput} type="file" accept="image/jpeg,image/png,image/webp" capture="environment" aria-label="Фото документа"
              onChange={(event) => {
                const photo = event.target.files?.[0]
                if (photo) {
                  attach([photo])
                  if (photo.size > 0 && photo.size <= 16 * 1024 * 1024 && !draft.text.trim()) update({ text: 'Что указано на фото?' })
                }
                event.target.value = ''
              }} />
            <button ref={attachmentTrigger} type="button" className="icon-button tool-button" disabled={attachmentsDisabled}
              aria-label="Прикрепить файлы" title="Прикрепить файлы" aria-haspopup={compact ? 'dialog' : undefined} aria-expanded={attachmentOpen}
              onClick={(event) => {
                if (attachmentOpen) { closeAttachments(); return }
                setAttachmentBelow(window.innerHeight - event.currentTarget.getBoundingClientRect().bottom >= 104)
                setAttachmentOpen(true)
              }}><PigeonIcon name="attach"/></button>
            {attachmentOpen && !compact && <div ref={attachmentMenu} className={'attachment-options attachment-dropdown' + (attachmentBelow ? ' attachment-below' : '')} role="group" aria-label="Прикрепить файл"
              onKeyDown={(event) => {
                if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
                event.preventDefault()
                const options = [...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
                const current = options.findIndex((option) => option === document.activeElement)
                const delta = event.key === 'ArrowDown' ? 1 : -1
                options[(current + delta + options.length) % options.length]?.focus()
              }}
              onBlur={(event) => {
                if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget) && event.relatedTarget !== attachmentTrigger.current) setAttachmentOpen(false)
              }}>{attachmentOptions}</div>}
          </div>
          <div className="compose-tools">
            <button type="button" className={'icon-button tool-button' + (recording !== null ? ' recording' : '')} disabled={disabled || startingMic || transcribing}
              aria-label={recording === null ? 'Записать аудио' : 'Остановить запись'} title={recording === null ? 'Записать аудио' : 'Остановить запись'} onClick={() => { void toggleRecording() }}>
              {recording === null ? <PigeonIcon name="mic"/> : <PigeonIcon name="stop"/>}
            </button>
            {recording !== null && <span className="mic-timer"><PigeonIcon name="waveform"/> {displayDuration(elapsed)}</span>}
            {onCancel ? <button className="send-button stop-button" type="button" aria-label="Остановить ответ" title="Остановить ответ"
              disabled={cancelling} onClick={onCancel}><PigeonIcon name="stop"/></button> :
              <button className="send-button" type="submit" aria-label="Отправить сообщение" disabled={!submitEnabled}>{disabled ? <span className="spinner" aria-hidden="true"/> : <PigeonIcon name="send"/>}</button>}
          </div>
        </div>
      </form>
      {attachmentOpen && compact && <Modal title="Прикрепить файл" className="attachment-modal" onClose={closeAttachments}>
        <div className="attachment-options">{attachmentOptions}</div>
      </Modal>}
      <p className={'composer-note' + (warning ? ' warning' : '')} role={warning ? 'alert' : undefined}>
        {warning || (exceedsTextLimit ? 'Сократите текст до 2000 символов, чтобы отправить сообщение.' : transcribing ? 'Расшифровываем запись…' :
          'Пигеон может ошибаться. Проверяйте важную информацию.')}
      </p>
    </div>
  )
}
