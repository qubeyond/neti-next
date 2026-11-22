import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, errorText, type Chat, type ChatEvent, type ChatSummary, type Profile, type Session } from './api'
import { joinGuestChats, readGuestCache, writeGuestCache, type GuestCache } from './guest-cache'

const accountSession: Session = {}

export function useWorkspace(guest: boolean, onProfile: (profile: Profile | null) => void, keepGuest: boolean) {
  const [initial] = useState(() => keepGuest ? readGuestCache() : {})
  const cache = useRef(initial)
  const [session, setSession] = useState<Session | null>(guest ? initial.token ? { guestToken: initial.token } : null : accountSession)
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [chat, setChat] = useState<Chat | null>(initial.chat ?? null)
  const [archive, setArchive] = useState(initial.archive)
  const [active, setActive] = useState<string | null>(initial.active ?? null)
  const [notice, setNotice] = useState('')
  const [connected, setConnected] = useState(false)
  const sessionRef = useRef(session)
  const guestStart = useRef<Promise<Session> | null>(null)
  const activeRef = useRef(active)
  const chatRequest = useRef(0)
  const listRequest = useRef(0)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
      const previous = sessionRef.current
      if (previous?.guestToken && !keepGuest) void api.endGuest(previous).catch(() => {})
    }
  }, [keepGuest])

  const saveGuest = useCallback((patch: GuestCache) => {
    if (!keepGuest) return
    cache.current = { ...cache.current, ...patch }
    writeGuestCache(cache.current)
  }, [keepGuest])

  const startSession = useCallback(async () => {
    if (sessionRef.current) return sessionRef.current
    if (!guestStart.current) {
      guestStart.current = api.guest().then(({ token }) => {
        const next = { guestToken: token }
        if (!alive.current) {
          void api.endGuest(next).catch(() => {})
          throw new Error('Session closed')
        }
        sessionRef.current = next
        setSession(next)
        saveGuest({ token })
        return next
      }).finally(() => { guestStart.current = null })
    }
    return guestStart.current
  }, [saveGuest])

  const setActiveChat = useCallback((id: string | null) => {
    if (activeRef.current === id) return
    chatRequest.current++
    activeRef.current = id
    setChat(null)
    setActive(id)
    saveGuest({ active: id ?? undefined, chat: undefined })
  }, [saveGuest])

  const archiveGuest = useCallback(() => {
    const saved = joinGuestChats(cache.current.archive, cache.current.chat)
    if (!saved) return
    const archived = {
      ...saved,
      messages: saved.messages.map((message) => message.state === 'working'
        ? { ...message, state: 'failed' as const, error: 'Этот ответ не успел сохраниться.' }
        : message),
    }
    setArchive(archived)
    saveGuest({ archive: archived, chat: undefined })
  }, [saveGuest])

  const readError = useCallback((who: Session, error: unknown) => {
    if (sessionRef.current !== who) return
    if (error instanceof ApiError && error.status === 401) {
      if (!who.guestToken) {
        onProfile(null)
        return
      }
      sessionRef.current = null
      setSession(null)
      setChats([])
      archiveGuest()
      saveGuest({ token: undefined, chat: undefined, active: undefined })
      setActiveChat(null)
      if (keepGuest) return
    }
    setNotice(errorText(error))
  }, [archiveGuest, keepGuest, onProfile, saveGuest, setActiveChat])

  const loadList = useCallback(async (who: Session) => {
    const sequence = ++listRequest.current
    try {
      const next = await api.chats(who)
      if (sequence !== listRequest.current || !alive.current || sessionRef.current !== who) return
      setChats(next)
    } catch (error) {
      if (sequence === listRequest.current && alive.current) readError(who, error)
    }
  }, [readError])

  const loadChat = useCallback(async (who: Session, id: string) => {
    if (activeRef.current !== id) return
    const sequence = ++chatRequest.current
    try {
      const next = await api.chat(who, id)
      if (sequence === chatRequest.current && alive.current && sessionRef.current === who) {
        saveGuest({ chat: next })
        setChat(next)
      }
    } catch (error) {
      if (sequence !== chatRequest.current || !alive.current) return
      if (error instanceof ApiError && error.status === 404) {
        archiveGuest()
        setActiveChat(null)
      } else readError(who, error)
    }
  }, [archiveGuest, readError, saveGuest, setActiveChat])

  useEffect(() => {
    if (active && session) queueMicrotask(() => { void loadChat(session, active) })
  }, [active, session, loadChat])

  useEffect(() => {
    if (!session) return
    let cancelled = false
    let socket: WebSocket | undefined
    let retryTimer = 0
    let delay = 700
    queueMicrotask(() => { if (!cancelled) void loadList(session) })
    function refresh(change?: ChatEvent) {
      if (!session || cancelled) return
      if (!change || change.type === 'chats.updated') void loadList(session)
      const id = activeRef.current
      if (id && (!change?.chatId || change.chatId === id)) void loadChat(session, id)
    }
    function connect() {
      if (!session || cancelled) return
      socket = api.eventSocket(session)
      socket.onopen = () => {
        if (cancelled) return
        delay = 700
        setConnected(true)
        refresh()
      }
      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          const change = JSON.parse(event.data) as ChatEvent
          if (change.type === 'chat.updated' || change.type === 'chats.updated') refresh(change)
        } catch {
          // Незнакомые события не меняют историю.
        }
      }
      socket.onclose = () => {
        if (cancelled) return
        setConnected(false)
        refresh()
        retryTimer = window.setTimeout(connect, delay)
        delay = Math.min(delay * 2, 10000)
      }
    }
    connect()
    const recover = () => refresh()
    window.addEventListener('online', recover)
    return () => {
      cancelled = true
      clearTimeout(retryTimer)
      socket?.close()
      window.removeEventListener('online', recover)
    }
  }, [session, loadChat, loadList])

  const waiting = chat?.messages.at(-1)?.state === 'working'
  useEffect(() => {
    if (!session || !active || !waiting) return
    const timer = window.setInterval(() => { void loadChat(session, active) }, connected ? 6000 : 1400)
    return () => window.clearInterval(timer)
  }, [active, session, connected, waiting, loadChat])

  async function createChat(who: Session) {
    const item = await api.createChat(who)
    setActiveChat(item.id)
    setChats((previous) => [item, ...previous.filter(({ id }) => id !== item.id)])
    return item.id
  }

  async function removeChat(id: string) {
    if (!session) return
    await api.deleteChat(session, id)
    if (activeRef.current === id) setActiveChat(null)
    await loadList(session)
  }

  async function clearChats() {
    if (!session) return
    await api.clearChats(session)
    setActiveChat(null)
    setChats([])
    await loadList(session)
  }

  return {
    active, chat: joinGuestChats(archive, chat), archivedMessages: archive?.messages, chats, session, notice, setNotice, waiting, startSession,
    setActiveChat, createChat, removeChat, clearChats, loadChat, loadList,
  }
}
