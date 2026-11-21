export type Theme = 'light' | 'dark'
export type Profile = { id: string; name: string; email: string; theme: Theme; instructions: string }
export type FileItem = { id: string; name: string; mime: string; size: number }
export type Citation = { url: string; quote: string }
export type Artifact = { kind: 'image' | 'link' | 'quote' | 'audio' | 'file'; title: string; url?: string; text?: string }
export type Activity = { kind: string; label: string; elapsedMs: number }
export type Message = {
  id: string
  role: 'user' | 'assistant'
  text: string
  quote?: string
  attachments?: FileItem[]
  state?: 'working' | 'complete' | 'failed'
  status?: 'answered' | 'partial' | 'unverified'
  error?: string
  citations?: Citation[]
  artifacts?: Artifact[]
  activity?: Activity[]
  createdAt: string
  durationMs?: number
}
export type ChatSummary = { id: string; title: string; updatedAt: string }
export type Chat = ChatSummary & { messages: Message[] }
export type Session = { guestToken?: string }
export type ChatEvent = { type: 'chat.updated' | 'chats.updated'; chatId?: string }

const prefix = '/api/v1'

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

export function errorText(error: unknown) {
  return error instanceof ApiError ? error.message : 'Связь с сервером прервалась. Попробуйте ещё раз.'
}

async function request(path: string, session?: Session, options: RequestInit = {}) {
  const headers = new Headers(options.headers)
  if (session?.guestToken) headers.set('Authorization', 'Bearer ' + session.guestToken)
  if (typeof options.body === 'string') headers.set('Content-Type', 'application/json')
  const response = await fetch(prefix + path, { ...options, headers, cache: 'no-store' })
  if (!response.ok) {
    const fallback = response.status === 413 ? 'Размер файла превышает лимит.' : 'Сервер не смог выполнить запрос.'
    const body = await response.json().catch(() => null)
    throw new ApiError(response.status, typeof body?.detail === 'string' ? body.detail : fallback)
  }
  return response
}

async function json<T>(path: string, session?: Session, options?: RequestInit): Promise<T> {
  return (await request(path, session, options)).json()
}

const chatPath = (id: string) => '/chats/' + encodeURIComponent(id)
const filePath = (chat: string, file: string) => chatPath(chat) + '/files/' + encodeURIComponent(file)
const payload = (body: unknown, method = 'POST') => ({ method, body: JSON.stringify(body) })

export const api = {
  profile: () => json<Profile>('/profile'),
  auth: (mode: 'login' | 'register', values: { email: string; password: string; name?: string }) =>
    json<Profile>('/auth/' + mode, undefined, payload(values)),
  logout: () => request('/auth/logout', undefined, { method: 'POST' }),
  guest: () => json<{ token: string }>('/auth/guest', undefined, { method: 'POST' }),
  endGuest: (session: Session) => request('/auth/guest', session, { method: 'DELETE', keepalive: true }),
  settings: (values: Partial<Pick<Profile, 'name' | 'theme' | 'instructions'>> & { password?: string; currentPassword?: string }) =>
    json<Profile>('/profile', undefined, payload(values, 'PATCH')),
  deleteProfile: () => request('/profile', undefined, { method: 'DELETE' }),
  chats: (session: Session) => json<ChatSummary[]>('/chats', session),
  createChat: (session: Session) => json<ChatSummary>('/chats', session, { method: 'POST' }),
  chat: (session: Session, id: string) => json<Chat>(chatPath(id), session),
  deleteChat: (session: Session, id: string) => request(chatPath(id), session, { method: 'DELETE' }),
  clearChats: (session: Session) => request('/chats', session, { method: 'DELETE' }),
  send: (session: Session, id: string, input: { requestId: string; text: string; quote: string; fileIds: string[] }) =>
    json<{ messageId: string }>(chatPath(id) + '/messages', session, payload(input)),
  retry: (session: Session, id: string, message: string) =>
    json<{ messageId: string }>(chatPath(id) + '/messages/' + encodeURIComponent(message) + '/retry', session, { method: 'POST' }),
  upload: (session: Session, id: string, file: File) => {
    const body = new FormData()
    body.append('file', file)
    return json<FileItem>(chatPath(id) + '/files', session, { method: 'POST', body })
  },
  removeFile: (session: Session, chat: string, file: string) => request(filePath(chat, file), session, { method: 'DELETE' }),
  download: async (session: Session, chat: string, file: string) => (await request(filePath(chat, file), session)).blob(),
  eventSocket: (session: Session) => {
    const url = new URL(prefix + '/events', location.href)
    url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    return new WebSocket(url, session.guestToken ? ['neti.v1', 'guest.' + session.guestToken] : ['neti.v1'])
  },
}

export function safeURL(input?: string) {
  if (!input) return undefined
  try {
    const url = new URL(input)
    return (url.protocol === 'http:' || url.protocol === 'https:') && !url.username && !url.password ? url.href : undefined
  } catch {
    return undefined
  }
}
