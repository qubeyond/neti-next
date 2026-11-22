import { PigeonIcon } from './pigeon-icon'
import { useEffect, useRef, type ReactNode } from 'react'

const pigeonWing = (
  <path fill="currentColor" fillRule="evenodd" d="M33.6 25.3C29.8 17.6 25.8 14 17.9 11.6 11.8 9.8 6.8 6.6 2.9 3.5c-.6 6.4 2.1 11.5 7.4 15.4l-5.8-2c2 5 6.2 8.4 12.3 10.2l-5.7-.4c3.5 4.4 8.4 6.2 14 5.5l-3.2 1.3c5.4 1.5 9.2-.8 11.9-5.2l-.2-3ZM14.7 15.6c5.7 1.4 10.1 4.1 14.2 8.9-4.2-3.2-8.2-5.2-13.1-6.3l-1.1-2.6Zm5.6 7.2c3.8.9 6.6 2.6 9.2 5.3-3-1.5-5.8-2.4-9.7-2.9l.5-2.4Z"/>
)

export function BrandMark({ className = '' }: { className?: string }) {
  return (
    <span className={'brand-mark ' + className} aria-hidden="true">
      <span className="brand-flight">
        <svg viewBox="0 0 72 37" fill="none">
          <g className="brand-wing brand-wing-left">{pigeonWing}</g>
          <g className="brand-wing brand-wing-right">
            <g transform="translate(72 0) scale(-1 1)">{pigeonWing}</g>
          </g>
        </svg>
      </span>
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
