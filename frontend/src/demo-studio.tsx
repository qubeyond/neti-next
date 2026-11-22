import { useEffect, useRef, useState, type ReactNode } from 'react'
import { api, errorText, safeURL, type Citation, type DemoTurn, type Artifact } from './api'
import { Composer, emptyDraft, type Draft } from './composer'
import { groupCitations, sourceLocation } from './citations'
import MarkdownContent from './markdown-content'
import { PigeonIcon } from './pigeon-icon'
import { isImageTranscript } from './artifact-utils'
import { TraceMenu } from './trace-menu'
import './trace-workspace.css'

type Usage = { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; cost?: number }
type Span = {
  id: string
  parent?: string
  service: string
  operation: string
  at_ms: number
  duration_ms?: number
  upstream_ms?: number
  http_status?: number
  count?: number
  cache_hit?: boolean
  outcome?: 'ok' | 'error'
  decision?: string
  model?: string
  provider?: string
  domains?: string[]
  active_skills?: string[]
  usage?: Usage
}
type Reply = { status: string; answer: string; citations: Citation[] | null; artifacts?: Artifact[] }
type End = { type: 'answer'; answer: Reply; decision?: string; recovery?: string }
type Event =
  | ({ type: 'span_start' | 'span_end'; request_id: string } & Span)
  | (End & { request_id: string })
  | { type: 'error'; detail: string; request_id: string }
  | { type: 'heartbeat'; request_id: string }

export type { Event as TraceEvent, Span as TraceSpan }

export type TraceSnapshot = {
  question: string
  requestID: string
  spans: Span[]
  answer?: End
  error?: string
  elapsed: number
}

const services: Record<string, string> = { backend: 'Go API', agent: 'Агент', web: 'Веб', storage: 'PostgreSQL', inference: 'Inference' }
const operations: Record<string, [string, string]> = {
  agent_request: ['Запрос к агенту', 'POST /agent/v1/answers/trace'],
  prepare_media: ['Подготовка вложения', 'prepare'],
  ocr: ['Распознавание документа', 'POST /inference/v1/ocr'],
  transcribe: ['Расшифровка аудио', 'POST /inference/v1/transcribe'],
  resolve_context: ['Условия вопроса', 'resolve'],
  search: ['Поиск источников', 'search'],
  cache_lookup: ['Чтение кэша', 'cached_document'],
  generation: ['Сборка ответа', 'answer_from_search'],
  select_skills: ['Выбор скилов', 'select_skills'],
  generate: ['Вызов модели', 'POST /inference/v1/generate'],
  grounding: ['Проверка цитат', 'check_answer'],
  repair: ['Повтор генерации', 'repair'],
  read: ['Чтение документа', 'read_document'],
  rewrite: ['Уточнение поискового запроса', 'rewrite_query'],
  backup_search: ['Дополнительный поиск', 'search'],
  regeneration: ['Ответ по новым источникам', 'answer_from_search'],
  verify: ['Перепроверка доказательств', 'audit'],
  evidence_audit: ['Оценка применимости цитат', 'audit'],
}
const decisions: Record<string, string> = {
  answered: 'Подтверждённый ответ', partial: 'Частичный ответ', unverified: 'Подтверждения недостаточно',
  clarification: 'Запрошено уточнение', ready: 'Вопрос готов к поиску', invalid: 'Некорректный формат ответа модели',
  supported: 'Проверка пройдена', citations: 'Цитаты или числа не прошли проверку',
  missing_total: 'Источники не подтверждают общий итог', procedure: 'Источник не подтверждает процедуру',
  audit: 'Дополнительная проверка не подтвердила ответ',
  conflicting_login_links: 'Найдены разные адреса входа',
}
const recoveries: Record<string, string> = {
  accepted_retry: 'Использованы новые доказательства', kept_initial: 'Сохранён первоначальный ответ',
  no_new_evidence: 'Дополнительный шаг не нашёл доказательств', provider_error: 'Дополнительный вызов завершился ошибкой',
}

const number = Intl.NumberFormat('ru-RU')
const money = Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 4 })
const ms = (value: number) => value < 1000 ? `${number.format(value)} мс` : `${(value / 1000).toFixed(2)} с`
const label = (span: Span) => operations[span.operation]?.[0] ?? span.operation

