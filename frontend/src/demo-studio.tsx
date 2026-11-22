import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { api, errorText, type DemoTurn } from './api'
import { PigeonIcon } from './pigeon-icon'
import { TraceWorkspace, type TraceEnd, type TraceEvent, type TraceLayout, type TraceSnapshot, type TraceSpan } from './trace-workspace'
import './trace-workspace.css'

export default function DemoStudio({ account, layout = 'timeline', fixture, controls, execute = api.demoRun }: {
  account: string
  layout?: TraceLayout
  fixture?: TraceSnapshot
  controls?: ReactNode
  execute?: typeof api.demoRun
}) {
  return <main className="trace-studio" data-layout={layout}>
    <header className="trace-masthead">
      <div className="trace-topline">
        <a href={fixture ? '/' : '#'} className="trace-back"><PigeonIcon name="next"/>{fixture ? 'Пигеон' : 'Кабинет'}</a>
        <span>{account}</span>
      </div>
      <div className="trace-title-row">
        <h1>Трассировка запроса</h1>
        {controls}
      </div>
    </header>
    <TraceSession key={fixture?.requestID} fixture={fixture} layout={layout} execute={execute}/>
  </main>
}

function TraceSession({ fixture, layout, execute }: {
  fixture?: TraceSnapshot
  layout: TraceLayout
  execute: typeof api.demoRun
}) {
  const [composerOpen, setComposerOpen] = useState(!fixture)
  const [question, setQuestion] = useState('')
  const [history, setHistory] = useState<DemoTurn[]>([])
  const [file, setFile] = useState<File | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const [photo, setPhoto] = useState('')
  const photoURL = useRef('')
  const [spans, setSpans] = useState<TraceSpan[]>(fixture?.spans ?? [])
  const [answer, setAnswer] = useState<TraceEnd | undefined>(fixture?.answer)
  const [requestID, setRequestID] = useState(fixture?.requestID ?? '')
  const [error, setError] = useState(fixture?.error ?? '')
  const [elapsed, setElapsed] = useState(fixture?.elapsed ?? 0)
  const [lastQuestion, setLastQuestion] = useState(fixture?.question ?? '')
  const [attempt, setAttempt] = useState(0)
  const [running, setRunning] = useState(false)
  const active = useRef<AbortController | null>(null)

  useEffect(() => () => {
    active.current?.abort()
    if (photoURL.current) URL.revokeObjectURL(photoURL.current)
  }, [])
  useEffect(() => {
    if (!running) return
    const started = performance.now()
    const timer = window.setInterval(() => setElapsed(Math.round(performance.now() - started)), 100)
    return () => window.clearInterval(timer)
  }, [running])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (active.current || (!question.trim() && !file)) return
    const controller = new AbortController()
    active.current = controller
    setSpans([])
    setAttempt((previous) => previous + 1)
    setLastQuestion(question.trim() || file?.name || '')
    setAnswer(undefined)
    setRequestID('')
    setError('')
    setElapsed(0)
    setRunning(true)
    if (photoURL.current) URL.revokeObjectURL(photoURL.current)
    photoURL.current = file?.type.startsWith('image/') ? URL.createObjectURL(file) : ''
    setPhoto(photoURL.current)
    let terminal = false
    try {
      const response = await execute(question.trim(), history, file, controller.signal)
      const stream = response.body?.pipeThrough(new TextDecoderStream()).getReader()
      if (!stream) throw new Error('Empty response')
      let pending = ''
      while (!terminal) {
        const chunk = await stream.read()
        if (chunk.done) break
        pending += chunk.value
        const lines = pending.split('\n')
        pending = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.trim()) continue
          const update = JSON.parse(line) as TraceEvent
          setRequestID(update.request_id)
          switch (update.type) {
            case 'span_start':
              setSpans((previous) => [...previous, update])
              break
            case 'span_end':
              setSpans((previous) => previous.map((span) => span.id === update.id ? { ...span, ...update, at_ms: span.at_ms } : span))
              break
            case 'answer': {
              setAnswer(update)
              const spoken = update.answer.artifacts?.find((item) => item.title.startsWith('Расшифровка · '))?.text
              setHistory((previous) => [
                ...previous,
                { role: 'user' as const, text: Array.from(question.trim() || spoken || '').slice(0, 800).join('') },
                { role: 'assistant' as const, text: Array.from(update.answer.answer).slice(0, 800).join('') },
              ].filter((turn) => turn.text).slice(-6))
              setQuestion('')
              setFile(null)
              if (fileInput.current) fileInput.current.value = ''
              terminal = true
              break
            }
            case 'error':
              setError(update.detail)
              terminal = true
              break
          }
        }
      }
      if (!terminal) setError('Поток завершился без результата.')
    } catch (cause) {
      if (controller.signal.aborted) setError('Запрос остановлен.')
      else setError(errorText(cause))
    } finally {
      active.current = null
      setRunning(false)
    }
  }

  function clearFile() {
    setFile(null)
    if (fileInput.current) fileInput.current.value = ''
  }

  return <>
    <details className="trace-request-box" open={composerOpen} onToggle={(event) => setComposerOpen(event.currentTarget.open)}>
      <summary><PigeonIcon name="plus"/> Новый запрос <span className="trace-chevron" aria-hidden="true"/></summary>
      <form className="trace-request" onSubmit={(event) => { void submit(event) }}>
        <label htmlFor="demo-question">Вопрос о вузах</label>
        <textarea id="demo-question" maxLength={2000} rows={2} value={question} disabled={running}
          onChange={(event) => setQuestion(event.target.value)} placeholder="Например, как оформить перевод в НГТУ?" required={!file}/>
        <input ref={fileInput} type="file" accept="image/jpeg,image/png,image/webp,application/pdf,audio/*"
          disabled={running} hidden onChange={(event) => setFile(event.target.files?.[0] ?? null)}/>
        <div className="trace-request-tools">
          <button type="button" className="trace-quiet-button" disabled={running} onClick={() => fileInput.current?.click()}>
            <PigeonIcon name="attach"/> Прикрепить файл
          </button>
          {file && <span className="trace-file"><span title={file.name}>{file.name}</span>
            <button type="button" disabled={running} onClick={clearFile} aria-label="Удалить вложение"><PigeonIcon name="close"/></button>
          </span>}
          {history.length > 0 && <button type="button" className="trace-quiet-button" disabled={running} onClick={() => setHistory([])}>
            Сбросить контекст ({history.length})
          </button>}
          {running ?
            <button type="button" className="trace-run-button" onClick={() => active.current?.abort()}>
              <PigeonIcon name="stop"/> Остановить
            </button> :
            <button type="submit" className="trace-run-button" disabled={!question.trim() && !file}>
              Запустить трассировку <PigeonIcon name="next"/>
            </button>}
        </div>
      </form>
    </details>
    <TraceWorkspace key={attempt} layout={layout} running={running} photo={photo}
      run={{ spans, elapsed, answer, requestID, error, question: lastQuestion }}/>
  </>
}
