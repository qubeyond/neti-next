import { ArrowClockwiseIcon } from '@phosphor-icons/react/dist/icons/ArrowClockwise'
import { ArrowUpRightIcon } from '@phosphor-icons/react/dist/icons/ArrowUpRight'
import { ChatTextIcon } from '@phosphor-icons/react/dist/icons/ChatText'
import { ClockIcon } from '@phosphor-icons/react/dist/icons/Clock'
import { CopyIcon } from '@phosphor-icons/react/dist/icons/Copy'
import { DownloadIcon } from '@phosphor-icons/react/dist/icons/Download'
import { FileIcon } from '@phosphor-icons/react/dist/icons/File'
import { ImageIcon } from '@phosphor-icons/react/dist/icons/Image'
import { QuotesIcon } from '@phosphor-icons/react/dist/icons/Quotes'
import { WarningIcon } from '@phosphor-icons/react/dist/icons/Warning'
import { WrenchIcon } from '@phosphor-icons/react/dist/icons/Wrench'
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText, safeURL, type Artifact, type Citation, type FileItem, type Message, type Session } from './api'
import { AsciiPigeon } from './pigeon-ascii'
import { BrandMark, displayDuration } from './ui'
import { downloadDeadline } from './calendar'
import { ChatBlockView } from './chat-blocks'

const MarkdownContent = lazy(() => import('./markdown-content'))

function AttachedFile({ file, session, chat }: { file: FileItem; session: Session; chat: string }) {
  const [preview, setPreview] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const mediaURL = useRef('')
  const alive = useRef(true)
  const image = /^image\/(png|jpeg|gif|webp|avif)$/i.test(file.mime)
  const audio = file.mime.startsWith('audio/')

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      if (mediaURL.current) URL.revokeObjectURL(mediaURL.current)
    }
  }, [])

  async function open(download: boolean) {
    if (!download && preview) {
      URL.revokeObjectURL(mediaURL.current)
      mediaURL.current = ''
      setPreview('')
      return
    }
    setBusy(true)
    setError('')
    try {
      const blob = await api.download(session, chat, file.id)
      if (!alive.current) return
      const url = URL.createObjectURL(blob)
      if (download) {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = file.name
        anchor.click()
        setTimeout(() => URL.revokeObjectURL(url), 2000)
      } else {
        mediaURL.current = url
        setPreview(url)
      }
    } catch (error) {
      if (alive.current) setError(errorText(error))
    } finally {
      if (alive.current) setBusy(false)
    }
  }

  return <div className="attachment">
    <div className="attachment-line">
      {image ? <ImageIcon aria-hidden="true"/> : <FileIcon aria-hidden="true"/>}
      <span title={file.name}>{file.name}</span>
      {(image || audio) && <button type="button" disabled={busy} onClick={() => { void open(false) }}>{preview ? 'Скрыть' : image ? 'Просмотр' : 'Прослушать'}</button>}
      <button type="button" className="mini-button" aria-label={'Скачать ' + file.name} disabled={busy} onClick={() => { void open(true) }}><DownloadIcon/></button>
    </div>
    {preview && (image ? <img src={preview} alt={file.name}/> : <audio controls src={preview} aria-label={file.name}/>)}
    {error && <small role="alert">{error}</small>}
  </div>
}

function sourceLocation(citation: Citation) {
  const address = safeURL(citation.url)
  if (!address) return undefined
  const url = new URL(address)
  if (url.hash.includes(':~:') || url.pathname.toLowerCase().endsWith('.pdf')) return url.href
  const quote = Array.from(citation.quote.trim()).slice(0, 220).join('')
  if (quote.length < 12) return url.href
  url.hash += ':~:text=' + encodeURIComponent(quote).replaceAll('-', '%2D')
  return url.href
}

function CitationPhoto({ citation, session, chat }: { citation: Citation; session: Session; chat: string }) {
  const [photo, setPhoto] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const source = useRef('')
  const active = useRef(true)

  useEffect(() => {
    active.current = true
    return () => {
      active.current = false
      if (source.current) URL.revokeObjectURL(source.current)
      source.current = ''
    }
  }, [])

  async function reveal() {
    if (!citation.attachment_id) return
    setLoading(true)
    setError('')
    try {
      const blob = await api.download(session, chat, citation.attachment_id)
      if (!active.current) return
      source.current = URL.createObjectURL(blob)
      setPhoto(source.current)
    } catch (cause) {
      if (active.current) setError(errorText(cause))
    } finally {
      if (active.current) setLoading(false)
    }
  }

  const box = citation.region
  return <>
    {!photo && <button type="button" className="soft-button" disabled={loading} onClick={() => { void reveal() }}>Показать фото</button>}
    {error && <small role="alert">{error}</small>}
    {photo && <figure className="source-photo">
      <div>
        <img src={photo} alt="Фото, которое вы приложили"/>
        {box && <span className="source-highlight" aria-hidden="true" style={{
          left: `${box.x * 100}%`, top: `${box.y * 100}%`,
          width: `${box.width * 100}%`, height: `${box.height * 100}%`,
        }}/>}
      </div>
      {box && <figcaption>Строка, совпавшая с цитатой</figcaption>}
    </figure>}
  </>
}

