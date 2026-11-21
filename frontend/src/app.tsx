import { ArrowRightIcon } from '@phosphor-icons/react/dist/icons/ArrowRight'
import { BuildingsIcon } from '@phosphor-icons/react/dist/icons/Buildings'
import { CalendarDotsIcon } from '@phosphor-icons/react/dist/icons/CalendarDots'
import { ChatCircleTextIcon } from '@phosphor-icons/react/dist/icons/ChatCircleText'
import { DetectiveIcon } from '@phosphor-icons/react/dist/icons/Detective'
import { GearIcon } from '@phosphor-icons/react/dist/icons/Gear'
import { GraduationCapIcon } from '@phosphor-icons/react/dist/icons/GraduationCap'
import { PlusIcon } from '@phosphor-icons/react/dist/icons/Plus'
import { SidebarSimpleIcon } from '@phosphor-icons/react/dist/icons/SidebarSimple'
import { SignInIcon } from '@phosphor-icons/react/dist/icons/SignIn'
import { TrashIcon } from '@phosphor-icons/react/dist/icons/Trash'
import { XIcon } from '@phosphor-icons/react/dist/icons/X'
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { api, errorText, type FileItem, type Profile, type Session, type Theme } from './api'
import { Composer, emptyDraft, type Draft } from './composer'
import { ChatMessage } from './messages'
import { AccountDialog, SettingsDialog } from './settings'
import { Avatar, BrandMark, Modal } from './ui'
import { useWorkspace } from './use-workspace'

const DemoStudio = lazy(() => import('./demo-studio'))

const prompts = [
  { label: 'Поступление', prompt: 'Какие ИТ-направления есть в НГТУ НЭТИ?', Icon: GraduationCapIcon, tone: 'lilac' },
  { label: 'Общежитие', prompt: 'Как первокурснику заселиться в общежитие НГТУ НЭТИ?', Icon: BuildingsIcon, tone: 'peach' },
  { label: 'Учёба', prompt: 'Где первокурснику НГУ смотреть расписание?', Icon: CalendarDotsIcon, tone: 'sage' },
]

const compactViewport = '(max-width: 760px)'

type WorkspaceProps = {
  profile: Profile | null
  guest: boolean
  theme: Theme
  onTheme: (theme: Theme) => void
  onGuest: () => void
  onLogin: () => void
  onProfile: (profile: Profile | null) => void
  demoEnabled: boolean
}

type StagedUpload = { chat: string; session: Session; files: Map<File, FileItem> }
type SendAttempt = { id: string; draft: Draft; chat: string }

