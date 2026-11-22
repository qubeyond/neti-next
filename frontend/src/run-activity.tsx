import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { PigeonIcon } from './pigeon-icon'
import { safeURL, type Message } from './api'

const skillNames: Record<string, string> = {
  overview: 'Краткая справка о вузе',
  ranking: 'Рейтинги вузов',
  comparison: 'Сравнение',
  calendar: 'Сроки',
  calculation: 'Расчёт',
  plan: 'План действий',
  office: 'Подразделения',
  edition: 'Редакции документов',
  attachment: 'Вложения',
  'table-evidence': 'Извлечение таблиц',
}

const secondsFormat = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })

function operationTime(milliseconds: number) {
  return milliseconds < 10 ? '< 0,01 с' : secondsFormat.format(milliseconds / 1000) + ' с'
}

const visibleOperations = new Set(['search', 'backup_search', 'read', 'select_skills', 'inference', 'tool'])

export function RunActivity({ item }: { item: Message }) {
  const panelID = useId()
  const [open, setOpen] = useState(false)
  const anchor = useRef<{ bubble: HTMLElement; thread: HTMLElement; top: number } | null>(null)
  useLayoutEffect(() => {
    const position = anchor.current
    if (!position) return
    anchor.current = null
    position.thread.scrollTop += position.bubble.getBoundingClientRect().top - position.top
  }, [open])

  function toggle(button: HTMLButtonElement) {
    const bubble = button.closest('article')?.querySelector<HTMLElement>('.assistant-content')
    const thread = button.closest<HTMLElement>('.thread-view')
    if (bubble && thread) anchor.current = { bubble, thread, top: bubble.getBoundingClientRect().top }
    setOpen((previous) => !previous)
  }
  const presentedSkills = new Set<string>()
  const steps = (item.activity ?? []).filter((step) => {
    if (!visibleOperations.has(step.kind)) return false
    if (step.kind !== 'select_skills' || (step.state && step.state !== 'ok')) return true
    if (!step.skills?.length) return false
    const key = JSON.stringify([...new Set(step.skills)].sort())
    if (presentedSkills.has(key)) return false
    presentedSkills.add(key)
    return true
  })
  const working = item.state === 'working'
  const [clock, setClock] = useState(() => Date.now())
  const hasRunning = working && steps.some((step) => step.state === 'running')
  useEffect(() => {
    if (!hasRunning) return
    const timer = window.setInterval(() => setClock(Date.now()), 250)
    return () => clearInterval(timer)
  }, [hasRunning])
  if (!working && !steps.length) return null

  const current = working ? steps.filter((step) => step.state === 'running').at(-1) : undefined
  const status = working ? current?.label ?? 'Думаю' : 'Ход работы'
  const indicator = working ? <span className="spinner" aria-hidden="true"/> : <PigeonIcon name="clock"/>
  return (
    <>
      <div className="run-activity-bar">
        {steps.length ? <button type="button" className="run-activity-toggle" aria-expanded={open} aria-controls={panelID}
          onClick={(event) => toggle(event.currentTarget)}>{indicator}
          <span className="activity-summary-label" aria-live="polite">{status}</span>
        </button> : <span className="run-activity-status" role="status">{indicator} {status}</span>}
      </div>
      <ol className="run-activity-panel" id={panelID} hidden={!open} aria-label="Этапы выполнения">
        {steps.map((step, index) => {
          const running = step.state === 'running'
          const duration = running ? Math.max(0, clock - Date.parse(item.createdAt) - step.elapsedMs) : step.durationMs
          const status = step.state === 'error' ? 'Ошибка' : step.state === 'interrupted' ? 'Прервано' : ''
          return <li key={step.id ?? index} className={running ? 'active-step' : undefined}>
            <PigeonIcon name="activity" className="tool-step"/>
            <div>
              <div className="activity-step-heading">
                <span>{step.label}</span>
                {step.count != null && (step.kind === 'search' || step.kind === 'backup_search') && <small>Найдено: {step.count}</small>}
                {duration != null && <time>{operationTime(duration)}</time>}
                {status && <small>{status}</small>}
              </div>
              {!!step.skills?.length && <p className="activity-step-detail">{step.skills.map((skill) => skillNames[skill] ?? skill).join(', ')}</p>}
              {!!step.domains?.length && !step.sources?.length && <p className="activity-step-detail">{step.domains.join(', ')}</p>}
              {!!step.sources?.length && <ul className="activity-results" aria-label="Найденные материалы">
                {step.sources.map((source, sourceIndex) => {
                  const url = safeURL(source.url)
                  if (!url) return null
                  return <li key={sourceIndex}>
                    <a href={url} target="_blank" rel="noreferrer">{source.title || new URL(url).hostname}</a>
                    {source.excerpt && <p>{source.excerpt}</p>}
                  </li>
                })}
              </ul>}
            </div>
          </li>
        })}
      </ol>
    </>
  )
}
