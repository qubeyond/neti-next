import { useId, useState } from 'react'
import { createPortal } from 'react-dom'
import { safeURL, type Citation } from './api'

export type CitationReference = {
  id: string
  number: number
  citation: Citation
  open?: () => void
}

export function sourceLocation(citation: Citation) {
  const address = safeURL(citation.url)
  if (!address) return undefined
  const url = new URL(address)
  if (url.hash.includes(':~:') || url.pathname.toLowerCase().endsWith('.pdf')) return url.href
  const parts = citation.quote.split(/\.{3}|…|\||\n|(?:^|\s)#{1,6}\s+/u)
    .map((part) => part.replace(/\*\*|__/gu, '').replace(/\s+/gu, ' ').trim())
  const phrase = parts.find((part) => part.split(' ').length >= 4) ?? parts[0] ?? ''
  const words = phrase.split(' ')
  const quote = (phrase.length <= 120 ? phrase : words.slice(0, 7).join(' '))
    .replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, '')
  if (quote.length < 12) return url.href
  url.hash += ':~:text=' + encodeURIComponent(quote).replaceAll('-', '%2D')
  return url.href
}

export function groupCitations(messageID: string, citations: Citation[], openPhoto?: (citation: Citation) => void) {
  const references = new Map<number, CitationReference>()
  const byEvidence = new Map<string, CitationReference>()

  const numbered = citations.some((citation) => citation.ref != null)

  for (const [index, citation] of citations.entries()) {
    const address = safeURL(citation.url)
    const url = address ? new URL(address) : null
    if (url) {
      if (!url.pathname.toLowerCase().endsWith('.pdf')) url.hash = ''
      for (const key of [...url.searchParams.keys()]) {
        if (key.startsWith('utm_')) url.searchParams.delete(key)
      }
      url.searchParams.sort()
    }
    const key = JSON.stringify([citation.attachment_id ?? url?.href ?? index, citation.quote.trim().replace(/\s+/g, ' '), citation.region])
    let reference = byEvidence.get(key)
    if (!reference) {
      const number = byEvidence.size + 1
      reference = { id: `evidence-${messageID}-${number}`, number, citation,
        open: citation.attachment_id && openPhoto ? () => openPhoto(citation) : undefined }
      byEvidence.set(key, reference)
    }
    const ref = citation.ref ?? (numbered ? undefined : index + 1)
    if (ref != null && !references.has(ref)) references.set(ref, reference)
  }
  return { references, sources: Array.from(byEvidence.values()) }
}

export function CitationLink({ reference }: { reference: CitationReference }) {
  const { citation, number } = reference
  const tooltipID = useId()
  const [position, setPosition] = useState<{ left: number; top: number; above: boolean }>()
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const target = sourceLocation(citation)
  const visible = (hovered || focused) && position

  function place(element: HTMLElement) {
    const rect = element.getBoundingClientRect()
    const width = Math.min(336, window.innerWidth - 24)
    const above = rect.bottom > window.innerHeight / 2
    setPosition({
      left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
      top: above ? rect.top - 8 : rect.bottom + 8,
      above,
    })
  }

  const props = {
    className: 'inline-citation',
    'aria-label': `Открыть цитату ${number}`,
    'aria-describedby': visible ? tooltipID : undefined,
    onMouseEnter: (event: React.MouseEvent<HTMLElement>) => { place(event.currentTarget); setHovered(true) },
    onMouseLeave: () => setHovered(false),
    onFocus: (event: React.FocusEvent<HTMLElement>) => { place(event.currentTarget); setFocused(true) },
    onBlur: () => setFocused(false),
    onKeyDown: (event: React.KeyboardEvent<HTMLElement>) => {
      if (event.key === 'Escape') { setHovered(false); setFocused(false) }
    },
    children: `〔${number}〕`,
  }

  return <>
    {target ? <a {...props} href={target} target="_blank" rel="noopener noreferrer"/> :
      <button {...props} type="button" onClick={(event) => {
        if (reference.open) { setHovered(false); setFocused(false); reference.open() }
        else { place(event.currentTarget); setFocused(true) }
      }}/>}
    {visible && createPortal(<div className="citation-preview" id={tooltipID} role="tooltip"
      style={{ left: position.left, top: position.top, transform: position.above ? 'translateY(-100%)' : undefined }}>
      {citation.quote}
    </div>, document.body)}
  </>
}
