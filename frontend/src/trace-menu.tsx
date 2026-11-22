import { useId, useRef, useState, type KeyboardEvent } from 'react'

type Choice<Key extends string> = { value: Key; label: string }

export function TraceMenu<Key extends string>({ label, value, options, onChange }: {
  label: string
  value: Key
  options: Choice<Key>[]
  onChange: (value: Key) => void
}) {
  const id = useId()
  const trigger = useRef<HTMLButtonElement>(null)
  const popover = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const selected = options.find((option) => option.value === value)

  function position(panel: HTMLDivElement) {
    const rect = trigger.current?.getBoundingClientRect()
    if (!rect) return
    const width = Math.min(238, window.innerWidth - 24)
    const below = window.innerHeight - rect.bottom - 12
    const above = rect.top - 12
    const height = options.length * 38 + 12
    const up = below < height && above > below
    panel.style.width = width + 'px'
    panel.style.left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)) + 'px'
    panel.style.maxHeight = Math.max(up ? above : below, 0) + 'px'
    panel.style.top = up ? 'auto' : rect.bottom + 5 + 'px'
    panel.style.bottom = up ? window.innerHeight - rect.top + 5 + 'px' : 'auto'
  }

  function close() {
    popover.current?.hidePopover()
    trigger.current?.focus()
  }

  function navigate(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Tab') {
      close()
      return
    }
    if (event.key === 'Escape') {
      event.preventDefault()
      close()
      return
    }
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const entries = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'))
    if (!entries.length) return
    const current = entries.indexOf(document.activeElement as HTMLButtonElement)
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? entries.length - 1 :
      (current + (event.key === 'ArrowDown' ? 1 : entries.length - 1)) % entries.length
    entries[next]?.focus()
  }

  return <div className="trace-select">
    <button ref={trigger} type="button" className="trace-select-trigger" popoverTarget={id}
      aria-label={label + ': ' + (selected?.label ?? value)} aria-haspopup="menu" aria-controls={id} aria-expanded={open}
      onKeyDown={(event) => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return
        event.preventDefault()
        popover.current?.showPopover()
      }}>
      <span>{selected?.label ?? value}</span><span className="trace-chevron" aria-hidden="true"/>
    </button>
    <div ref={popover} id={id} className="trace-select-menu" role="menu" aria-label={label} popover="auto"
      onBeforeToggle={(event) => { if (event.newState === 'open') position(event.currentTarget) }}
      onToggle={(event) => {
        const next = event.newState === 'open'
        setOpen(next)
        if (next) event.currentTarget.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus({ preventScroll: true })
      }}
      onKeyDown={navigate}>
      {options.map((option) => <button type="button" key={option.value} role="menuitemradio" tabIndex={-1}
        aria-checked={option.value === value} onClick={() => { close(); onChange(option.value) }}>
        <span>{option.label}</span><span className="trace-check" aria-hidden="true">✓</span>
      </button>)}
    </div>
  </div>
}
