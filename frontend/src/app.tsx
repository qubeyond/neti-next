import { BuildingsIcon } from '@phosphor-icons/react/dist/icons/Buildings'
import { CalendarDotsIcon } from '@phosphor-icons/react/dist/icons/CalendarDots'
import { ChatCircleTextIcon } from '@phosphor-icons/react/dist/icons/ChatCircleText'
import { DetectiveIcon } from '@phosphor-icons/react/dist/icons/Detective'
import { GearIcon } from '@phosphor-icons/react/dist/icons/Gear'
import { GraduationCapIcon } from '@phosphor-icons/react/dist/icons/GraduationCap'
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/icons/MagnifyingGlass'
import { PlusIcon } from '@phosphor-icons/react/dist/icons/Plus'
import { SidebarSimpleIcon } from '@phosphor-icons/react/dist/icons/SidebarSimple'
import { TrashIcon } from '@phosphor-icons/react/dist/icons/Trash'
import { XIcon } from '@phosphor-icons/react/dist/icons/X'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, errorText, type FileItem, type Profile, type Session, type Theme } from './api'
import { PigeonVolume } from './pigeon-volume'
import { Composer, emptyDraft, type Draft } from './composer'
import { ChatMessage } from './messages'
import { ProfileMenu } from './profile-menu'
import { AccountDialog, SettingsDialog } from './settings'
import { BrandMark, Modal } from './ui'
import { useWorkspace } from './use-workspace'

const prompts = [
  { label: 'Поступление', prompt: 'Какие ИТ-направления есть в НГТУ НЭТИ?', Icon: GraduationCapIcon, tone: 'pink' },
  { label: 'Общежитие', prompt: 'Как первокурснику заселиться в общежитие НГТУ НЭТИ?', Icon: BuildingsIcon, tone: 'peach' },
  { label: 'Учёба', prompt: 'Где первокурснику НГУ смотреть расписание?', Icon: CalendarDotsIcon, tone: 'blue' },
]

const compactViewport = '(max-width: 760px)'

type WorkspaceProps = {
  profile: Profile | null
  guest: boolean
  incognito: boolean
  theme: Theme
  onTheme: (theme: Theme) => void
  onGuest: () => void
  onLogin: () => void
  onProfile: (profile: Profile | null) => void
}

type StagedUpload = { chat: string; session: Session; files: Map<File, FileItem> }
type SendAttempt = { id: string; draft: Draft; chat: string }

