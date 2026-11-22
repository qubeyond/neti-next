import { useState } from 'react'
import { safeURL, type Artifact, type Citation } from './api'
import MarkdownContent from './markdown-content'
import { TraceMenu } from './trace-menu'

type Usage = { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number; cost?: number }
export type TraceSpan = {
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
export type TraceEnd = { type: 'answer'; answer: Reply; decision?: string; recovery?: string }
export type TraceEvent =
  | ({ type: 'span_start' | 'span_end'; request_id: string } & TraceSpan)
  | (TraceEnd & { request_id: string })
  | { type: 'error'; detail: string; request_id: string }
  | { type: 'heartbeat'; request_id: string }

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
const seconds = Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const ms = (value: number) => value < 1000 ? `${number.format(value)} мс` : `${seconds.format(value / 1000)} с`
const label = (span: TraceSpan) => operations[span.operation]?.[0] ?? span.operation

export type TraceLayout = 'timeline' | 'services' | 'answer'

export type TraceSnapshot = {
  question: string
  requestID: string
  spans: TraceSpan[]
  answer?: TraceEnd
  error?: string
  elapsed: number
}

const serviceName = (service: string) => services[service] ?? service
const outcomeName = { ok: 'Готово', error: 'Ошибка', running: 'В работе' }

function SpanInspector({ span, spans }: { span?: TraceSpan; spans: TraceSpan[] }) {
  if (!span) return <p className="trace-empty">Выберите шаг, чтобы посмотреть вызов и результат.</p>
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
  return <><div className="trace-inspector-title"><h3>{label(span)}</h3><span>{serviceName(span.service)}</span></div><dl className="trace-details">{rows.filter(([, value]) => value !== undefined && value !== '').map(([name, value]) =>
    <div key={name}><dt>{name}</dt><dd>{value}</dd></div>,
  )}</dl></>
}

function TraceSteps({ spans, visible, total, elapsed, selection, select, timeline, indented }: {
  spans: TraceSpan[]
  visible: TraceSpan[]
  total: number
  elapsed: number
  selection?: string
  select: (id: string) => void
  timeline: boolean
  indented: boolean
}) {
  const byID = new Map(spans.map((span) => [span.id, span]))
  function depth(span: TraceSpan) {
    let levels = 0
    let parent = span.parent
    while (parent && levels < 3) {
      levels++
      parent = byID.get(parent)?.parent
    }
    return levels
  }

  return <div className={timeline ? 'trace-table trace-waterfall' : 'trace-table'}>
    <div className="trace-table-head" aria-hidden="true">
      <span>Операция</span>
      {timeline ? <span className="trace-ticks">{[0, .25, .5, .75, 1].map((point) => <span key={point}>{ms(Math.round(point * total))}</span>)}</span> :
        <span className="trace-offset">Начало</span>}
      <span>Время</span>
    </div>
    <ol className="trace-steps" aria-label="Вызванные операции">
      {visible.map((span) => {
        const duration = span.duration_ms ?? Math.max(0, elapsed - span.at_ms)
        const status = span.outcome ?? 'running'
        const left = Math.min(98, span.at_ms / total * 100)
        const width = Math.min(100 - left, Math.max(1.5, duration / total * 100))
        return <li key={span.id}>
          <button type="button" className="trace-step" aria-pressed={selection === span.id} onClick={() => select(span.id)}>
            <span className="trace-step-name" style={{ paddingInlineStart: indented ? depth(span) * 11 : 0 }}>
              <span className={'trace-dot is-' + status} role="img" aria-label={outcomeName[status]}/>
              <span><strong>{label(span)}</strong><small>{serviceName(span.service)}</small></span>
            </span>
            {timeline ? <span className="trace-track" aria-hidden="true"><span className={'is-' + status}
              style={{ left: left + '%', width: width + '%' }}/></span> :
              <time className="trace-offset">{ms(span.at_ms)}</time>}
            <time>{span.duration_ms === undefined ? '…' : ms(duration)}</time>
          </button>
        </li>
      })}
    </ol>
  </div>
}

function TraceAnswer({ answer, photo }: { answer: TraceEnd; photo: string }) {
  return <section className="trace-answer" aria-labelledby="trace-answer-heading">
      <header className="trace-section-head"><h2 id="trace-answer-heading">Ответ и источники</h2><span className="trace-status">{decisions[answer.answer.status] ?? answer.answer.status}</span></header>
      <div className="markdown"><MarkdownContent text={answer.answer.answer}/></div>
      {answer.decision && decisions[answer.decision] && <p>{decisions[answer.decision]}</p>}
      {answer.recovery && recoveries[answer.recovery] && <p>{recoveries[answer.recovery]}</p>}
      {answer.answer.artifacts?.map((item, index) => item.text && <details className="trace-artifact" key={index}>
        <summary>{item.title}<span className="trace-chevron" aria-hidden="true"/></summary><blockquote>{item.text}</blockquote>
      </details>)}
      {(answer.answer.citations ?? []).map(({ quote, url, ref, attachment_id: attachmentID, region }, index) => {
        const link = safeURL(url)
        return <div className="trace-source" key={index}>
          <strong>{ref ? `〔${ref}〕` : `Источник ${index + 1}`}</strong>
          <blockquote>{quote}</blockquote>
          {link && <a href={link} target="_blank" rel="noreferrer">Открыть источник</a>}
          {attachmentID && photo && <figure className="source-photo"><div>
            <img src={photo} alt="Вложение демо"/>
            {region && <span className="source-highlight" aria-hidden="true" style={{
              left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.width * 100}%`, height: `${region.height * 100}%`,
            }}/>}
          </div></figure>}
        </div>
      })}
    </section>
}

export function TraceWorkspace({ run, layout, running, photo }: {
  run: TraceSnapshot
  layout: TraceLayout
  running: boolean
  photo: string
}) {
  const [service, setService] = useState('')
  const [selection, setSelection] = useState('')
  const { spans, elapsed, question, requestID, answer, error } = run
  const calls = spans.filter((span) => span.service === 'inference')
  const tokens = calls.flatMap((span) => span.usage?.total_tokens ?? [])
  const costs = calls.flatMap((span) => span.usage?.cost ?? [])
  const total = spans.reduce((end, span) => Math.max(end, span.at_ms + (span.duration_ms ?? 0)), running ? elapsed : 0)
  const agentDuration = spans.find((span) => span.id === 'agent')?.duration_ms
  const metrics = [
    ['Время', ms(agentDuration ?? (total || elapsed))],
    ['Вызовы модели', calls.length],
    ['Токены', tokens.length ? number.format(tokens.reduce((sum, value) => sum + value, 0)) : '—'],
    ['Расход', costs.length ? money.format(costs.reduce((sum, value) => sum + value, 0)) : '—'],
  ]
  const options = [
    { value: '', label: 'Все сервисы', count: spans.length },
    ...Array.from(new Set(spans.map((span) => span.service)), (value) => ({
      value, label: serviceName(value), count: spans.filter((span) => span.service === value).length,
    })),
  ]
  const visible = service ? spans.filter((span) => span.service === service) : spans
  const current = visible.find((span) => span.id === selection) ?? visible.find((span) => span.outcome === 'error') ??
    visible.find((span) => span.operation === 'generate') ?? visible.at(-1)
  const hasRun = Boolean(question || error || answer || running || spans.length)
  const state = running ? 'В процессе' : error ? 'Сбой' : answer ? 'Завершено' : 'Ожидание'
  const answerPanel = answer && <TraceAnswer answer={answer} photo={photo}/>

  return <>
    {hasRun && <section className="trace-summary" aria-label="Сводка запроса">
      <div className="trace-run-info">
        <span className={'trace-state' + (error && !running ? ' is-error' : '')}>
          <span className={'trace-dot is-' + (running ? 'running' : error ? 'error' : 'ok')} aria-hidden="true"/>{state}
        </span>
        {requestID && <code>{requestID}</code>}
      </div>
      {question && <h2 className="trace-run-title">{question}</h2>}
      <dl className="trace-metrics">{metrics.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
      {((tokens.length > 0 && tokens.length !== calls.length) || (costs.length > 0 && costs.length !== calls.length)) &&
        <p className="trace-empty">Расход и токены рассчитаны по доступным данным провайдера.</p>}
    </section>}
    {error && <p role="status" className="trace-error">{error}</p>}
    <div className="trace-grid">
      {layout === 'answer' && answerPanel}
      {layout === 'services' && spans.length > 0 && <aside className="trace-services" aria-labelledby="trace-services-heading">
        <header className="trace-section-head"><h2 id="trace-services-heading">Сервисы</h2></header>
        {options.map((option) => <button type="button" key={option.value} className="trace-service"
          aria-pressed={service === option.value} onClick={() => setService(option.value)}>
          <span>{option.label}</span><span>{option.count}</span>
        </button>)}
      </aside>}
      <section className="trace-operations" aria-labelledby="trace-operations-heading">
        <header className="trace-section-head">
          <h2 id="trace-operations-heading">{layout === 'timeline' ? 'Ход запроса' : 'Операции'}</h2>
          {layout !== 'services' && options.length > 1 && <TraceMenu label="Фильтр по сервису"
            value={service} options={options} onChange={setService}/>}
        </header>
        {visible.length ? <TraceSteps spans={spans} visible={visible} total={Math.max(1,total)} elapsed={elapsed}
          selection={current?.id} select={setSelection} timeline={layout === 'timeline'} indented={!service && layout !== 'answer'}/> :
          <p className="trace-empty">{running ? 'Ждём первый этап…' : 'Первый этап появится после запуска трассировки.'}</p>}
      </section>
      <section className="trace-inspector" aria-labelledby="trace-inspector-heading">
        <header className="trace-section-head"><h2 id="trace-inspector-heading">Инспектор шага</h2></header>
        <SpanInspector span={current} spans={spans}/>
      </section>
      {layout !== 'answer' && answerPanel}
    </div>
  </>
}
