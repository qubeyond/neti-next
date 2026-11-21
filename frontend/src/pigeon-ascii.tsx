import { useEffect, useRef, useState } from 'react'
import colors from './assets/pigeon-colors.json'
import portrait from './assets/pigeon-ascii.txt?raw'

const rows = portrait.trimEnd().split('\n')
const lipCenter = 28.5
const lipRadius = 7.5

function mouthFrame(opening: number) {
  return rows.map((line, y) => {
    if (y < 14 || y > 18) return line
    return Array.from(line, (glyph, x) => {
      const offset = (x - lipCenter) / lipRadius
      if (Math.abs(offset) > 1) return glyph
      const curvedEdge = Math.sqrt(1 - offset * offset)
      const distance = y - 16
      if (Math.abs(distance) < opening * curvedEdge) return '@'
      if (Math.abs(distance) < (opening + .95) * curvedEdge) return distance < 0 ? '=' : '+'
      return glyph
    }).join('')
  }).join('\n')
}

const speechFrames = [.35, 1.25, 2.4, 1.55, .65, 1.85, 1.1].map(mouthFrame)

const palette: Record<string, string> = colors.palette
const paintedFrames = speechFrames.map((frame) => frame.split('\n').slice(0, 35).map((row, y) => {
  const runs: { color?: string; text: string }[] = []
  Array.from(row).forEach((glyph, x) => {
    const color = glyph === ' ' ? undefined : palette[colors.rows[y]?.[x] ?? '']
    const previous = runs.at(-1)
    if (previous && previous.color === color) previous.text += glyph
    else runs.push({ color, text: glyph })
  })
  return runs
}))

export function AsciiPigeon({ speaking }: { speaking: boolean }) {
  const [frame, setFrame] = useState(0)
  const cursor = useRef(0)
  useEffect(() => {
    if (!speaking) return
    const preference = matchMedia('(prefers-reduced-motion: reduce)')
    let timer = 0
    function watchPreference() {
      window.clearInterval(timer)
      if (!preference.matches) {
        timer = window.setInterval(() => {
          cursor.current = (cursor.current + 1) % paintedFrames.length
          setFrame(cursor.current)
        }, 110)
      }
    }
    watchPreference()
    preference.addEventListener('change', watchPreference)
    return () => {
      window.clearInterval(timer)
      preference.removeEventListener('change', watchPreference)
    }
  }, [speaking])

  return <figure className="ascii-pigeon" role="img" aria-label="ASCII-голубь с человеческими губами" data-speaking={speaking || undefined}>
    <pre aria-hidden="true">{(paintedFrames[speaking ? frame : 0] ?? []).map((row, y) => <span key={y}>
      {row.map(({ color, text }, x) => color ? <span key={x} style={{ color }}>{text}</span> : text)}
      {'\n'}
    </span>)}</pre>
  </figure>
}
