import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { PigeonIcon } from './pigeon-icon'
import { api, errorText, type FileItem, type Profile, type Session } from './api'
import { PigeonVolume } from './pigeon-volume'
import { Composer, emptyDraft, type Draft } from './composer'
import { ChatMessage } from './messages'
import { ProfileMenu } from './profile-menu'
import { SettingsDialog } from './settings'
import { AccountPage } from './account-page'
import { BrandMark, Modal } from './ui'
import { universityPrompts } from './university-prompts'
import { useWorkspace } from './use-workspace'

const DemoStudio = lazy(() => import('./demo-studio'))
const TracePreview = import.meta.env.DEV ? lazy(() => import('./trace-preview')) : null

const compactViewport = '(max-width: 760px)'

type WorkspaceProps = {
  profile: Profile | null
  guest: boolean
  incognito: boolean
  onGuest: () => void
  onProfile: (profile: Profile | null) => void
  demoEnabled: boolean
}

type StagedUpload = { chat: string; session: Session; files: Map<File, FileItem> }
type SendAttempt = { id: string; draft: Draft; chat: string }

function Workspace({ profile, guest, incognito, onGuest, onProfile, demoEnabled }: WorkspaceProps) {
  const work = useWorkspace(guest, onProfile, guest && !incognito)
  const [compact, setCompact] = useState(() => matchMedia(compactViewport).matches)
  const [sidebar, setSidebar] = useState(!guest && !compact)
  const [search, setSearch] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [settings, setSettings] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [draft, setDraft] = useState<Draft>(() => {
    const id = new URLSearchParams(location.search).get('example')
    const example = universityPrompts.find((item) => item.id === id)
    return example ? { ...emptyDraft, text: example.prompt } : emptyDraft
  })
  const [busy, setBusy] = useState(false)
  const [cancelling, setCancelling] = useState(false)
  const [animate, setAnimate] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const input = useRef<HTMLTextAreaElement>(null)
  const navigationToggle = useRef<HTMLButtonElement>(null)
  const sidebarClose = useRef<HTMLButtonElement>(null)
  const searchInput = useRef<HTMLInputElement>(null)
  const searchToggle = useRef<HTMLButtonElement>(null)
  const thread = useRef<HTMLDivElement>(null)
  const dock = useRef<HTMLDivElement>(null)
  const composeOrigin = useRef<DOMRect | null>(null)
  const stickToBottom = useRef(true)
  const sending = useRef(false)
  const draftScope = useRef(0)
  const staged = useRef<StagedUpload | null>(null)
  const attempt = useRef<SendAttempt | null>(null)
  const lastMessage = work.chat?.messages.at(-1)
  const lastAssistant = work.chat?.messages.filter((item) => item.role === 'assistant').at(-1)
  const workingMessage = lastMessage?.state === 'working' ? lastMessage : null
  const chatId = work.chat?.id
  const showChat = Boolean(work.active || work.chat || busy)
  const searchTerm = search.trim().toLocaleLowerCase('ru')
  const visibleChats = work.chats.filter(({ title }) => title.toLocaleLowerCase('ru').includes(searchTerm))
  const archivedIDs = new Set(work.archivedMessages?.map(({ id }) => id))

  useEffect(() => {
    const viewport = matchMedia(compactViewport)
    const update = () => setCompact(viewport.matches)
    viewport.addEventListener('change', update)
    return () => viewport.removeEventListener('change', update)
  }, [])

  useEffect(() => {
    const node = thread.current
    if (node && stickToBottom.current) node.scrollTop = node.scrollHeight
  }, [chatId, lastMessage?.id, draft.quote, draft.files.length])

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
    if (sending.current || work.waiting || Array.from(draft.text).length > 2000 || (!draft.text.trim() && !draft.files.length)) return
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

  async function cancel(id: string) {
    if (!work.active || !work.session || cancelling) return
    const chat = work.active
    const session = work.session
    setCancelling(true)
    try {
      await api.cancel(session, chat, id)
      await work.loadChat(session, chat)
    } catch (error) {
      work.setNotice(errorText(error))
    } finally {
      setCancelling(false)
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

  async function logout() {
    await api.logout()
    window.location.replace('/register')
  }

  function closeSidebar() {
    setSidebar(false)
    requestAnimationFrame(() => navigationToggle.current?.focus())
  }

  return <div className={'shell' + (sidebar ? ' sidebar-open' : '') + (incognito ? ' shell-private' : !profile ? ' shell-guest' : '')}>
    {profile && !incognito && <>
      <button aria-label="Закрыть боковую панель" className="sidebar-backdrop" type="button" tabIndex={-1} onClick={closeSidebar}/>
      <aside className="sidebar" id="workspace-sidebar" aria-label="Навигация">
        <div className="sidebar-content">
          <div className="sidebar-brand">
            <a className="workspace-brand" href="/"><BrandMark/><strong>Пигеон</strong></a>
            <button ref={searchToggle} type="button" className="icon-button search-toggle" aria-label="Поиск чатов" title="Поиск чатов" aria-expanded={searchOpen} aria-controls={searchOpen ? 'history-search' : undefined} onClick={() => {
              if (searchOpen) setSearch('')
              else requestAnimationFrame(() => searchInput.current?.focus())
              setSearchOpen(!searchOpen)
            }}><PigeonIcon name="search"/></button>
            <button ref={sidebarClose} type="button" className="icon-button close-sidebar" aria-label="Свернуть панель" title="Свернуть панель" onClick={closeSidebar}><PigeonIcon name="sidebar"/></button>
          </div>
          <button type="button" className="new-chat" onClick={() => openChat(null)} disabled={busy}><PigeonIcon name="plus"/> Новый чат</button>
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
              {visibleChats.map((item) => <div className={'history-row' + (item.id === chatId ? ' selected' : '')} key={item.id}>
                <button type="button" className="history-item" disabled={busy} aria-current={item.id === chatId ? 'page' : undefined} title={item.title} onClick={() => openChat(item.id)}>
                  <PigeonIcon name="chat"/><span>{item.title}</span>
                </button>
                <button type="button" className="mini-button delete-chat" disabled={busy} aria-label={'Удалить диалог ' + item.title} onClick={() => setDeleteTarget(item.id)}><PigeonIcon name="trash"/></button>
              </div>)}
            </nav> : <p>{searchTerm ? 'Диалоги не найдены.' : 'Здесь появятся ваши диалоги.'}</p>}
          </div>
          <div className="sidebar-footer">
            {demoEnabled && <a className="nav-button" href="#demo">Трассировка</a>}
            <ProfileMenu profile={profile} disabled={busy} onSettings={() => setSettings(true)}
              onLogout={() => { void logout().catch((error) => work.setNotice(errorText(error))) }}/>
          </div>
        </div>
      </aside>
    </>}
    <main className="workspace" inert={sidebar && compact}>
      {profile && <header className="workspace-bar">
        <div className="workspace-title">
          {incognito ? <span className="private-mode-marker" aria-label="Инкогнито" title="Инкогнито"><PigeonIcon name="private" className="private-icon"/></span> :
            <button ref={navigationToggle} type="button" className="icon-button" aria-label={sidebar ? 'Свернуть боковую панель' : 'Открыть боковую панель'} aria-expanded={sidebar} aria-controls="workspace-sidebar" onClick={() => setSidebar(!sidebar)}><PigeonIcon name="sidebar"/></button>}
        </div>
        <div className="workspace-actions">
          {incognito ? <>
            {work.active && <button type="button" className="icon-button" aria-label="Новый чат" title="Новый чат" disabled={busy} onClick={onGuest}><PigeonIcon name="plus"/></button>}
            <button type="button" className="icon-button" aria-label="Настройки" title="Настройки" onClick={onGuest}><PigeonIcon name="settings"/></button>
            <button type="button" className="icon-button" aria-label="Выйти из инкогнито" title="Выйти из инкогнито" disabled={busy} onClick={onGuest}><PigeonIcon name="close"/></button>
          </> : <span className="tooltip-anchor"><button type="button" className="icon-button private-button" aria-label="Инкогнито" aria-describedby="incognito-tooltip" disabled={busy} onClick={onGuest}><PigeonIcon name="private"/></button><span className="control-tooltip" id="incognito-tooltip" role="tooltip">Инкогнито</span></span>}
        </div>
      </header>}
      {!profile && <header className="workspace-bar">
        <div className="workspace-actions">
          <button type="button" className="icon-button" aria-label="Настройки" title="Настройки" onClick={() => setSettings(true)}><PigeonIcon name="settings"/></button>
          <a className="soft-button guest-login" href="/login">Войти</a>
        </div>
      </header>}
      <div className={'workspace-stage' + (showChat ? ' chat-stage' : ' landing-stage') + (!incognito ? ' has-mascot' : '') + (!showChat && !incognito ? ' landing-with-mascot' : '')}>
        <div className="thread-view" ref={thread} hidden={!showChat} onScroll={(event) => {
          const node = event.currentTarget
          stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 96
        }}>
          {work.chat ? <div className="thread-content" role="log" aria-label="История диалога" aria-live="polite">
            {work.chat.messages.map((item) => <ChatMessage key={item.id} item={item} chat={work.chat!.id} session={work.session ?? {}}
              readOnly={archivedIDs.has(item.id) || !work.session}
              latest={item.id === lastMessage?.id} mascot={!incognito && item.id === lastAssistant?.id} animate={item.id === animate} onRetry={() => { void retry(item.id) }}/>)}
            {!busy && !work.waiting && !!lastMessage?.suggestions?.length && <div className="reply-suggestions" role="group" aria-label="Варианты ответа">
              {lastMessage.suggestions.map((suggestion) => <button type="button" className="soft-button" key={suggestion}
                onClick={() => { updateDraft({ text: suggestion }); input.current?.focus() }}>{suggestion}</button>)}
            </div>}
          </div> : showChat && <div className="thread-loading"><span className="spinner"/> {busy ? 'Отправляем вопрос…' : 'Открываем диалог…'}</div>}
        </div>
        <div className="composer-dock" ref={dock}>
          {!showChat && <div className="welcome">
            {incognito ? <h1>Диалог <span>инкогнито</span></h1> : <h1>Агент по вузам</h1>}
            {!incognito && <p>Помогает с поступлением и учёбой по всей России.</p>}
          </div>}
          {work.notice && <div className="workspace-notice" role="alert"><span>{work.notice}</span><button type="button" className="mini-button" aria-label="Убрать уведомление" onClick={() => work.setNotice('')}><PigeonIcon name="close"/></button></div>}
          <Composer draft={draft} update={updateDraft} inputRef={input} disabled={busy} pending={Boolean(work.waiting)} compact={compact}
            onTranscribe={transcribe} onSubmit={() => { void send() }} cancelling={cancelling}
            onCancel={workingMessage ? () => { void cancel(workingMessage.id) } : undefined}/>
          {!showChat && !incognito && <div className="prompts" aria-label="Примеры вопросов">
            {universityPrompts.map(({ label, prompt, icon }) => <button className="prompt" type="button" key={label} title={prompt} onClick={() => { updateDraft({ text: prompt }); input.current?.focus() }}>
              <PigeonIcon name={icon}/>{label}
            </button>)}
          </div>}
        </div>
        {!showChat && !incognito && <PigeonVolume/>}
      </div>
    </main>
    {settings && <SettingsDialog profile={guest ? null : profile} onProfile={onProfile}
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

function WorkspaceApp() {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [guestMode, setGuestMode] = useState(false)
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

  useEffect(() => {
    let alive = true
    api.profile().then((account) => { if (alive) setProfile(account) })
      .catch(() => {})
      .finally(() => { if (alive) setAuthRead(true) })
    return () => { alive = false }
  }, [])

  const guest = !profile || guestMode
  const accountChanged = useCallback((next: Profile | null) => {
    setProfile(next)
    setDemoEnabled(false)
    if (!next) setGuestMode(false)
  }, [])

  if (!authRead) return <div className="app-loading" role="status"><BrandMark/><span>Открываем Пигеон…</span></div>

  if (isDemo && demoEnabled && !guest) return <Suspense fallback={<div className="app-loading" role="status">Открываем трассировку…</div>}><DemoStudio account={profile.name}/></Suspense>
  if (isDemo && !guest) return <main className="demo-studio"><a href="#">Вернуться в кабинет</a><p>Экран доступен назначенному демо-аккаунту.</p></main>

  return <Workspace demoEnabled={demoEnabled && !guest} key={(profile?.id ?? 'anonymous') + ':' + guestMode} profile={profile} guest={guest} incognito={guestMode}
    onGuest={() => setGuestMode(!guestMode)} onProfile={accountChanged}/>
}

export function App() {
  const path = location.pathname.replace(/\/+$/, '')
  if (TracePreview && path === '/trace-preview') return <Suspense fallback={null}><TracePreview/></Suspense>
  if (path === '/login') return <AccountPage mode="login"/>
  if (path === '/register') return <AccountPage mode="register"/>
  return <WorkspaceApp/>
}