function ServiceDiagram({ spans }: { spans: Span[] }) {
  function node(service: string) {
    const related = spans.filter((span) => span.service === service)
    const running = related.some((span) => span.outcome === undefined)
    const errors = related.filter((span) => span.outcome === 'error').length
    const state = running ? 'running' : errors ? 'error' : related.length ? 'done' : 'idle'
    return <div className={`trace-node trace-node-${state}`} key={service}>
      <strong>{services[service]}</strong>
      <small>{state === 'idle' ? 'Ожидает' : running ? 'В работе' : errors ? `Ошибок: ${errors}` : `Готово · ${related.length}`}</small>
    </div>
  }
  return <div className="trace-diagram" aria-label="Схема движения запроса">
    {node('backend')}<span className="trace-arrow" aria-hidden="true">→</span>{node('agent')}
    <span className="trace-arrow" aria-hidden="true">⇄</span>
    <div className="trace-branches">{['web', 'storage', 'inference'].map(node)}</div>
  </div>
}

function SpanInspector({ span, spans }: { span?: Span; spans: Span[] }) {
  if (!span) return <p className="trace-placeholder">Выберите шаг, чтобы посмотреть вызов и результат.</p>
  const parent = spans.find((candidate) => candidate.id === span.parent)
  const rows = [
    ['Метод', operations[span.operation]?.[1] ?? span.operation],
    ['Вызван из', parent && label(parent)],
    ['Начало', ms(span.at_ms)],
    ['Длительность', span.duration_ms === undefined ? 'В работе' : ms(span.duration_ms)],
    ['Время внутри инференса', span.upstream_ms !== undefined ? ms(span.upstream_ms) : undefined],
    ['HTTP', span.http_status],
    ['Провайдер', span.provider],
    ['Домены', span.domains?.join(', ')],
    ['Скилы', span.operation === 'select_skills' && span.outcome ? span.active_skills?.join(', ') || 'Базовый ответ' : undefined],
    ['Модель', span.model],
    ['Токены на входе', span.usage?.prompt_tokens],
    ['Токены на выходе', span.usage?.completion_tokens],
    ['Токены всего', span.usage?.total_tokens],
    ['Расход по API', span.usage?.cost !== undefined ? money.format(span.usage.cost) : undefined],
    ['Результатов или цитат', span.count],
    ['Кэш', span.cache_hit === undefined ? undefined : span.cache_hit ? 'Попадание' : 'Промах'],
    ['Решение', span.decision && decisions[span.decision]],
  ]
  return <><h3>{label(span)}</h3><dl className="trace-details">{rows.filter(([, value]) => value !== undefined && value !== '').map(([name, value]) =>
    <div key={name}><dt>{name}</dt><dd>{value}</dd></div>,
  )}</dl></>
}

function TraceSteps({ spans, visible, elapsed, selection, select }: {
  spans: Span[]
  visible: Span[]
  elapsed: number
  selection: string
  select: (id: string) => void
}) {
  const spanByID = new Map(spans.map((span) => [span.id, span]))
  function depth(span: Span) {
    let levels = 0
    let parent = span.parent
    while (parent && levels < 5) {
      levels++
      parent = spanByID.get(parent)?.parent
    }
    return levels
  }
  const width = Math.max(elapsed, ...spans.map((span) => span.at_ms + (span.duration_ms ?? 0)), 1)
  return <ol className="trace-steps" aria-label="Вызванные операции">{visible.map((span) => {
    const duration = span.duration_ms ?? Math.max(0, elapsed - span.at_ms)
    return <li key={span.id}>
      <button type="button" className="trace-step" aria-pressed={selection === span.id} onClick={() => select(span.id)}>
        <span className="trace-step-name" style={{ paddingInlineStart: depth(span) * 12 }}>
          <span className={`trace-dot ${span.outcome ?? 'running'}`}/><span><strong>{label(span)}</strong><small>{services[span.service]}</small></span>
        </span>
        <span className="trace-bar" aria-hidden="true"><span className={span.outcome ?? 'running'} style={{
          left: `${Math.min(98, span.at_ms / width * 100)}%`,
          width: `${Math.max(2, duration / width * 100)}%`,
        }}/></span>
        <time>{span.duration_ms === undefined ? 'В работе' : ms(duration)}</time>
      </button>
    </li>
  })}</ol>
}

export default function DemoStudio({ account, fixture, controls, execute = api.demoRun }: {
  account: string
  fixture?: TraceSnapshot
  controls?: ReactNode
  execute?: typeof api.demoRun
}) {
  return <main className="demo-studio">
    <header className="demo-header">
      <div><a href={fixture ? '/' : '#'}>← Кабинет</a><h1>Трассировка запроса</h1></div>
      <div className="trace-header-actions"><span>{account}</span>{controls}</div>
    </header>
    <TraceSession key={fixture?.requestID} fixture={fixture} execute={execute}/>
  </main>
}