function RunActivity({ item }: { item: Message }) {
  const working = item.state === 'working'
  const [clock, setClock] = useState(() => Date.now())
  useEffect(() => {
    if (!working) return
    const timer = window.setInterval(() => setClock(Date.now()), 300)
    return () => clearInterval(timer)
  }, [working])
  const elapsed = working ? clock - Date.parse(item.createdAt) : item.durationMs ?? 0
  const duration = !working && elapsed > 0 && elapsed < 1000 ? 'меньше 1 с' : displayDuration(elapsed)
  const lastStep = item.activity?.at(-1)
  return (
    <details className={'run-activity' + (working ? ' run-active' : '')} open={working || undefined}>
      <summary>{working ? <span className="spinner" aria-hidden="true"/> : <ClockIcon aria-hidden="true"/>}
        <span>{working ? 'Агент работает' : 'Ход работы'}</span><time>{duration}</time>
      </summary>
      <ol aria-label="Этапы выполнения">
        {(item.activity ?? []).map((step, index) => <li key={index} className={working && step === lastStep ? 'active-step' : ''}>
          {step.kind === 'tool' ? <WrenchIcon className="tool-step" aria-label="Инструмент"/> : <span aria-hidden="true"/>}
          <span>{step.label}</span><time>{displayDuration(step.elapsedMs)}</time>
        </li>)}
      </ol>
    </details>
  )
}

function ArtifactCard({ item }: { item: Artifact }) {
  const url = safeURL(item.url)
  if (item.kind === 'image' && url) {
    return <figure className="artifact-card"><a href={url} target="_blank" rel="noreferrer"><img src={url} alt={item.title} loading="lazy" referrerPolicy="no-referrer"/></a>
      <figcaption>{item.title}</figcaption></figure>
  }
  if (item.kind === 'quote' && item.text) {
    return <div className="artifact-card"><strong>{item.title}</strong><blockquote>{item.text}</blockquote>{url && <a href={url} target="_blank" rel="noreferrer">Открыть источник <ArrowUpRightIcon/></a>}</div>
  }
  if (!url) return null
  return <a className="artifact-card artifact-link" href={url} target="_blank" rel="noreferrer">
    <FileIcon aria-hidden="true"/><span>{item.title}</span><ArrowUpRightIcon aria-hidden="true"/>
  </a>
}

type Props = {
  item: Message
  chat: string
  session: Session
  latest: boolean
  mascot: boolean
  animate: boolean
  onQuote: (text: string, question: boolean) => void
  onSuggestion: (text: string) => void
  onRetry: () => void
  onCancel: () => void
}