function Workspace({ profile, guest, theme, onTheme, onGuest, onLogin, onProfile, demoEnabled }: WorkspaceProps) {
  const work = useWorkspace(guest, onProfile)
  const [compact, setCompact] = useState(() => matchMedia(compactViewport).matches)
  const [sidebar, setSidebar] = useState(!compact)
  const [settings, setSettings] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [busy, setBusy] = useState(false)
  const [animate, setAnimate] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const navigationToggle = useRef<HTMLButtonElement>(null)
  const sidebarClose = useRef<HTMLButtonElement>(null)
  const thread = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)
  const sending = useRef(false)
  const draftScope = useRef(0)
  const staged = useRef<StagedUpload | null>(null)
  const attempt = useRef<SendAttempt | null>(null)
  const lastMessage = work.chat?.messages.at(-1)
  const activeTitle = work.chats.find(({ id }) => id === work.active)?.title ?? 'Новый диалог'

  useEffect(() => {
    const viewport = matchMedia(compactViewport)
    const update = () => setCompact(viewport.matches)
    viewport.addEventListener('change', update)
    return () => viewport.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    const node = thread.current
    if (node && stickToBottom.current) node.scrollTop = node.scrollHeight
  }, [work.chat, draft.quote, draft.files.length])

  useEffect(() => {
    if (!sidebar || !compact) return
    sidebarClose.current?.focus()
    function escape(event: KeyboardEvent) {
      if (event.key === 'Escape' && !document.querySelector('dialog[open]')) {
        setSidebar(false)
        requestAnimationFrame(() => navigationToggle.current?.focus())
      }
    }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [sidebar, compact])

  function discardStaged() {
    const previous = staged.current
    staged.current = null
    if (!previous) return
    for (const file of previous.files.values()) {
      void api.removeFile(previous.session, previous.chat, file.id).catch(() => {})
    }
  }

  function openChat(id: string | null) {
    if (sending.current || (id !== null && id === work.active)) return
    draftScope.current++
    discardStaged()
    setDraft(emptyDraft)
    attempt.current = null
    setAnimate(null)
    work.setNotice('')
    stickToBottom.current = true
    work.setActiveChat(id)
    if (compact) setSidebar(false)
    if (!id) requestAnimationFrame(() => input.current?.focus())
  }

  function updateDraft(change: Partial<Draft>) {
    setDraft((old) => ({ ...old, ...change }))
  }

  async function transcribe(audio: Blob) {
    const scope = draftScope.current
    const session = await work.startSession()
    if (scope !== draftScope.current) return
    const { text } = await api.transcribe(session, audio)
    if (scope !== draftScope.current) return
    setDraft((old) => {
      const previous = old.text.trimEnd()
      const combined = previous ? `${previous} ${text}` : text
      return { ...old, text: combined }
    })
    input.current?.focus()
  }

  async function send() {
    if (sending.current || work.waiting || (!draft.text.trim() && !draft.files.length)) return
    sending.current = true
    setBusy(true)
    work.setNotice('')
    try {
      const session = await work.startSession()
      const chat = work.active ?? await work.createChat(session)
      if (staged.current?.chat !== chat) staged.current = { chat, session, files: new Map() }
      const uploaded = staged.current.files
      for (const [source, file] of uploaded) {
        if (!draft.files.includes(source)) {
          await api.removeFile(session, chat, file.id).catch(() => {})
          uploaded.delete(source)
        }
      }
      const fileIDs = []
      for (const file of draft.files) {
        const item = uploaded.get(file) ?? await api.upload(session, chat, file)
        uploaded.set(file, item)
        fileIDs.push(item.id)
      }
      if (attempt.current?.draft !== draft || attempt.current.chat !== chat) {
        attempt.current = { id: crypto.randomUUID?.() ?? Array.from(crypto.getRandomValues(new Uint8Array(24)), (byte) => byte.toString(16).padStart(2, '0')).join(''), draft, chat }
      }
      const answer = await api.send(session, chat, { requestId: attempt.current.id, text: draft.text, quote: draft.quote, fileIds: fileIDs })
      staged.current = null
      attempt.current = null
      stickToBottom.current = true
      setAnimate(answer.messageId)
      setDraft((current) => current === draft ? emptyDraft : { ...current, files: current.files.filter((file) => !draft.files.includes(file)) })
      await Promise.all([work.loadChat(session, chat), work.loadList(session)])
    } catch (error) {
      work.setNotice(errorText(error))
    } finally {
      setBusy(false)
      sending.current = false
    }
  }

  async function retry(id: string) {
    if (!work.active || !work.session || sending.current) return
    const chat = work.active
    const session = work.session
    sending.current = true
    setBusy(true)
    work.setNotice('')
    try {
      await api.retry(session, chat, id)
      setAnimate(id)
      await work.loadChat(session, chat)
    } catch (error) {
      work.setNotice(errorText(error))
    } finally {
      sending.current = false
      setBusy(false)
    }
  }

  async function cancel(id: string) {
    if (!work.active || !work.session) return
    try {
      await api.cancel(work.session, work.active, id)
    } catch (error) {
      work.setNotice(errorText(error))
    }
  }

  async function removeChat() {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      if (deleteTarget === work.active) {
        discardStaged()
        setDraft(emptyDraft)
        attempt.current = null
      }
      await work.removeChat(deleteTarget)
      setDeleteTarget(null)
    } catch (error) {
      work.setNotice(errorText(error))
      setDeleteTarget(null)
    } finally {
      setDeleting(false)
    }
  }

  function quote(text: string, question: boolean) {
    setDraft((old) => ({ ...old, quote: Array.from(text).slice(0, 1200).join(''), text: question && !old.text ? 'Поясни этот фрагмент: ' : old.text }))
    input.current?.focus()
  }

  return <div className={'shell' + (sidebar ? ' sidebar-open' : '')}>
    <button aria-label="Закрыть боковую панель" className="sidebar-backdrop" type="button" tabIndex={-1} onClick={() => setSidebar(false)}/>
    <aside className="sidebar" id="workspace-sidebar" aria-label="Навигация">
      <div className="sidebar-brand">
        <BrandMark/>
        <div><strong>NETI Next</strong><span>Помощник по вузам</span></div>
        <button ref={sidebarClose} type="button" className="icon-button close-sidebar" aria-label="Свернуть панель" title="Свернуть панель" onClick={() => { setSidebar(false); requestAnimationFrame(() => navigationToggle.current?.focus()) }}><SidebarSimpleIcon/></button>
      </div>
      <button type="button" className="new-chat" onClick={() => openChat(null)} disabled={busy}><PlusIcon weight="bold"/> Новый диалог</button>
      <div className="history">
        <h2>Диалоги</h2>
        {work.chats.length ? <nav aria-label="Список диалогов">
          {work.chats.map((item) => <div className={'history-row' + (item.id === work.active ? ' selected' : '')} key={item.id}>
            <button type="button" className="history-item" disabled={busy} aria-current={item.id === work.active ? 'page' : undefined} title={item.title} onClick={() => openChat(item.id)}>
              <ChatCircleTextIcon weight="duotone" aria-hidden="true"/><span>{item.title}</span>
            </button>
            <button type="button" className="mini-button delete-chat" disabled={busy} aria-label={'Удалить диалог ' + item.title} onClick={() => setDeleteTarget(item.id)}><TrashIcon/></button>
          </div>)}
        </nav> : <p>Здесь появятся ваши диалоги.</p>}
      </div>
      <div className="sidebar-footer">
        {demoEnabled && <a className="nav-button" href="#demo">Демо трассировки</a>}
        {profile && <button type="button" className="nav-button" disabled={busy} onClick={onGuest}><DetectiveIcon weight="duotone"/> {guest ? 'Вернуться в профиль' : 'Включить инкогнито'}</button>}
        <button type="button" className="nav-button" disabled={busy} onClick={() => setSettings(true)}><GearIcon weight="duotone"/> Настройки</button>
        <button type="button" className="profile-button" disabled={busy} onClick={profile && !guest ? () => setSettings(true) : profile ? onGuest : onLogin}>
          <Avatar/>
          <span><strong>{profile && !guest ? profile.name : 'Гостевой режим'}</strong><small>{profile && !guest ? profile.email : profile ? 'Вернуться в профиль' : 'Войти или создать профиль'}</small></span>
          {(!profile || guest) && <SignInIcon aria-hidden="true"/>}
        </button>
      </div>
    </aside>
    <main className="workspace" inert={sidebar && compact}>
      <header className="workspace-bar">
        <div className="workspace-title">
          <button ref={navigationToggle} type="button" className="icon-button" aria-label={sidebar ? 'Свернуть боковую панель' : 'Открыть боковую панель'} aria-expanded={sidebar} aria-controls="workspace-sidebar" onClick={() => setSidebar(!sidebar)}><SidebarSimpleIcon/></button>
          <span className="mobile-brand"><BrandMark/></span>
          <span>{work.active ? activeTitle : 'NETI Next'}</span>
        </div>
        {guest && <span className="guest-pill"><DetectiveIcon weight="duotone" aria-hidden="true"/> Инкогнито</span>}
      </header>
      <div className="thread-view" ref={thread} onScroll={(event) => {
        const node = event.currentTarget
        stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96
      }}>
        {work.active && work.chat ? <div className="thread-content" role="log" aria-label="История диалога" aria-live="polite">
          {work.session && work.chat.messages.map((item) => <ChatMessage key={item.id} item={item} chat={work.chat!.id} session={work.session!}
            latest={item.id === lastMessage?.id} animate={item.id === animate} onQuote={quote} onSuggestion={(text) => { updateDraft({ text }); input.current?.focus() }} onRetry={() => { void retry(item.id) }} onCancel={() => { void cancel(item.id) }}/>)}
        </div> : work.active ? <div className="thread-loading"><span className="spinner"/> Открываем диалог…</div> : <section className="welcome">
          <BrandMark className="welcome-mark"/>
          <h1>Что хотите узнать<br/>о вузах Новосибирска?</h1>
          <p>Поступление, учёба и студенческая жизнь.<br className="mobile-break"/> Ответы с цитатами из официальных материалов.</p>
          <div className="prompts" aria-label="Примеры вопросов">
            {prompts.map(({ label, prompt, Icon, tone }) => <button className="prompt" type="button" key={tone} title={prompt} onClick={() => { updateDraft({ text: prompt }); input.current?.focus() }}>
              <span className={'prompt-icon ' + tone}><Icon weight="duotone"/></span><span>{label}</span><ArrowRightIcon className="prompt-arrow" aria-hidden="true"/>
            </button>)}
          </div>
        </section>}
      </div>
      <div className="composer-dock">
        {work.notice && <div className="workspace-notice" role="alert"><span>{work.notice}</span><button type="button" className="mini-button" aria-label="Убрать уведомление" onClick={() => work.setNotice('')}><XIcon/></button></div>}
        <Composer draft={draft} update={updateDraft} inputRef={input} disabled={busy} pending={Boolean(work.waiting)} guest={guest} onTranscribe={transcribe} onSubmit={() => { void send() }}/>
      </div>
    </main>
    {settings && <SettingsDialog profile={guest ? null : profile} theme={theme} onTheme={onTheme} onProfile={onProfile}
      onClose={() => setSettings(false)} onClear={async () => { await work.clearChats(); discardStaged(); setDraft(emptyDraft); attempt.current = null }}
      onLogout={async () => { await api.logout(); onProfile(null) }} onDelete={() => onProfile(null)}/>}
    {deleteTarget && <Modal title="Удалить диалог?" onClose={() => { if (!deleting) setDeleteTarget(null) }}>
      <div className="settings-form">
        <p>Сообщения и вложения в этом диалоге будут удалены.</p>
        <div className="dialog-actions"><button type="button" className="soft-button" disabled={deleting} onClick={() => setDeleteTarget(null)}>Отмена</button>
          <button type="button" className="danger-button" disabled={deleting} onClick={() => { void removeChat() }}>Удалить диалог</button></div>
      </div>
    </Modal>}
  </div>
}

