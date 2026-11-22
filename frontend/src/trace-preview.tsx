import { useState } from 'react'
import DemoStudio from './demo-studio'
import { TraceMenu } from './trace-menu'
import type { TraceEvent, TraceLayout, TraceSnapshot, TraceSpan } from './trace-workspace'

const layouts: { value: TraceLayout; label: string }[] = [
  { value: 'timeline', label: 'Хронология' },
  { value: 'services', label: 'По сервисам' },
  { value: 'answer', label: 'Ответ и источники' },
]
const scenarios = [
  { value: 'success', label: 'Успешный ответ' },
  { value: 'error', label: 'Сбой модели' },
] as const
type Scenario = typeof scenarios[number]['value']

const spans: TraceSpan[] = [
  { id: 'agent', service: 'backend', operation: 'agent_request', at_ms: 0, duration_ms: 4860, http_status: 200, outcome: 'ok' },
  { id: 'context', parent: 'agent', service: 'agent', operation: 'resolve_context', at_ms: 23, duration_ms: 156, decision: 'ready', outcome: 'ok' },
  { id: 'skills', parent: 'agent', service: 'agent', operation: 'select_skills', at_ms: 181, duration_ms: 42, active_skills: ['plan'], outcome: 'ok' },
  { id: 'cache', parent: 'agent', service: 'storage', operation: 'cache_lookup', at_ms: 237, duration_ms: 89, cache_hit: false, outcome: 'ok' },
  { id: 'search', parent: 'agent', service: 'web', operation: 'search', at_ms: 342, duration_ms: 1020, provider: 'Exa', domains: ['nstu.example.org'], count: 5, outcome: 'ok' },
  { id: 'generation', parent: 'agent', service: 'agent', operation: 'generation', at_ms: 1380, duration_ms: 3130, outcome: 'ok' },
  { id: 'model', parent: 'generation', service: 'inference', operation: 'generate', at_ms: 1450, duration_ms: 2870, upstream_ms: 2630, http_status: 200, provider: 'RouterAI',
    model: 'DeepSeek V4 Flash', usage: { prompt_tokens: 1494, completion_tokens: 348, total_tokens: 1842, cost: .0734 }, outcome: 'ok' },
  { id: 'grounding', parent: 'generation', service: 'agent', operation: 'grounding', at_ms: 4339, duration_ms: 136, decision: 'supported', count: 2, outcome: 'ok' },
  { id: 'check', parent: 'agent', service: 'agent', operation: 'evidence_audit', at_ms: 4534, duration_ms: 209, decision: 'supported', outcome: 'ok' },
]

const success: TraceSnapshot = {
  question: 'Как первокурснику заселиться в общежитие НГТУ?',
  requestID: 'trace-demo-0248',
  elapsed: 4860,
  spans,
  answer: {
    type: 'answer',
    answer: {
      status: 'answered',
      answer: '**Сначала проверьте, выделили ли вам место.** После этого сверьте дату заезда и список документов для вашего корпуса.\n\n' +
        '1. Откройте памятку для заселяющихся и найдите свой корпус.\n' +
        '2. Возьмите паспорт и документы из списка.\n' +
        '3. В день заезда обратитесь к администратору общежития.',
      citations: [
        { ref: 1, url: 'https://nstu.example.org/settling-in', quote: 'Даты заезда и перечень документов публикуются в памятке для заселяющихся.' },
        { ref: 2, url: 'https://nstu.example.org/housing', quote: 'При заселении студент обращается к администратору общежития.' },
      ],
      artifacts: [{ kind: 'quote', title: 'Выдержка из памятки', text: 'Сверьте дату заезда и документы по вашему корпусу.' }],
    },
  },
}
const failure: TraceSnapshot = {
  question: success.question,
  requestID: 'trace-demo-0249',
  elapsed: 2690,
  error: 'Модель не ответила вовремя. Повторите запрос.',
  spans: spans.slice(0, 7).map((span) => {
    if (span.id === 'agent') return { ...span, duration_ms: 2690, http_status: 502, outcome: 'error' }
    if (span.id === 'generation') return { ...span, duration_ms: 1260, outcome: 'error' }
    if (span.id === 'model') return { ...span, duration_ms: 1140, http_status: 504, usage: undefined, outcome: 'error' }
    return span
  }),
}

function replay(run: TraceSnapshot, signal: AbortSignal): Response {
  const request_id = run.requestID
  const events: TraceEvent[] = run.spans.flatMap((span): TraceEvent[] => [
    { type: 'span_start', id: span.id, parent: span.parent, service: span.service, operation: span.operation, at_ms: span.at_ms, request_id },
    { ...span, type: 'span_end', at_ms: span.at_ms + (span.duration_ms ?? 0), request_id },
  ])
  events.sort((a, b) => ('at_ms' in a ? a.at_ms : 0) - ('at_ms' in b ? b.at_ms : 0))
  if (run.answer) events.push({ ...run.answer, request_id })
  else events.push({ type: 'error', detail: run.error ?? 'Запрос не завершился.', request_id })
  const encoder = new TextEncoder()
  let index = 0
  return new Response(new ReadableStream({
    async pull(controller) {
      await new Promise((resolve) => window.setTimeout(resolve, 42))
      signal.throwIfAborted()
      const event = events[index++]
      if (event) controller.enqueue(encoder.encode(JSON.stringify(event) + '\n'))
      else controller.close()
    },
  }), { headers: { 'Content-Type': 'application/x-ndjson' } })
}

function setPreviewParam(key: string, value: string) {
  const url = new URL(window.location.href)
  url.searchParams.set(key, value)
  window.history.replaceState(window.history.state, '', url)
}

export default function TracePreview() {
  const [layout, setLayout] = useState<TraceLayout>(() => {
    const selected = new URLSearchParams(location.search).get('layout')
    return layouts.find((option) => option.value === selected)?.value ?? 'timeline'
  })
  const [scenario, setScenario] = useState<Scenario>(() => new URLSearchParams(location.search).get('scenario') === 'error' ? 'error' : 'success')
  const fixture = scenario === 'error' ? failure : success
  return <DemoStudio account="Тестовые данные" layout={layout} fixture={fixture}
    execute={async (_question, _history, _file, signal) => replay(fixture, signal)}
    controls={<div className="trace-preview-tools" aria-label="Параметры предпросмотра">
      <TraceMenu label="Макет" value={layout} options={layouts}
        onChange={(next) => { setLayout(next); setPreviewParam('layout', next) }}/>
      <TraceMenu label="Сценарий" value={scenario} options={[...scenarios]}
        onChange={(next) => { setScenario(next); setPreviewParam('scenario', next) }}/>
    </div>}/>
}
