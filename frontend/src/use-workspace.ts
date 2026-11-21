import { useCallback, useEffect, useRef, useState } from 'react'
import { api, ApiError, errorText, type Chat, type ChatEvent, type ChatSummary, type Profile, type Session } from './api'

const accountSession: Session = {}

export function useWorkspace(guest: boolean, onProfile: (profile: Profile | null) => void) {
  const [session, setSession] = useState<Session | null>(guest ? null : accountSession)
  const [chats, setChats] = useState<ChatSummary[]>([])
  const [chat, setChat] = useState<Chat | null>(null)
  const [active, setActive] = useState<string | null>(null)
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
      if (previous?.guestToken) void api.endGuest(previous).catch(() => {})
    }
  }, [])

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
        return next
      }).finally(() => { guestStart.current = null })
    }
    return guestStart.current
  }, [])

  const setActiveChat = useCallback((id: string | null) => {
    if (activeRef.current === id) return
    chatRequest.current++
    activeRef.current = id
    setChat(null)
    setActive(id)
  }, [])

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
      setActiveChat(null)
    }
    setNotice(errorText(error))
  }, [onProfile, setActiveChat])

  const loadList = useCallback(async (who: Session) => {
    const sequence = ++listRequest.current
    try {
      const next = await api.chats(who)
      if (sequence !== listRequest.current || !alive.current) return
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
      if (sequence === chatRequest.current && alive.current) setChat(next)
    } catch (error) {
      if (sequence !== chatRequest.current || !alive.current) return
      if (error instanceof ApiError && error.status === 404) setActiveChat(null)
      else readError(who, error)
    }
  }, [readError, setActiveChat])

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
    active, chat, chats, session, notice, setNotice, waiting, startSession,
    setActiveChat, createChat, removeChat, clearChats, loadChat, loadList,
  }
}