function Workspace({ profile, guest, incognito, theme, onTheme, onGuest, onLogin, onProfile }: WorkspaceProps) {
  const work = useWorkspace(guest, onProfile)
  const [compact, setCompact] = useState(() => matchMedia(compactViewport).matches)
  const [sidebar, setSidebar] = useState(!compact && !incognito)
  const [search, setSearch] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [settings, setSettings] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft>(emptyDraft)
  const [busy, setBusy] = useState(false)
  const [animate, setAnimate] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const navigationToggle = useRef<HTMLButtonElement>(null)
  const sidebarClose = useRef<HTMLButtonElement>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const searchToggle = useRef<HTMLButtonElement>(null)
  const thread = useRef<HTMLDivElement>(null)
  const threadContent = useRef<HTMLDivElement>(null)
  const dock = useRef<HTMLDivElement>(null)
  const composeOrigin = useRef<DOMRect | null>(null)
  const stickToBottom = useRef(true)
  const sending = useRef(false)
  const staged = useRef<StagedUpload | null>(null)
  const attempt = useRef<SendAttempt | null>(null)
  const lastMessage = work.chat?.messages.at(-1)
  const chatId = work.chat?.id
  const showChat = Boolean(work.active || busy)
  const searchTerm = search.trim().toLocaleLowerCase('ru')
  const visibleChats = work.chats.filter(({ title }) => title.toLocaleLowerCase('ru').includes(searchTerm))

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
    const content = threadContent.current
    const viewport = thread.current
    if (!chatId || !content || !viewport) return
    const observer = new ResizeObserver(() => {
      if (stickToBottom.current) viewport.scrollTop = viewport.scrollHeight
    })
    observer.observe(content)
    return () => observer.disconnect()
  }, [chatId])

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

  useLayoutEffect(() => {
    const origin = composeOrigin.current
    composeOrigin.current = null
    const composer = dock.current?.querySelector('.composer-area')
    if (!showChat || !origin || !composer || matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const target = composer.getBoundingClientRect()
    const animation = composer.animate([
      { transform: 'translate(' + (origin.left - target.left) + 'px,' + (origin.top - target.top) + 'px)' },
      { transform: 'translate(0,0)' },
    ], { duration: 440, easing: 'cubic-bezier(.2,.8,.24,1)' })
    return () => animation.cancel()
  }, [showChat])

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

  async function send() {
    if (sending.current || work.waiting || (!draft.text.trim() && !draft.files.length)) return
    if (!work.active) composeOrigin.current = dock.current?.querySelector('.composer-area')?.getBoundingClientRect() ?? null
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

  async function logout() {
    await api.logout()
    onProfile(null)
  }

  function closeSidebar() {
    setSidebar(false)
    requestAnimationFrame(() => navigationToggle.current?.focus())
  }

  return <div className={'shell' + (sidebar ? ' sidebar-open' : '') + (incognito ? ' shell-private' : '')}>
    {!incognito && <>
      <button aria-label="Закрыть боковую панель" className="sidebar-backdrop" type="button" tabIndex={-1} onClick={closeSidebar}/>
      <aside className="sidebar" id="workspace-sidebar" aria-label="Навигация">
        <div className="sidebar-content">
          <div className="sidebar-brand">
            <BrandMark/>
            <strong>Пигеон</strong>
            <button ref={searchToggle} type="button" className="icon-button search-toggle" aria-label="Поиск чатов" title="Поиск чатов" aria-expanded={searchOpen} aria-controls={searchOpen ? 'history-search' : undefined} onClick={() => {
              if (searchOpen) setSearch('')
              else requestAnimationFrame(() => searchInput.current?.focus())
              setSearchOpen(!searchOpen)
            }}><MagnifyingGlassIcon/></button>
            <button ref={sidebarClose} type="button" className="icon-button close-sidebar" aria-label="Свернуть панель" title="Свернуть панель" onClick={closeSidebar}><SidebarSimpleIcon/></button>
          </div>
          <button type="button" className="new-chat" onClick={() => openChat(null)} disabled={busy}><PlusIcon weight="bold"/> Новый чат</button>
          {searchOpen && <label className="history-search" id="history-search"><input ref={searchInput} type="search" value={search} aria-label="Найти чат" placeholder="Найти чат" onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => {
            if (event.key !== 'Escape') return
            event.stopPropagation()
            setSearch('')
            setSearchOpen(false)
            searchToggle.current?.focus()
          }}/></label>}
          <div className="history">
            <h2>Диалоги</h2>
            {visibleChats.length ? <nav aria-label="Список диалогов">
              {visibleChats.map((item) => <div className={'history-row' + (item.id === work.active ? ' selected' : '')} key={item.id}>
                <button type="button" className="history-item" disabled={busy} aria-current={item.id === work.active ? 'page' : undefined} title={item.title} onClick={() => openChat(item.id)}>
                  <ChatCircleTextIcon weight="duotone" aria-hidden="true"/><span>{item.title}</span>
                </button>
                <button type="button" className="mini-button delete-chat" disabled={busy} aria-label={'Удалить диалог ' + item.title} onClick={() => setDeleteTarget(item.id)}><TrashIcon/></button>
              </div>)}
            </nav> : <p>{searchTerm ? 'Диалоги не найдены.' : 'Здесь появятся ваши диалоги.'}</p>}
          </div>
          <div className="sidebar-footer">
            <ProfileMenu profile={profile} disabled={busy} onSettings={() => setSettings(true)}
              onLogout={() => { void logout().catch((error) => work.setNotice(errorText(error))) }}/>
          </div>
        </div>
      </aside>
    </>}
    <main className="workspace" inert={sidebar && compact}>
      <header className="workspace-bar">
        <div className="workspace-title">
          {incognito ? <span className="private-mode-marker" aria-label="Инкогнито" title="Инкогнито"><DetectiveIcon className="private-icon" weight="duotone" aria-hidden="true"/></span> :
            <button ref={navigationToggle} type="button" className="icon-button" aria-label={sidebar ? 'Свернуть боковую панель' : 'Открыть боковую панель'} aria-expanded={sidebar} aria-controls="workspace-sidebar" onClick={() => setSidebar(!sidebar)}><SidebarSimpleIcon/></button>}
        </div>
        <div className="workspace-actions">
          {!profile && <button type="button" className="login-button" onClick={onLogin}>Войти</button>}
          {incognito ? <>
            {work.active && <button type="button" className="icon-button" aria-label="Новый чат" title="Новый чат" disabled={busy} onClick={() => openChat(null)}><PlusIcon/></button>}
            <button type="button" className="icon-button" aria-label="Настройки" title="Настройки" onClick={() => setSettings(true)}><GearIcon/></button>
            <button type="button" className="icon-button" aria-label="Выйти из инкогнито" title="Выйти из инкогнито" disabled={busy} onClick={onGuest}><XIcon/></button>
          </> : <span className="tooltip-anchor"><button type="button" className="icon-button private-button" aria-label="Инкогнито" aria-describedby="incognito-tooltip" disabled={busy} onClick={onGuest}><DetectiveIcon weight="duotone" aria-hidden="true"/></button><span className="control-tooltip" id="incognito-tooltip" role="tooltip">Инкогнито</span></span>}
        </div>
      </header>
      <div className={'workspace-stage' + (showChat ? ' chat-stage' : ' landing-stage') + (!incognito ? ' has-mascot' : '') + (!showChat && !incognito ? ' landing-with-mascot' : '')}>
        <div className="thread-view" ref={thread} hidden={!showChat} onScroll={(event) => {
          const node = event.currentTarget
          stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96
        }}>
          {work.active && work.chat ? <div className="thread-content" ref={threadContent} role="log" aria-label="История диалога" aria-live="polite">
            {work.session && work.chat.messages.map((item) => <ChatMessage key={item.id} item={item} chat={work.chat!.id} session={work.session!}
              latest={item.id === lastMessage?.id} mascot={!incognito && item.id === lastMessage?.id} animate={item.id === animate} onQuote={quote} onRetry={() => { void retry(item.id) }}/>)}
          </div> : showChat && <div className="thread-loading"><span className="spinner"/> {busy ? 'Отправляем вопрос…' : 'Открываем диалог…'}</div>}
        </div>
        <div className="composer-dock" ref={dock}>
          {!showChat && <div className="welcome">
            {incognito ? <h1>Диалог <span>инкогнито</span></h1> : <h1>Вузы <span>Новосибирска</span></h1>}
            {!incognito && <p>Поступление, учёба и общежития. Ответы с источниками.</p>}
          </div>}
          {work.notice && <div className="workspace-notice" role="alert"><span>{work.notice}</span><button type="button" className="mini-button" aria-label="Убрать уведомление" onClick={() => work.setNotice('')}><XIcon/></button></div>}
          <Composer draft={draft} update={updateDraft} inputRef={input} disabled={busy} pending={Boolean(work.waiting)} onSubmit={() => { void send() }}/>
          {!showChat && !incognito && <div className="prompts" aria-label="Примеры вопросов">
            {prompts.map(({ label, prompt, Icon, tone }) => <button className={'prompt ' + tone} type="button" key={label} title={prompt} onClick={() => { updateDraft({ text: prompt }); input.current?.focus() }}>
              <Icon weight="duotone" aria-hidden="true"/>{label}
            </button>)}
          </div>}
        </div>
        {!showChat && !incognito && <PigeonVolume/>}
      </div>
    </main>
    {settings && <SettingsDialog profile={guest ? null : profile} theme={theme} onTheme={onTheme} onProfile={onProfile}
      onClose={() => setSettings(false)} onClear={async () => { await work.clearChats(); discardStaged(); setDraft(emptyDraft); attempt.current = null }}
      onLogout={logout} onDelete={() => onProfile(null)}/>}
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
  const [guestTheme, setGuestTheme] = useState<Theme>('light')

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
    if (!next) setGuestMode(false)
  }, [])

  if (!authRead) return <div className="app-loading" role="status"><BrandMark/><span>Открываем Пигеон…</span></div>

  return <>
    <Workspace key={(profile?.id ?? 'anonymous') + ':' + guestMode} profile={profile} guest={guest} incognito={guestMode} theme={theme} onTheme={setGuestTheme}
      onGuest={() => setGuestMode(!guestMode)} onLogin={() => setShowAuth(true)} onProfile={accountChanged}/>
    {showAuth && <AccountDialog initial="login" onClose={() => setShowAuth(false)} onAccount={(account) => { setGuestMode(false); setProfile(account); setShowAuth(false) }}/>}
  </>
}
