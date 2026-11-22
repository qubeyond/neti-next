import { InfoIcon } from '@phosphor-icons/react/dist/icons/Info'
import { SignOutIcon } from '@phosphor-icons/react/dist/icons/SignOut'
import { useEffect, useRef, useState } from 'react'
import { PigeonIcon } from './pigeon-icon'
import type { Profile } from './api'
import { Avatar, Modal } from './ui'

type Props = {
  profile: Profile
  disabled: boolean
  onSettings: () => void
  onLogout: () => void
}

export function ProfileMenu({ profile, disabled, onSettings, onLogout }: Props) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState(false)
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
      <span><strong>{profile.name}</strong></span>
    </div>
    <button ref={trigger} className="icon-button profile-gear" type="button" disabled={disabled}
      aria-label="Меню настроек" aria-expanded={open}
      aria-controls="profile-actions" onClick={() => setOpen(!open)}><PigeonIcon name="settings"/></button>
    <div className="profile-actions" id="profile-actions" hidden={!open} role="group" aria-label="Действия профиля">
      <button ref={firstAction} className="nav-button" type="button" onClick={() => choose(onSettings)}><PigeonIcon name="settings"/> Настройки</button>
      <button className="nav-button" type="button" onClick={() => choose(() => setDetail(true))}><InfoIcon/> Подробнее</button>
      <button className="nav-button profile-logout" type="button" onClick={() => choose(onLogout)}><SignOutIcon/> Выйти</button>
    </div>
    {detail && <Modal title="О Пигеоне" onClose={() => setDetail(false)} className="about-modal">
      <div className="settings-form about-content">
        <p className="about-intro">Пигеон — агент по вузам России.</p>
        <p>Помогает разобраться с поступлением, программами, общежитиями и студенческой жизнью. Задавайте вопросы, уточняйте условия и прикладывайте документы.</p>
        <section><h3>Ответы с источниками</h3><p>Наведите на ссылку с названием сайта, чтобы увидеть цитату, или нажмите, чтобы открыть оригинал. Сроки и требования сверяйте с документами нужного года.</p></section>
        <section><h3>Документы и голос</h3><p>Прикрепите изображение или PDF для разбора. Голосовая запись превращается в черновик, который можно поправить перед отправкой.</p></section>
        <section><h3>Ваше рабочее пространство</h3><p>Чаты хранят отдельный контекст. В настройках профиля можно изменить имя и задать предпочтения для ответов агента.</p></section>
        <section><h3>Ход работы</h3><p>Этапы под ответом показывают поиск и чтение материалов. Когда сведений недостаточно, уточните вопрос или приложите официальный источник.</p></section>
      </div>
    </Modal>}
  </div>
}