function TraceResult({ result, requestID, photo }: { result: End; requestID: string; photo: string }) {
  const { answer, decision, recovery } = result
  const evidenceNodes = useRef(new Map<Citation, HTMLDetailsElement>())
  const { references, sources } = groupCitations(requestID, answer.citations ?? [], (citation) => {
    const details = evidenceNodes.current.get(citation)
    if (!details) return
    details.open = true
    details.querySelector('summary')?.focus()
  })
  const attachments = answer.artifacts?.filter((artifact) => !isImageTranscript(artifact) && (artifact.text || safeURL(artifact.url))) ?? []
  const status = decisions[answer.status] ?? answer.status
  const note = decision && decisions[decision] !== status ? decisions[decision] : undefined
  const followup = recovery ? recoveries[recovery] : undefined

  return <section className="trace-panel trace-answer" aria-labelledby="trace-result-title">
    <header className="trace-result-header">
      <h2 id="trace-result-title">Ответ и доказательства</h2>
      <span className="trace-result-status" data-status={answer.status}>{status}</span>
    </header>
    <div className={'trace-result-body' + (sources.length || attachments.length ? ' with-evidence' : '')}>
      <div className="trace-result-text">
        <div className="markdown"><MarkdownContent text={answer.answer} references={references}/></div>
        {(note || followup) && <div className="trace-result-notes">{note && <p>{note}</p>}{followup && <p>{followup}</p>}</div>}
      </div>
      {(sources.length > 0 || attachments.length > 0) && <div className="trace-evidence">
        {sources.length > 0 && <section aria-labelledby="trace-sources-title">
          <h3 id="trace-sources-title">Источники <span>{sources.length}</span></h3>
          {sources.map(({ id, number, citation }, index) => {
            const link = sourceLocation(citation)
            const title = link ? new URL(link).hostname : citation.attachment_id ? 'Вложение' : 'Цитата'
            const region = citation.region
            return <details className="trace-source trace-disclosure" key={id} open={index === 0} ref={(node) => {
              if (node) evidenceNodes.current.set(citation, node)
              else evidenceNodes.current.delete(citation)
            }}>
              <summary><span className="trace-bracket" aria-hidden="true"/><span>〔{number}〕 {title}</span></summary>
              <blockquote>{citation.quote}</blockquote>
              {link && <a className="trace-source-link" href={link} target="_blank" rel="noreferrer">
                Открыть источник <PigeonIcon name="external"/>
              </a>}
              {citation.attachment_id && photo && <figure className="source-photo"><div>
                <img src={photo} alt="Приложенное изображение"/>
                {region && <span className="source-highlight" aria-hidden="true" style={{
                  left: region.x * 100 + '%', top: region.y * 100 + '%', width: region.width * 100 + '%', height: region.height * 100 + '%',
                }}/>}
              </div></figure>}
            </details>
          })}
        </section>}
        {attachments.length > 0 && <section aria-labelledby="trace-artifacts-title">
          <h3 id="trace-artifacts-title">Материалы прогона</h3>
          {attachments.map((item, index) => {
            const url = safeURL(item.url)
            return <details className="trace-source trace-disclosure" key={index}>
              <summary><span className="trace-bracket" aria-hidden="true"/>{item.title}</summary>
              {item.text && <blockquote>{item.text}</blockquote>}
              {url && <a className="trace-source-link" href={url} target="_blank" rel="noreferrer">
                Открыть материал <PigeonIcon name="external"/>
              </a>}
            </details>
          })}
        </section>}
      </div>}
    </div>
  </section>
}

const compactViewport = '(max-width: 760px)'

