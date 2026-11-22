import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react'
import { PigeonIcon } from './pigeon-icon'
import { api, errorText, safeURL, type Artifact, type Citation, type FileItem, type Message, type Session } from './api'
import { AsciiPigeon } from './pigeon-ascii'
import { citationReferences } from './citations'
import { downloadDeadline } from './calendar'
import { ChatBlockView } from './chat-blocks'
import { RunActivity } from './run-activity'
import { Modal } from './ui'

const answerDate = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' })

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
      {image ? <PigeonIcon name="photo"/> : <PigeonIcon name="file"/>}
      <span title={file.name}>{file.name}</span>
      {(image || audio) && <button type="button" disabled={busy} onClick={() => { void open(false) }}>{preview ? 'Скрыть' : image ? 'Просмотр' : 'Прослушать'}</button>}
      <button type="button" className="mini-button" aria-label={'Скачать ' + file.name} disabled={busy} onClick={() => { void open(true) }}><PigeonIcon name="download"/></button>
    </div>
    {preview && (image ? <img src={preview} alt={file.name}/> : <audio controls src={preview} aria-label={file.name}/>)}
    {error && <small role="alert">{error}</small>}
  </div>
}

function CitationPhoto({ citation, session, chat }: { citation: Citation; session: Session; chat: string }) {
  const [photo, setPhoto] = useState('')
  const [error, setError] = useState('')
  const fileID = citation.attachment_id

  useEffect(() => {
    if (!fileID) return
    let active = true
    let source = ''
    void api.download(session, chat, fileID).then((blob) => {
      if (!active) return
      source = URL.createObjectURL(blob)
      setPhoto(source)
    }).catch((cause: unknown) => {
      if (active) setError(errorText(cause))
    })
    return () => {
      active = false
      if (source) URL.revokeObjectURL(source)
    }
  }, [chat, fileID, session])

  const box = citation.region
  return <>
    {!photo && !error && <small role="status">Загружаем фото…</small>}
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

function ArtifactCard({ item }: { item: Artifact }) {
  const url = safeURL(item.url)
  if (item.kind === 'image' && url) {
    return <figure className="artifact-card"><a href={url} target="_blank" rel="noreferrer"><img src={url} alt={item.title} loading="lazy" referrerPolicy="no-referrer"/></a>
      <figcaption>{item.title}</figcaption></figure>
  }
  if (item.kind === 'quote' && item.text) {
    return <div className="artifact-card"><strong>{item.title}</strong><blockquote>{item.text}</blockquote>{url && <a href={url} target="_blank" rel="noreferrer">Открыть источник <PigeonIcon name="external"/></a>}</div>
  }
  if (!url) return null
  return <a className="artifact-card artifact-link" href={url} target="_blank" rel="noreferrer">
    <PigeonIcon name="file"/><span>{item.title}</span><PigeonIcon name="external"/>
  </a>
}

type Props = {
  item: Message
  chat: string
  session: Session
  latest: boolean
  mascot: boolean
  animate: boolean
  readOnly: boolean
  onRetry: () => void
}

export function ChatMessage({ item, chat, session, latest, mascot, animate, readOnly, onRetry }: Props) {
  const [copyState, setCopyState] = useState('')
  const [typing, setTyping] = useState(false)
  const [selectedPhoto, setSelectedPhoto] = useState<Citation | null>(null)
  const assistant = item.role === 'assistant'

  const evidence = item.citations
  const references = useMemo(() => citationReferences(item.id, evidence ?? [], readOnly ? undefined : setSelectedPhoto), [item.id, evidence, readOnly])

  async function copy() {
    try {
      await navigator.clipboard.writeText(item.text)
      setCopyState('Ответ скопирован')
    } catch {
      setCopyState('Не удалось скопировать')
    }
  }

  if (!assistant) return <article className="user-message" aria-label="Ваше сообщение">
    {item.quote && <blockquote>{item.quote}</blockquote>}
    {item.text && <p>{item.text}</p>}
    {!!item.attachments?.length && <div className="attachment-list">
      {item.attachments.map((file) => readOnly ? <span className="draft-file" key={file.id}><PigeonIcon name="file"/> {file.name}</span> :
        <AttachedFile file={file} key={file.id} session={session} chat={chat}/>)}
    </div>}
  </article>

  const working = item.state === 'working'
  const answeredAt = Date.parse(item.createdAt) + (item.durationMs ?? 0)

  return <article className={'assistant-message' + (mascot ? ' with-mascot' : '') + (working ? ' awaiting-answer' : '')} aria-label="Ответ Пигеона">
    <RunActivity item={item}/>
    {mascot ? <div className="assistant-mascot"><AsciiPigeon speaking={typing}/></div> : null}
    {!working && <div className={'assistant-content' + (item.state !== 'failed' || mascot ? ' speech-bubble' : '') + (animate && item.state === 'complete' ? ' reveal' : '')}>
      <header className="message-heading"><strong>Пигеон</strong></header>
      {item.state === 'failed' && <div className="answer-error">
        <p role="alert"><PigeonIcon name="warning"/> {item.problem?.detail ?? item.error ?? 'Не удалось получить ответ.'}</p>
        {latest && !readOnly && <button type="button" className="soft-button" onClick={onRetry}><PigeonIcon name="retry"/> Повторить запрос</button>}
      </div>}
      {item.state === 'complete' && <>
        <div className="markdown">
          <Suspense fallback={<p>Оформляем ответ…</p>}><MarkdownContent text={item.text} references={references} animate={animate} onTyping={setTyping}/></Suspense>
        </div>
        {item.blocks?.map((block, index) => block.kind !== 'gap' && <ChatBlockView key={index} block={block} sources={references}/>)}
        {!!item.artifacts?.length && <div className="artifact-grid">{item.artifacts.map((artifact, index) => <ArtifactCard item={artifact} key={index}/>)}</div>}
        {!!item.events?.length && <div className="deadline-actions" aria-label="Сроки для календаря">
          {item.events.map((event, index) => {
            const group = (evidence ?? []).filter((source) => source.ref === event.citation && source.url)
            const citation = group[0] && { ...group[0], quote: group.map((source) => source.quote).join('\n') }
            return citation && <button type="button" className="soft-button" key={index}
              onClick={() => downloadDeadline(event, citation, `${item.id}-${index}`)}>
              <PigeonIcon name="download"/> {event.title} · {event.date.split('-').reverse().join('.')} (.ics)
            </button>
          })}
        </div>}
        {!readOnly && selectedPhoto && <Modal title="Цитата из фото" onClose={() => setSelectedPhoto(null)}>
          <div className="settings-form"><blockquote>{selectedPhoto.quote}</blockquote>
            <CitationPhoto citation={selectedPhoto} session={session} chat={chat}/>
          </div>
        </Modal>}
        <div className="message-actions">
          <button type="button" className="icon-button" aria-label="Скопировать ответ" title="Скопировать ответ" onClick={() => { void copy() }}><PigeonIcon name="copy"/></button>
          {Number.isFinite(answeredAt) && <time dateTime={new Date(answeredAt).toISOString()} title="Дата и время ответа">{answerDate.format(answeredAt)}</time>}
          <small role="status">{copyState}</small>
        </div>
      </>}
    </div>}
    {item.state === 'complete' && item.blocks?.map((block, index) => block.kind === 'gap' &&
      <p className="answer-guidance" key={index}>{block.note}</p>)}
  </article>
}