export function ChatMessage({ item, chat, session, latest, mascot, animate, onQuote, onSuggestion, onRetry, onCancel }: Props) {
  const body = useRef<HTMLDivElement>(null)
  const [selection, setSelection] = useState('')
  const [copyState, setCopyState] = useState('')
  const [typing, setTyping] = useState(false)
  const assistant = item.role === 'assistant'

  const evidence = item.citations ?? []
  const sourceIDs = new Map<number, string>()
  const citationGroups = new Map<string, [Citation, ...Citation[]]>()
  for (const [index, citation] of evidence.entries()) {
    const id = (citation.ref ? sourceIDs.get(citation.ref) : undefined) ?? `evidence-${item.id}-${index}`
    if (citation.ref) sourceIDs.set(citation.ref, id)
    const group = citationGroups.get(id)
    if (group) group.push(citation)
    else citationGroups.set(id, [citation])
  }

  const selectText = useCallback(() => {
    const picked = window.getSelection()
    if (!picked || picked.isCollapsed || !body.current?.contains(picked.anchorNode) || !body.current.contains(picked.focusNode)) return
    const text = Array.from(picked.toString().trim()).slice(0, 1200).join('')
    if (text) setSelection(text)
  }, [])

  useEffect(() => {
    if (item.state !== 'complete') return
    document.addEventListener('selectionchange', selectText)
    return () => document.removeEventListener('selectionchange', selectText)
  }, [item.state, selectText])

  async function copy() {
    try {
      await navigator.clipboard.writeText(selection || item.text)
      setCopyState(selection ? 'Фрагмент скопирован' : 'Ответ скопирован')
    } catch {
      setCopyState('Не удалось скопировать')
    }
  }

  if (!assistant) return <article className="user-message" aria-label="Ваше сообщение">
    {item.quote && <blockquote>{item.quote}</blockquote>}
    {item.text && <p>{item.text}</p>}
    {!!item.attachments?.length && <div className="attachment-list">
      {item.attachments.map((file) => <AttachedFile file={file} key={file.id} session={session} chat={chat}/>)}
    </div>}
  </article>

  return <article className={'assistant-message' + (mascot ? ' with-mascot' : '')} aria-label="Ответ Пигеона">
    {mascot ? <div className="assistant-mascot"><AsciiPigeon speaking={typing}/></div> : <BrandMark className="message-mark"/>}
    <div className={'assistant-content' + (item.state === 'complete' ? ' speech-bubble' : '') + (animate && item.state === 'complete' ? ' reveal' : '')}>
      <header className="message-heading"><strong>Пигеон</strong></header>
      {!!item.activity?.length && <RunActivity item={item}/>}
      {item.state === 'working' && <>
        <div className="thinking-shimmer" aria-hidden="true"><span/><span/></div>
        {latest && <button type="button" className="soft-button" onClick={onCancel}>Остановить</button>}
      </>}
      {item.state === 'failed' && <div className="answer-error">
        <p role="alert"><WarningIcon aria-hidden="true"/> {item.problem?.detail ?? item.error ?? 'Не удалось получить ответ.'}</p>
        {latest && <button type="button" className="soft-button" onClick={onRetry}><ArrowClockwiseIcon/> Повторить запрос</button>}
      </div>}
      {item.state === 'complete' && <>
        {item.status !== 'answered' && <span className="answer-status">{item.status === 'partial' ? 'Частичный ответ' : item.status === 'clarification' ? 'Уточнение' : 'Недостаточно подтверждений'}</span>}
        <div ref={body} className="markdown" onMouseUp={selectText} onKeyUp={selectText}>
          <Suspense fallback={<p>Оформляем ответ…</p>}><MarkdownContent text={item.text} sourceIDs={sourceIDs} animate={animate} onTyping={setTyping}/></Suspense>
        </div>
        {item.blocks?.map((block, index) => <ChatBlockView key={index} block={block} sources={sourceIDs}/>)}
        {!!item.suggestions?.length && <div className="deadline-actions" aria-label="Варианты уточнения">
          {item.suggestions.map((suggestion) => <button className="soft-button" type="button" key={suggestion}
            onClick={() => onSuggestion(suggestion)}>{suggestion}</button>)}
        </div>}
        {!!item.artifacts?.length && <div className="artifact-grid">{item.artifacts.map((artifact, index) => <ArtifactCard item={artifact} key={index}/>)}</div>}
        {!!item.events?.length && <div className="deadline-actions" aria-label="Сроки для календаря">
          {item.events.map((event, index) => {
            const group = evidence.filter((source) => source.ref === event.citation && source.url)
            const citation = group[0] && { ...group[0], quote: group.map((source) => source.quote).join('\n') }
            return citation && <button type="button" className="soft-button" key={index}
              onClick={() => downloadDeadline(event, citation, `${item.id}-${index}`)}>
              <DownloadIcon aria-hidden="true"/> {event.title} · {event.date.split('-').reverse().join('.')} (.ics)
            </button>
          })}
        </div>}
        {!!evidence.length && <section className="source-list" aria-label="Источники ответа">
          {Array.from(citationGroups, ([id, group], index) => {
            const first = group[0]
            const url = safeURL(first.url)
            return <details className="source" id={id} key={id}>
              <summary><span className="source-index">{first.ref ?? index + 1}</span><span>{first.attachment_id ? 'Ваше фото' : url ? new URL(url).hostname : 'Источник'}</span></summary>
              {group.map((citation, position) => {
                const location = sourceLocation(citation)
                return <div key={position}>
                  <blockquote>{citation.quote}</blockquote>
                  {location && <a href={location} target="_blank" rel="noreferrer">{location === url ? 'К оригиналу' : 'Открыть цитату в источнике'} <ArrowUpRightIcon/></a>}
                  {citation.attachment_id && <CitationPhoto citation={citation} session={session} chat={chat}/>}
                </div>
              })}
            </details>
          })}
        </section>}
        {selection && <div className="selection-preview"><QuotesIcon aria-hidden="true"/><span>{selection}</span><button className="mini-button" type="button" aria-label="Снять выделение фрагмента" onClick={() => { setSelection(''); setCopyState(''); window.getSelection()?.removeAllRanges() }}>×</button></div>}
        <div className="message-actions">
          <button type="button" className="icon-button" aria-label={selection ? 'Скопировать фрагмент' : 'Скопировать ответ'} title={selection ? 'Скопировать фрагмент' : 'Скопировать ответ'} onClick={() => { void copy() }}><CopyIcon/></button>
          <button type="button" className="icon-button" aria-label="Процитировать" title="Процитировать" onClick={() => onQuote(selection || item.text, false)}><QuotesIcon/></button>
          <button type="button" className="icon-button" aria-label="Задать вопрос по ответу" title="Задать вопрос по ответу" onClick={() => onQuote(selection || item.text, true)}><ChatTextIcon/></button>
          <small aria-live="polite">{copyState}</small>
        </div>
      </>}
    </div>
  </article>
}
