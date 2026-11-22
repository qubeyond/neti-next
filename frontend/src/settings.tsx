import { UserIcon } from '@phosphor-icons/react/dist/icons/User'
import { SparkleIcon } from '@phosphor-icons/react/dist/icons/Sparkle'
import { ShieldCheckIcon } from '@phosphor-icons/react/dist/icons/ShieldCheck'
import { CheckIcon } from '@phosphor-icons/react/dist/icons/Check'
import { SignOutIcon } from '@phosphor-icons/react/dist/icons/SignOut'
import { TrashIcon } from '@phosphor-icons/react/dist/icons/Trash'
import { useState, type FormEvent } from 'react'
import { api, errorText, type Profile } from './api'
import { Avatar, Modal } from './ui'

type SettingsProps = {
  profile: Profile | null
  onClose: () => void
  onProfile: (profile: Profile) => void
  onClear: () => Promise<void>
  onLogout: () => Promise<void>
  onDelete: () => void
}

const settingsSections = [
  { id: 'profile', label: 'Профиль', Icon: UserIcon, requiresProfile: true },
  { id: 'agent', label: 'Агент', Icon: SparkleIcon, requiresProfile: true },
  { id: 'data', label: 'Данные', Icon: ShieldCheckIcon, requiresProfile: false },
] as const

export function SettingsDialog({ profile, onClose, onProfile, onClear, onLogout, onDelete }: SettingsProps) {
  const [section, setSection] = useState<(typeof settingsSections)[number]['id']>(profile ? 'profile' : 'data')
  const [name, setName] = useState(profile?.name ?? '')
  const [instructions, setInstructions] = useState(profile?.instructions ?? '')
  const [currentPassword, setCurrentPassword] = useState('')
  const [password, setPassword] = useState('')
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState<'history' | 'account' | null>(null)

  async function perform(action: () => Promise<void>) {
    if (saving) return
    setSaving(true)
    setFeedback('')
    setError('')
    try {
      await action()
    } catch (error) {
      setError(errorText(error))
    } finally {
      setSaving(false)
    }
  }

  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void perform(async () => {
      if (section === 'agent') {
        onProfile(await api.settings({ instructions }))
      } else {
        const next = await api.settings({ name, password, currentPassword })
        onProfile(next)
        setName(next.name)
        setCurrentPassword('')
        setPassword('')
      }
      setFeedback('Настройки сохранены.')
    })
  }

  function destroy() {
    void perform(async () => {
      if (confirm === 'account') {
        await api.deleteProfile()
        onDelete()
      } else if (confirm === 'history') {
        await onClear()
        setConfirm(null)
        setFeedback('История удалена.')
      }
    })
  }

  return <Modal title="Настройки" onClose={onClose} className="settings-modal">
    <div className="settings-layout">
      <nav className="settings-nav" aria-label="Разделы настроек">
        {settingsSections.filter(({ requiresProfile }) => profile || !requiresProfile).map(({ id, label, Icon }) =>
          <button className="nav-button" type="button" key={id} aria-pressed={section === id} disabled={saving} onClick={() => {
            setSection(id)
            setConfirm(null)
            setFeedback('')
            setError('')
          }}><Icon size={18} weight="duotone"/> {label}</button>)}
      </nav>
      <div className="settings-form settings-pane">
        {section === 'profile' && profile && <form className="setting-section" onSubmit={save}>
          <h3>Профиль</h3>
          <div className="setting-profile"><Avatar/><div><strong>{profile.name}</strong><p className="muted">{profile.email}</p></div></div>
          <label>Как к вам обращаться<input value={name} onChange={(event) => setName(event.target.value)} required maxLength={60} autoComplete="nickname"/></label>
          <details className="password-section" open={Boolean(password || currentPassword) || undefined}><summary>Изменить пароль</summary>
            <label>Текущий пароль<input type="password" autoComplete="current-password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required={!!password} maxLength={72}/></label>
            <label>Новый пароль<input type="password" autoComplete="new-password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={8} maxLength={72}/></label>
          </details>
          <button type="submit" className="primary-button" disabled={saving}>Сохранить профиль</button>
        </form>}
        {section === 'agent' && profile && <form className="setting-section" onSubmit={save}>
          <h3>Агент</h3>
          <label>Инструкция для агента<textarea value={instructions} onChange={(event) => setInstructions(event.target.value)} rows={4} maxLength={2000} placeholder="Например, отвечай кратко и приводи источники."/></label>
          <p className="muted">Инструкция сохраняется в профиле. Агент начнёт учитывать её после обновления API.</p>
          <button type="submit" className="primary-button" disabled={saving}>Сохранить инструкцию</button>
        </form>}
        {section === 'data' && <section className="setting-section danger-zone">
          <h3>Данные</h3>
          {confirm ? <div className="confirm-action" role="group" aria-label="Подтверждение удаления">
            <p>{confirm === 'account' ? 'Удалить профиль, все его диалоги и вложения?' : 'Удалить все диалоги и вложения в этой сессии?'}</p>
            <div><button type="button" className="soft-button" disabled={saving} onClick={() => setConfirm(null)}>Отмена</button>
              <button type="button" className="danger-button" disabled={saving} onClick={destroy}>{confirm === 'account' ? 'Удалить аккаунт' : 'Удалить историю'}</button></div>
          </div> : <div className="danger-actions">
            <button type="button" className="soft-button" disabled={saving} onClick={() => setConfirm('history')}><TrashIcon/> Удалить историю</button>
            {profile && <button type="button" className="soft-button" disabled={saving} onClick={() => setConfirm('account')}><TrashIcon/> Удалить аккаунт</button>}
            {profile && <button type="button" className="soft-button" disabled={saving} onClick={() => { void perform(onLogout) }}><SignOutIcon/> Выйти</button>}
          </div>}
        </section>}
        {feedback && <p role="status" className="form-success"><CheckIcon/> {feedback}</p>}
        {error && <p role="alert" className="form-error">{error}</p>}
      </div>
    </div>
  </Modal>
}
