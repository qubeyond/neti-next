import { PigeonIcon } from './pigeon-icon'
import { useRef, useState } from 'react'

const cards = [
  { label: 'Аккаунт', title: 'Вся история под рукой', text: 'Веди несколько диалогов и возвращайся к ним с другого устройства.', href: '/register' },
  { label: 'Промо Пигеона', title: 'От поступления до сессии', text: 'Посмотри, с какими вопросами поможет Пигеон.', href: '/#examples' },
  { label: 'Подписка и скидки', title: 'Начни бесплатно', text: 'Чат уже доступен. Платных тарифов и скидок пока нет.', href: '/#offers' },
]

const dismissKey = 'pigeon.guest.promotions.dismissed'

function alreadyDismissed() {
  try {
    return sessionStorage.getItem(dismissKey) === 'yes'
  } catch {
    return false
  }
}

export function GuestPromotions({ started }: { started: boolean }) {
  const [dismissed, setDismissed] = useState(alreadyDismissed)
  const login = useRef<HTMLAnchorElement>(null)

  function dismiss() {
    setDismissed(true)
    login.current?.focus()
    try {
      sessionStorage.setItem(dismissKey, 'yes')
    } catch {
      // Закрытие сохраняется до ухода со страницы.
    }
  }

  return <div className="guest-promotions">
    {!dismissed && <section className={'guest-offers' + (started ? ' guest-offers-compact' : '')} aria-label="Возможности Пигеона">
      {started ? <div className="guest-account-note">
        <span>Все диалоги в одном профиле.</span> <a href="/register">Создать профиль</a>
      </div> : <div className="guest-offer-grid">
        {cards.map(({ label, title, text, href }) => <a className="guest-offer" href={href} key={label}>
          <span className="guest-offer-label">{label} <PigeonIcon name="external"/></span>
          <strong>{title}</strong><span>{text}</span>
        </a>)}
      </div>}
      <button type="button" className="mini-button guest-offers-dismiss" aria-label="Скрыть предложения" onClick={dismiss}><PigeonIcon name="close"/></button>
    </section>}
    <nav className="guest-chat-links" aria-label="Ссылки гостевого чата">
      <a ref={login} href="/login">Войти</a>
      <a href="/#offers">Варианты доступа</a>
      <a href="/">О Пигеоне</a>
    </nav>
  </div>
}