export function App() {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [guestMode, setGuestMode] = useState(false)
  const [showAuth, setShowAuth] = useState(false)
  const [authRead, setAuthRead] = useState(false)
  const [demoEnabled, setDemoEnabled] = useState(false)
  const [isDemo, setIsDemo] = useState(() => location.hash === '#demo')

  useEffect(() => {
    const update = () => setIsDemo(location.hash === '#demo')
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])

  useEffect(() => {
    let active = true
    if (profile && !guestMode) api.demoAccess().then(() => {
      if (active) setDemoEnabled(true)
    }).catch(() => {})
    return () => { active = false }
  }, [profile, guestMode])
  const [guestTheme, setGuestTheme] = useState<Theme>(() => matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')

  useEffect(() => {
    let alive = true
    api.profile().then((account) => { if (alive) setProfile(account) })
      .catch(() => {})
      .finally(() => { if (alive) setAuthRead(true) })
    return () => { alive = false }
  }, [])

  const guest = !profile || guestMode
  const theme = guest ? guestTheme : profile.theme
  useEffect(() => { document.documentElement.dataset.theme = theme }, [theme])

  const accountChanged = useCallback((next: Profile | null) => {
    setProfile(next)
    setDemoEnabled(false)
    if (!next) setGuestMode(false)
  }, [])

  if (!authRead) return <div className="app-loading" role="status"><BrandMark/><span>Открываем NETI Next…</span></div>

  if (isDemo && demoEnabled && !guest) return <Suspense fallback={<div className="app-loading" role="status">Открываем трассировку…</div>}><DemoStudio account={profile.name}/></Suspense>
  if (isDemo && !guest) return <main className="demo-studio"><a href="#">Вернуться в кабинет</a><p>Экран доступен назначенному демо-аккаунту.</p></main>

  return <>
    <Workspace demoEnabled={demoEnabled && !guest} key={(profile?.id ?? 'anonymous') + ':' + guest} profile={profile} guest={guest} theme={theme} onTheme={setGuestTheme}
      onGuest={() => setGuestMode(!guestMode)} onLogin={() => setShowAuth(true)} onProfile={accountChanged}/>
    {showAuth && <AccountDialog initial="login" onClose={() => setShowAuth(false)} onAccount={(account) => { setGuestMode(false); accountChanged(account); setShowAuth(false) }}/>}
  </>
}
