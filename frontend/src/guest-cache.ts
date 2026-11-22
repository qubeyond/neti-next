import type { Chat } from './api'

const guestCacheKey = 'neti.guest.workspace.v1'

export type GuestCache = {
  token?: string
  active?: string
  chat?: Chat
  archive?: Chat
}

export function readGuestCache(): GuestCache {
  try {
    return JSON.parse(localStorage.getItem(guestCacheKey) ?? '{}') ?? {}
  } catch {
    return {}
  }
}

export function writeGuestCache(cache: GuestCache) {
  try {
    localStorage.setItem(guestCacheKey, JSON.stringify(cache))
  } catch {
    // Сессия остаётся доступной из открытой вкладки.
  }
}

export function joinGuestChats(archive?: Chat, chat?: Chat | null) {
  if (!archive || !chat) return chat ?? archive ?? null
  const remoteIDs = new Set(chat.messages.map(({ id }) => id))
  return { ...chat, title: archive.title, messages: [...archive.messages.filter(({ id }) => !remoteIDs.has(id)), ...chat.messages] }
}
