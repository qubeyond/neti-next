import { useEffect, useRef } from 'react'
import colors from './assets/pigeon-colors.json'
import art from './assets/pigeon-ascii.txt?raw'
import { pigeonShade } from './pigeon-shading'

const palette: Record<string, string> = colors.palette
const rows = art.trimEnd().split('\n')
const columns = 74
const height = rows.length + 4
const tau = Math.PI * 2

type Point = { x: number; y: number; z: number; color: string }

function buildSurface() {
  const points: Point[] = []

  function ellipsoid(x: number, y: number, z: number, width: number, tall: number, depth: number, color: string, feathered = false) {
    for (let latitude = 0; latitude <= 32; latitude++) {
      const vertical = latitude / 32 * Math.PI
      for (let longitude = 0; longitude < 85; longitude++) {
        const around = longitude / 85 * tau
        const edge = Math.sin(vertical)
        const stripe = feathered && latitude > 13 && latitude < 22 && latitude % 5 < 2
        points.push({
          x: x + Math.sin(around) * edge * width,
          y: y + Math.cos(vertical) * tall,
          z: z + Math.cos(around) * edge * depth,
          color: pigeonShade(stripe ? '#263746' : color, stripe ? '%' : latitude % 3 === 0 ? '=' : 'x'),
        })
      }
    }
  }

  for (const [y, line] of rows.entries()) {
    for (const match of line.matchAll(/\S+/g)) {
      const start = match.index
      const length = match[0].length
      const radius = length / 2
      const center = start + (length - 1) / 2 - 28.5
      // Грудь выпуклая спереди, спина и хвост вытянуты назад.
      const body = y >= 25 && y <= 52
      const head = y < 23
      const centerZ = head ? 14 : y < 27 ? 14 - (y - 23) * 4 : body ? -3 : 2
      const depth = head ? radius * .85 : body ? radius * .95 : radius * .5
      for (let step = 0; step < length * 3; step++) {
        const angle = step * tau / (length * 3)
        const offset = Math.sin(angle)
        const source = Math.min(length - 1, Math.round((offset + 1) * (length - 1) / 2))
        const front = Math.cos(angle)
        const feature = y >= 8 && y <= 18 && Math.abs(center + radius * offset) < 8 && front > 0 ? 1.18 : 1
        const backOfHead = front < -.12 && head
        const color = palette[backOfHead ? (Math.abs(offset) < .58 ? '3' : '5') : colors.rows[y]?.[start + source] ?? '']
        const glyph = backOfHead ? (Math.abs(offset) < .58 ? 'o' : 'x') : match[0][source] ?? '+'
        const eye = y >= 8 && y <= 10 && front > .55 && Math.abs(center + radius * offset) < (y === 10 ? 2.5 : 4.5)
        if (color) points.push({
          x: center + radius * offset,
          y: y - rows.length / 2 + (y < 23 ? 6 : y < 29 ? 29 - y : 0),
          z: centerZ + front * depth * feature,
          color: pigeonShade(color, glyph, eye),
        })
      }
    }
  }
  for (const side of [-1, 1]) {
    // Сложенные крылья закрывают бок корпуса и продолжаются к хвосту.
    ellipsoid(side * 14.5, 5, -5.5, 3.3, 12, 13, '#526c85', true)
    ellipsoid(side * 2.5, 18, -19, 3, 3.8, 14, '#34475b', true)
  }
  // Клюв выступает перед головой и виден в профиль.
  for (let step = 0; step <= 12; step++) {
    const radius = 2.6 * (1 - step / 13)
    for (let around = 0; around < 20; around++) {
      const turn = around / 20 * tau
      points.push({
        x: Math.cos(turn) * radius, y: -16 + step * .22 + Math.sin(turn) * radius * .5,
        z: 21 + step * .55, color: pigeonShade('#69777f', '+'),
      })
    }
  }
  return points
}

const surface = buildSurface()