function TraceSession({ fixture, execute }: { fixture?: TraceSnapshot; execute: typeof api.demoRun }) {
  const [draft, setDraft] = useState<Draft>(() => ({ ...emptyDraft, text: fixture?.question ?? '' }))
  const [history, setHistory] = useState<DemoTurn[]>([])
  const [compact, setCompact] = useState(() => matchMedia(compactViewport).matches)
  const input = useRef<HTMLTextAreaElement>(null)
  const [photo, setPhoto] = useState('')
  const photoURL = useRef('')
  const [spans, setSpans] = useState<Span[]>(fixture?.spans ?? [])
  const [selection, setSelection] = useState(fixture?.spans.find((span) => span.operation === 'generate')?.id ?? '')
  const [service, setService] = useState('')
  const [answer, setAnswer] = useState<End | undefined>(fixture?.answer)
  const [requestID, setRequestID] = useState(fixture?.requestID ?? '')
  const [error, setError] = useState(fixture?.error ?? '')
  const [elapsed, setElapsed] = useState(fixture?.elapsed ?? 0)
  const [running, setRunning] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const active = useRef<AbortController | null>(null)

  useEffect(() => {
    const viewport = matchMedia(compactViewport)
    const update = () => setCompact(viewport.matches)
    viewport.addEventListener('change', update)
    return () => viewport.removeEventListener('change', update)
  }, [])
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

  async function transcribe(audio: Blob) {
    const { text } = fixture ? { text: fixture.question } : await api.transcribe({}, audio)
    setDraft((current) => ({ ...current, text: [current.text.trimEnd(), text].filter(Boolean).join('\n') }))
    input.current?.focus()
  }

  async function submit() {
    const submitted = draft
    const question = submitted.text.trim()
    const file = submitted.files[0] ?? null
    if (active.current || (!question && !file)) return
    const controller = new AbortController()
    active.current = controller
    setSpans([])
    setSelection('')
    setService('')
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
      const response = await execute(question, history, file, controller.signal)
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
          const update = JSON.parse(line) as Event
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
                { role: 'user' as const, text: Array.from(question || spoken || '').slice(0, 800).join('') },
                { role: 'assistant' as const, text: Array.from(update.answer.answer).slice(0, 800).join('') },
              ].filter((turn) => turn.text).slice(-6))
              setDraft((current) => current === submitted ? emptyDraft : {
                ...current,
                files: current.files.filter((item) => !submitted.files.includes(item)),
              })
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
      setCancelling(false)
    }
  }

  const calls = spans.filter((span) => span.service === 'inference')
  const totals = calls.flatMap((span) => span.usage?.total_tokens ?? [])
  const costs = calls.flatMap((span) => span.usage?.cost ?? [])
  const total = spans.find((span) => span.id === 'agent')?.duration_ms
  const metrics = [
    ['Время', ms(total ?? elapsed)],
    ['Вызовы модели', String(calls.length)],
    ['Токены', totals.length ? number.format(totals.reduce((sum, value) => sum + value, 0)) : '—'],
    ['Расход модели', costs.length ? money.format(costs.reduce((sum, value) => sum + value, 0)) : '—'],
  ]

  const serviceOptions = [
    { value: '', label: 'Все сервисы' },
    ...Array.from(new Set(spans.map((span) => span.service)), (value) => ({ value, label: services[value] ?? value })),
  ]
  const visible = service ? spans.filter((span) => span.service === service) : spans
  const inspected = visible.find((span) => span.id === selection) ?? visible.at(-1)

  return <>
    <section className="trace-composer" aria-label="Запуск трассировки">
      <Composer draft={draft} update={(patch) => setDraft((current) => ({ ...current, ...patch }))}
        inputRef={input} disabled={running} pending={running} cancelling={cancelling} compact={compact}
        attachmentLimit={1} acceptFiles="image/jpeg,image/png,image/webp,application/pdf,audio/*"
        onSubmit={() => { void submit() }} onTranscribe={transcribe}
        onCancel={running ? () => { setCancelling(true); active.current?.abort() } : undefined}/>
      <div className="trace-compose-meta">
        <small><kbd>Enter</kbd> отправить · <kbd>Shift</kbd> + <kbd>Enter</kbd> новая строка</small>
        {history.length > 0 && <button type="button" className="soft-button" disabled={running} onClick={() => setHistory([])}>
          Сбросить контекст ({history.length})
        </button>}
      </div>
    </section>
    <dl className="trace-metrics">{metrics.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
    {(totals.length > 0 && totals.length !== calls.length || costs.length > 0 && costs.length !== calls.length) &&
      <p className="trace-placeholder">Токены и цена показаны только для вызовов, по которым провайдер вернул поля.</p>}
    <ServiceDiagram spans={spans}/>
    <div className="trace-content">
      <section className="trace-panel">
        <header><h2>Ход запроса</h2>
          <div className="trace-header-actions">
            {requestID && <code title={requestID}>{requestID}</code>}
            {serviceOptions.length > 1 && <TraceMenu label="Фильтр по сервису" value={service}
              options={serviceOptions} onChange={setService}/>}
          </div>
        </header>
        {visible.length ? <TraceSteps spans={spans} visible={visible} selection={inspected?.id ?? ''} select={setSelection} elapsed={total ?? elapsed}/> :
          <p className="trace-placeholder">Первый этап появится после запуска.</p>}
      </section>
      <section className="trace-panel">
        <header><h2>Инспектор шага</h2></header>
        <SpanInspector spans={spans} span={inspected}/>
      </section>
    </div>
    <div aria-live="polite">{error && <p className="trace-error">{error}</p>}</div>
    {answer && <TraceResult result={answer} requestID={requestID} photo={photo}/>}
  </>
}
