import { useEffect, useState, type FormEvent } from 'react'
import { PigeonIcon } from './pigeon-icon'
import { api, errorText } from './api'
import { AsciiPigeon } from './pigeon-ascii'
import { BrandMark } from './ui'
import { universityPrompts } from './university-prompts'
import './public-pages.css'

function SiteBrand() {
  return <a href="/" className="site-brand"><BrandMark/><span>Пигеон</span></a>
}

export function LandingPage() {
  useEffect(() => {
    const id = location.hash.slice(1)
    if (id === 'examples' || id === 'offers') document.getElementById(id)?.scrollIntoView()
  }, [])

  return <div className="public-site">
    <header className="site-header">
      <SiteBrand/>
      <a className="site-button site-button-quiet" href="/login">Войти <PigeonIcon name="next"/></a>
    </header>
    <main>
      <section className="site-hero" aria-label="О Пигеоне">
        <div className="hero-copy">
          <h1>Разберёмся<br/>с вузами<span>.</span></h1>
          <p>Спроси про поступление, программы или общежитие. Пигеон поможет разобраться и покажет источники.</p>
          <a className="site-button site-button-primary" href="/chat">Открыть чат <PigeonIcon name="next"/></a>
          <span className="hero-note">Можно начать без аккаунта</span>
        </div>
        <div className="hero-art">
          <div className="hero-portrait" aria-hidden="true"><AsciiPigeon speaking={false}/></div>
          <div className="hero-example"><span>Пример вопроса</span><p>Как первокурснику заселиться в общежитие?</p></div>
        </div>
      </section>
      <section className="site-section" id="examples" aria-labelledby="examples-title">
        <h2 id="examples-title">С чего начать</h2>
        <div className="site-examples">
          {universityPrompts.map(({ id, label, prompt, icon }) => <a className="site-example" key={id} href={'/chat?example=' + id}>
            <span><PigeonIcon name={icon}/> {label}</span>
            <strong>{prompt}</strong>
            <PigeonIcon name="next" className="site-example-arrow"/>
          </a>)}
        </div>
      </section>
      <section className="site-section site-access" id="offers" aria-labelledby="access-title">
        <h2 id="access-title">Как пользоваться</h2>
        <div className="site-access-options">
          <div>
            <h3>Без аккаунта</h3>
            <p>Один диалог. История остаётся в этом браузере.</p>
            <a href="/chat" className="site-button site-button-quiet">Открыть чат <PigeonIcon name="next"/></a>
          </div>
          <div>
            <h3>С профилем</h3>
            <p>Несколько диалогов, история и настройки на разных устройствах.</p>
            <a href="/register" className="site-button site-button-primary">Создать профиль <PigeonIcon name="next"/></a>
          </div>
        </div>
        <p className="site-access-note">Чат и профиль доступны бесплатно. Платных тарифов и скидок пока нет.</p>
      </section>
    </main>
  </div>
}

export function AccountPage({ mode }: { mode: 'login' | 'register' }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const register = mode === 'register'
  const title = register ? 'Создать профиль' : 'Войти в Пигеон'

  useEffect(() => {
    document.title = (register ? 'Регистрация' : 'Вход') + ' · Пигеон'
  }, [register])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = new FormData(event.currentTarget)
    setBusy(true)
    setError('')
    try {
      await api.auth(mode, {
        email: String(form.get('email')), password: String(form.get('password')),
        ...(register ? { name: String(form.get('name')) } : {}),
      })
      window.location.replace('/chat')
    } catch (error) {
      setError(errorText(error))
      setBusy(false)
    }
  }

  return <div className="public-site account-site">
    <header className="site-header">
      <SiteBrand/>
    </header>
    <main className="account-layout">
      <aside className="account-art" aria-hidden="true"><AsciiPigeon speaking={false}/></aside>
      <section className="account-content" aria-labelledby="account-title">
        <h1 id="account-title">{title}</h1>
        <p className="account-intro">{register ? 'Создавай отдельные диалоги и возвращайся к истории.' : 'Открой свою историю и продолжи разговор.'}</p>
        <form className="account-form" aria-busy={busy} onSubmit={(event) => { void submit(event) }}>
          {register && <label>Имя<input name="name" maxLength={60} autoComplete="nickname" required/></label>}
          <label>Почта<input name="email" type="email" maxLength={254} autoComplete="email" required/></label>
          <label>Пароль<input name="password" type="password" minLength={register ? 8 : undefined} maxLength={72} autoComplete={register ? 'new-password' : 'current-password'} required/></label>
          {error && <p role="alert" className="account-error">{error}</p>}
          <button type="submit" className="site-button site-button-primary" disabled={busy}>
            {busy ? 'Подождите…' : register ? 'Создать профиль' : 'Войти'} <PigeonIcon name="next"/>
          </button>
        </form>
        <div className="account-links">
          {register ? <a href="/login">Уже есть профиль</a> : <a href="/register">Создать профиль</a>}
          <a href="/chat">Продолжить без входа</a>
        </div>
      </section>
    </main>
  </div>
}
