import { GlobeIcon } from '@phosphor-icons/react/dist/icons/Globe'
import { InfoIcon } from '@phosphor-icons/react/dist/icons/Info'
import { GearIcon } from '@phosphor-icons/react/dist/icons/Gear'
import { SignOutIcon } from '@phosphor-icons/react/dist/icons/SignOut'
import { useEffect, useRef, useState } from 'react'
import type { Profile } from './api'
import { Avatar, Modal } from './ui'

type Props = {
  profile: Profile | null
  disabled: boolean
  onSettings: () => void
  onLogout: () => void
}

export function ProfileMenu({ profile, disabled, onSettings, onLogout }: Props) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<'language' | 'about' | null>(null)
  const anchor = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const firstAction = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    firstAction.current?.focus()
    function outside(event: PointerEvent) {
      if (!anchor.current?.contains(event.target as Node)) setOpen(false)
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      setOpen(false)
      trigger.current?.focus()
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  function choose(action: () => void) {
    setOpen(false)
    trigger.current?.focus()
    action()
  }

  return <div className="profile-menu-anchor" ref={anchor} onBlur={(event) => {
    if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) setOpen(false)
  }}>
    <div className="profile-summary">
      <Avatar/>
      <span><strong>{profile?.name ?? 'Гостевой режим'}</strong></span>
    </div>
    <button ref={trigger} className="icon-button profile-gear" type="button" disabled={disabled}
      aria-label="Меню настроек" aria-expanded={open} aria-controls="profile-actions"
      onClick={() => setOpen(!open)}><GearIcon/></button>
    <div className="profile-actions" id="profile-actions" hidden={!open} role="group" aria-label="Действия профиля">
      <button ref={firstAction} className="nav-button" type="button" onClick={() => choose(onSettings)}><GearIcon/> Настройки</button>
      <button className="nav-button" type="button" onClick={() => choose(() => setDetail('language'))}><GlobeIcon/> Язык</button>
      <button className="nav-button" type="button" onClick={() => choose(() => setDetail('about'))}><InfoIcon/> Подробнее</button>
      {profile && <button className="nav-button profile-logout" type="button" onClick={() => choose(onLogout)}><SignOutIcon/> Выйти</button>}
    </div>
    {detail && <Modal title={detail === 'language' ? 'Язык' : 'О Пигеоне'} onClose={() => setDetail(null)}>
      <div className="settings-form">
        {detail === 'language' ? <p>Язык интерфейса — русский.</p> :
          <p>Пигеон помогает ответить на вопросы о поступлении, учёбе и общежитиях вузов Новосибирска.</p>}
      </div>
    </Modal>}
  </div>
}
