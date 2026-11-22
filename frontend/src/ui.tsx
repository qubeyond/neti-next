import { PigeonIcon } from './pigeon-icon'
import { useEffect, useRef, type ReactNode } from 'react'

export function BrandMark({ className = '' }: { className?: string }) {
  return (
    <span className={'brand-mark ' + className} aria-hidden="true">
      <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round">
        <path d="M16 10a10 10 0 0 0-9.5-2v14.5a10 10 0 0 1 9.5 2 10 10 0 0 1 9.5-2V8a10 10 0 0 0-9.5 2v14.5"/>
        <path d="M10 12.5c1.3 0 2.2.24 3 .7m-3 3.2c1.3 0 2.2.24 3 .7m6-3.9c.8-.46 1.7-.7 3-.7" opacity=".7"/>
      </svg>
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
