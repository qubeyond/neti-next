import { XIcon } from '@phosphor-icons/react/dist/icons/X'
import { useEffect, useRef, type ReactNode } from 'react'

export function BrandMark({ className = '' }: { className?: string }) {
  return (
    <span className={'brand-mark ' + className} aria-hidden="true">
      <svg viewBox="0 0 32 32" fill="none">
        <path d="M5 24.5 15.8 6h5.9L11 24.5H5Z" fill="currentColor"/>
        <path d="m16 24.5 5.7-10.2 5.6 10.2H16Z" fill="currentColor" opacity=".62"/>
        <path d="M4 27h24" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity=".7"/>
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