export function PigeonVolume() {
  const canvas = useRef<HTMLCanvasElement>(null)
  const render = useRef<(() => void) | null>(null)
  const angle = useRef(-.12)
  const touched = useRef(0)
  const pointer = useRef<{ id: number; x: number } | null>(null)

  useEffect(() => {
    const element = canvas.current
    const ctx = element?.getContext('2d')
    if (!element || !ctx) return
    const depth = new Float32Array(columns * height)
    const pixels: (Point | undefined)[] = Array(columns * height)
    let request = 0
    let previousFrame = 0
    let visible = true
    const motion = matchMedia('(prefers-reduced-motion: reduce)')

    function draw(now = performance.now()) {
      if (!element || !ctx) return
      const { width, height: displayHeight } = element.getBoundingClientRect()
      if (!width || !displayHeight) return
      const ratio = Math.min(2, window.devicePixelRatio)
      const pixelWidth = Math.round(width * ratio)
      const pixelHeight = Math.round(displayHeight * ratio)
      if (element.width !== pixelWidth || element.height !== pixelHeight) {
        element.width = pixelWidth
        element.height = pixelHeight
      }
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
      ctx.clearRect(0, 0, width, displayHeight)
      const cellWidth = width / columns
      const cellHeight = displayHeight / height
      depth.fill(-Infinity)
      pixels.fill(undefined)
      const phase = Math.min(1, Math.max(0, (now - touched.current - 1800) / 1400))
      const idle = motion.matches || pointer.current ? 0 : Math.sin((now - touched.current) / 2400) * .26 * phase
      const cosine = Math.cos(angle.current + idle)
      const sine = Math.sin(angle.current + idle)
      for (const point of surface) {
        const z = point.z * cosine - point.x * sine
        const projection = 1 + z * .004
        const x = Math.round(columns / 2 + (point.x * cosine + point.z * sine) * projection)
        const y = Math.round(height / 2 + point.y * projection)
        if (x < 0 || x >= columns || y < 0 || y >= height) continue
        const index = y * columns + x
        if (z > (depth[index] ?? -Infinity)) {
          depth[index] = z
          pixels[index] = point
        }
      }
      for (const [index, point] of pixels.entries()) {
        const column = index % columns
        const neighbor = column > 0 && column < columns - 1 && pixels[index + 1] && pixels[index - 1]
        const visiblePoint = point || neighbor
        if (!visiblePoint) continue
        ctx.fillStyle = visiblePoint.color
        ctx.fillRect(column * cellWidth, Math.floor(index / columns) * cellHeight, cellWidth + .5, cellHeight + .5)
      }
      element.dataset.ready = 'true'
    }

    function tick(now: number) {
      if (visible && !document.hidden && now - previousFrame >= 40) {
        draw(now)
        previousFrame = now
      }
      if (!motion.matches && visible && !document.hidden) request = requestAnimationFrame(tick)
      else request = 0
    }
    function schedule() {
      if (!request) request = requestAnimationFrame(tick)
    }
    render.current = () => {
      draw()
      schedule()
    }
    const observer = new ResizeObserver(() => render.current?.())
    const visibility = new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting ?? false
      if (visible) render.current?.()
    })
    observer.observe(element)
    visibility.observe(element)
    motion.addEventListener('change', schedule)
    document.addEventListener('visibilitychange', schedule)
    render.current()
    return () => {
      observer.disconnect()
      visibility.disconnect()
      motion.removeEventListener('change', schedule)
      document.removeEventListener('visibilitychange', schedule)
      cancelAnimationFrame(request)
      render.current = null
    }
  }, [])

  function turn(change: number) {
    touched.current = performance.now()
    angle.current = (angle.current + change) % tau
    render.current?.()
  }

  return <figure className="pigeon-turntable">
    <canvas ref={canvas} tabIndex={0} aria-label="Объёмный голубь. Перетащите в сторону или нажмите стрелку влево либо вправо."
      onKeyDown={(event) => {
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
        event.preventDefault()
        turn(event.key === 'ArrowLeft' ? -.22 : .22)
      }}
      onPointerDown={(event) => {
        if (!event.isPrimary || event.button !== 0) return
        touched.current = performance.now()
        event.currentTarget.setPointerCapture(event.pointerId)
        pointer.current = { id: event.pointerId, x: event.clientX }
      }}
      onPointerMove={(event) => {
        if (pointer.current?.id !== event.pointerId) return
        turn((event.clientX - pointer.current.x) * .018)
        pointer.current.x = event.clientX
      }}
      onLostPointerCapture={() => { pointer.current = null }}>
      Объёмный Пигеон.
    </canvas>
  </figure>
}
