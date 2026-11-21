import { XIcon } from '@phosphor-icons/react/dist/icons/X'
import { useEffect, useRef, type ReactNode } from 'react'

export function BrandMark({ className = '' }: { className?: string }) {
  return (
    <span className={'brand-mark ' + className} aria-hidden="true">
      <svg viewBox="0 0 32 32" fill="none">
        <path d="M16 3.8c1.8 6.5 4 8.8 10.5 10.7-6.5 1.9-8.7 4.1-10.5 10.7-1.9-6.6-4.2-8.8-10.7-10.7C11.8 12.6 14.1 10.3 16 3.8Z" fill="currentColor" opacity=".26"/>
        <path d="M16 3.8c1.8 6.5 4 8.8 10.5 10.7-6.5 1.9-8.7 4.1-10.5 10.7-1.9-6.6-4.2-8.8-10.7-10.7C11.8 12.6 14.1 10.3 16 3.8Z" stroke="currentColor" strokeWidth="1.55" strokeLinejoin="round"/>
        <path d="M25.5 21.8v6.5m-3.3-3.3h6.6M5.8 5.7v4m-2-2h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
      </svg>
    </span>
  )
}

export function Avatar() {
  return <span className="avatar" aria-hidden="true"><span/><svg viewBox="0 0 32 32" fill="none"><path d="M11.5 15.5h.01m9 0h.01m-6 4c1 1 2 1 3 0" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg></span>
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const node = dialog.current
    node?.showModal()
    return () => { node?.close() }
  }, [])
  return (
    <dialog ref={dialog} className="modal" aria-label={title} onCancel={(event) => { event.preventDefault(); onClose() }}>
      <header className="modal-header">
        <h2>{title}</h2>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть"><XIcon/></button>
      </header>
      {children}
    </dialog>
  )
}

export function displayDuration(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  return seconds < 60 ? `${seconds} с` : `${Math.floor(seconds / 60)} мин ${seconds % 60} с`
}
