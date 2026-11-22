import { createContext, memo, useContext, useLayoutEffect, useMemo, useRef } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { safeURL } from './api'
import { CitationLink, type CitationReference } from './citations'

const citationMarker = /〔([1-9][0-9]{0,2})〕|\[([1-9][0-9]{0,2})\](?![([])/g
const marker = citationMarker.source
const citationCluster = new RegExp(`(?:${marker})(?:[ \\t]*(?:[,;][ \\t]*)?(?:${marker}))*`, 'g')
const citationAnchors = createContext<ReadonlyMap<string, CitationReference>>(new Map())

const markdownComponents: Components = {
  a: function MarkdownLink({ href, children }) {
    const anchors = useContext(citationAnchors)
    const citation = href?.startsWith('#') ? anchors.get(href.slice(1)) : undefined
    if (citation) return <CitationLink reference={citation}/>
    const target = safeURL(href)
    return target ? <a href={target} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>
  },
  img: ({ src, alt }) => {
    const target = safeURL(src)
    return target ? <a href={target} target="_blank" rel="noreferrer">{alt || 'Открыть изображение'}</a> : <span>{alt}</span>
  },
  table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
}

function renderCitations(text: string, references?: ReadonlyMap<number, CitationReference>) {
  const anchors = new Map<string, CitationReference>()
  let markdown = references?.size ? text.replace(citationCluster, (cluster) => {
    const seen = new Set<number>()
    const links: string[] = []
    for (const [, numbered, legacy] of cluster.matchAll(citationMarker)) {
      const ref = Number(numbered ?? legacy)
      const citation = references.get(ref)
      if (citation && !seen.has(citation.number)) {
        seen.add(citation.number)
        const anchor = citation.id + '-ref-' + ref
        anchors.set(anchor, citation)
        links.push(`[〔${citation.number}〕](#${anchor})`)
      }
    }
    return links.join(' ')
  }) : text
  if (!anchors.size && references?.size) {
    const links = new Map<number, string>()
    for (const citation of references.values()) {
      if (links.has(citation.number)) continue
      const anchor = citation.id + '-source'
      anchors.set(anchor, citation)
      links.set(citation.number, `[〔${citation.number}〕](#${anchor})`)
    }
    markdown += '\n\n' + [...links.values()].join(' ')
  }
  return { markdown, anchors }
}

type TypingPart = { length: number; reveal: (count: number) => void }

function typingParts(root: HTMLElement) {
  const parts: TypingPart[] = []
  const segmenter = new Intl.Segmenter('ru', { granularity: 'grapheme' })
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (node instanceof HTMLAnchorElement) return NodeFilter.FILTER_ACCEPT
      if (node instanceof Text && node.data.trim() && !node.parentElement?.closest('a')) return NodeFilter.FILTER_ACCEPT
      return NodeFilter.FILTER_SKIP
    },
  })

  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node instanceof HTMLAnchorElement) {
      const link = node
      link.setAttribute('data-typing-pending', '')
      parts.push({ length: 1, reveal: () => link.removeAttribute('data-typing-pending') })
    } else if (node instanceof Text) {
      const text = node
      const full = text.data
      const ends = Array.from(segmenter.segment(full), ({ index, segment }) => index + segment.length)
      text.data = ''
      parts.push({ length: ends.length, reveal: (count) => {
        text.data = full.slice(0, ends[count - 1])
      } })
    }
  }
  return parts
}

export default memo(function MarkdownContent({ text, references, animate, onTyping }: {
  text: string
  references?: ReadonlyMap<number, CitationReference>
  animate?: boolean
  onTyping?: (active: boolean) => void
}) {
  const { markdown, anchors } = useMemo(() => renderCitations(text, references), [text, references])
  const content = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const root = content.current
    const preference = matchMedia('(prefers-reduced-motion: reduce)')
    if (!root || !animate || preference.matches) return

    const parts = typingParts(root)
    if (!parts.length) return
    const total = parts.reduce((length, part) => length + part.length, 0)
    const delay = Math.min(11, 2000 / total)
    let index = 0
    let printed = 0
    let request = 0
    let started = 0

    root.dataset.typing = 'true'
    root.setAttribute('aria-live', 'off')

    function finish() {
      cancelAnimationFrame(request)
      for (const part of parts) part.reveal(part.length)
      root?.removeAttribute('data-typing')
      root?.removeAttribute('aria-live')
      onTyping?.(false)
    }

    function frame(now: number) {
      if (!started) {
        started = now
        onTyping?.(true)
      }
      const count = Math.min(total, Math.floor((now - started) / delay) + 1)
      while (index < parts.length) {
        const part = parts[index]!
        const visible = Math.min(count - printed, part.length)
        if (visible < 1) break
        part.reveal(visible)
        if (visible < part.length) break
        printed += part.length
        index++
      }
      if (count === total) finish()
      else request = requestAnimationFrame(frame)
    }
    request = requestAnimationFrame(frame)
    preference.addEventListener('change', finish)
    return () => {
      preference.removeEventListener('change', finish)
      finish()
    }
  }, [markdown, animate, onTyping])

  return <div ref={content} className="markdown-rendered">
    <citationAnchors.Provider value={anchors}>
      <Markdown skipHtml remarkPlugins={[remarkGfm]} components={markdownComponents}>{markdown}</Markdown>
    </citationAnchors.Provider>
  </div>
})
