import { PigeonIcon } from './pigeon-icon'
import { useEffect, useRef, type ReactNode } from 'react'

export function BrandMark({ className = '' }: { className?: string }) {
  return (
    <span className={'brand-mark ' + className} aria-hidden="true">
      <span className="brand-feather">
        <svg viewBox="0 0 32 32" fill="none">
          <path d="M28.7 3.7c-7.6-.2-15.6 3.4-19 9.4-1.5 2.7-1.7 5.7-.5 8.2 3.6-5.2 8.3-9.4 13.3-12.3-4.4 3.9-8.6 8.6-11.3 13.3 2.9.9 5.6.1 8-1.6l-2.6-.6 4.6-1.2c1-1 1.8-2.2 2.5-3.4l-2.8.1 4.2-2.4c1.8-3.2 2.9-6.5 3.6-9.5Z" fill="currentColor"/>
          <path d="M5.8 26.7c3.5-7 8.7-13.1 14.9-17" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round"/>
        </svg>
      </span>
      <span className="brand-ink"/>
    </span>
  )
}

export function Avatar() {
  return <span className="avatar" aria-hidden="true"><span/><svg viewBox="0 0 32 32" fill="none"><path d="M11.5 15.5h.01m9 0h.01m-6 4c1 1 2 1 3 0" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg></span>
}

export function Modal({ title, onClose, children, className = '' }: { title: string; onClose: () => void; children: ReactNode; className?: string }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const node = dialog.current
    node?.showModal()
    return () => { node?.close() }
  }, [])
  return (
    <dialog ref={dialog} className={'modal ' + className} aria-label={title} onCancel={(event) => { event.preventDefault(); onClose() }}>
      <header className="modal-header">
        <h2>{title}</h2>
        <button className="icon-button" type="button" onClick={onClose} aria-label="Закрыть"><PigeonIcon name="close"/></button>
      </header>
      {children}
    </dialog>
  )
}

export function displayDuration(milliseconds: number) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000))
  return seconds < 60 ? `${seconds} с` : `${Math.floor(seconds / 60)} мин ${seconds % 60} с`
}
