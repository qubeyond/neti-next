import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { safeURL } from './api'

type Props = { text: string; sourceIDs?: ReadonlyMap<number, string> }

export default function MarkdownContent({ text, sourceIDs }: Props) {
  const withCitations = sourceIDs?.size
    ? text.replace(/〔([1-9][0-9]{0,2})〕/g, (marker: string, ref: string) => {
      const id = sourceIDs.get(Number(ref))
      return id ? `[〔${ref}〕](#${id})` : marker
    })
    : text
  const sourceAnchors = new Set(sourceIDs?.values())
  return <Markdown skipHtml remarkPlugins={[remarkGfm]}
    components={{
      a: ({ href, children }) => {
        const id = href?.startsWith('#') ? href.slice(1) : ''
        if (sourceAnchors.has(id)) return <a href={href} className="inline-citation" onClick={(event) => {
          const evidence = document.getElementById(id)
          if (!(evidence instanceof HTMLDetailsElement)) return
          event.preventDefault()
          evidence.open = true
          evidence.querySelector('summary')?.focus()
          evidence.scrollIntoView({ block: 'nearest' })
        }}>{children}</a>
        const target = safeURL(href)
        return target ? <a href={target} target="_blank" rel="noreferrer">{children}</a> : <span>{children}</span>
      },
      img: ({ src, alt }) => {
        const target = safeURL(src)
        return target ? <a href={target} target="_blank" rel="noreferrer">{alt || 'Открыть изображение'}</a> : <span>{alt}</span>
      },
      table: ({ children }) => <div className="table-scroll"><table>{children}</table></div>,
    }}>{withCitations}</Markdown>
}
