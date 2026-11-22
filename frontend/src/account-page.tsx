import { useEffect, useState, type FormEvent } from 'react'
import { api, errorText } from './api'
import { PigeonAvatar } from './pigeon-avatar'
import { BrandMark } from './ui'
import './account-page.css'

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
      window.location.replace('/')
    } catch (error) {
      setError(errorText(error))
      setBusy(false)
    }
  }

  return <div className="account-site">
    <header className="site-header">
      <a href="/" className="site-brand"><BrandMark/><span>Пигеон</span></a>
    </header>
    <main className="account-layout">
      <aside className="account-art" aria-hidden="true"><PigeonAvatar speaking={false}/></aside>
      <section className="account-content" aria-labelledby="account-title">
        <h1 id="account-title">{title}</h1>
        <p className="account-intro">{register ? 'Создавай отдельные диалоги и возвращайся к истории.' : 'Открой свою историю и продолжи разговор.'}</p>
        <form className="account-form" aria-busy={busy} onSubmit={(event) => { void submit(event) }}>
          {register && <label>Имя<input name="name" maxLength={60} autoComplete="nickname" required/></label>}
          <label>Почта<input name="email" type="email" maxLength={254} autoComplete="email" required/></label>
          <label>Пароль<input name="password" type="password" minLength={register ? 8 : undefined} maxLength={72} autoComplete={register ? 'new-password' : 'current-password'} required/></label>
          {error && <p role="alert" className="account-error">{error}</p>}
          <button type="submit" className="primary-button" disabled={busy}>
            {busy ? 'Подождите…' : register ? 'Создать профиль' : 'Войти'}
          </button>
        </form>
        <div className="account-links">
          {register ? <a href="/login">Уже есть профиль</a> : <a href="/register">Создать профиль</a>}
          <a href="/">Продолжить без входа</a>
        </div>
      </section>
    </main>
  </div>
}
