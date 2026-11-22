import { useEffect, useRef } from 'react'
import pigeon from './assets/pigeon.webp'

const columns = 124
const rows = 218
const portraitWidth = 112
const tau = Math.PI * 2
const rotationPeriodMs = 14_000
const rotationPauseMs = 1_500
const frameIntervalMs = 32

type Point = { x: number; z: number; shade: number }

function unit(value: number) {
  return Math.max(0, Math.min(1, value))
}

function backTexture(x: number, y: number, base: number) {
  const side = Math.abs(x)
  const highlight = 24 * (1 - side ** 2) - 14 * x
  if (y >= .74) {
    const scales = Math.cos((y * 50 + side * 2) * tau)
    return 120 + highlight * .6 + scales * 6
  }

  const wingY = (y - .33) / .39
  if (wingY <= 0 || wingY >= 1) return base + highlight

  // Сложенные крылья сужаются к хвосту, а две поперечные полосы идут по изгибу перьев.
  const width = .36 * Math.sin(Math.PI * wingY) ** .85
  const center = .53 - .23 * wingY
  const wingX = (side - center) / width
  const edge = unit((1 - Math.abs(wingX)) * 12)
  const feather = wingY + .12 * wingX ** 2 - .1 * wingX
  const bars = unit((.075 - Math.abs(feather - .48)) * 28) + unit((.085 - Math.abs(feather - .72)) * 28)
  const layeredFeathers = Math.cos((feather * 13 + Math.sin(wingX * 17) * .25) * tau)
  return base + highlight + edge * (41 - 63 * bars + 7 * layeredFeathers)
}

function buildSurface(image: HTMLImageElement) {
  const texture = document.createElement('canvas')
  texture.width = portraitWidth
  texture.height = rows
  const ctx = texture.getContext('2d')
  if (!ctx) return []

  ctx.drawImage(image, 0, 0, portraitWidth, rows)
  const { data } = ctx.getImageData(0, 0, portraitWidth, rows)
  const surface: Point[][] = Array.from({ length: rows }, () => [])

  for (const [y, points] of surface.entries()) {
    const position = y / rows
    const neck = Math.min(1, Math.max(0, (position - .25) / .19))
    const chest = Math.sin(Math.PI * Math.min(1, Math.max(0, (position - .30) / .49)))
    // Положение центров сечений формирует выступающую голову и округлую грудь.
    const centerZ = 20 - neck * neck * (3 - 2 * neck) * 23
    const backShade = 88 - 16 * chest
    const rearTexture = Array.from({ length: portraitWidth }, (_, x) =>
      backTexture(2 * x / (portraitWidth - 1) - 1, position, backShade))
    let x = 0

    while (x < portraitWidth) {
      const start = x
      while (x < portraitWidth && data[(y * portraitWidth + x) * 4 + 3]! >= 112) x++
      if (x === start) {
        x++
        continue
      }

      const radius = (x - start) / 2
      const centerX = (start + x) / 2 - portraitWidth / 2
      const depth = radius * (.57 + .21 * (1 - neck) + .32 * chest)
      const steps = Math.ceil(Math.PI * radius * 4)
      for (let step = 0; step < steps; step++) {
        const angle = step / steps * tau
        const offsetX = radius * Math.sin(angle)
        const front = Math.cos(angle)
        const texel = Math.min(x - 1, Math.floor(start + radius + offsetX))
        const index = (y * portraitWidth + texel) * 4
        const luminance = data[index]! * .2126 + data[index + 1]! * .7152 + data[index + 2]! * .0722
        const beak = Math.max(0, 1 - Math.hypot((centerX + offsetX) / 8, (position - .166) / .061))
        const portraitWeight = Math.sqrt(Math.max(0, front))
        const rearWeight = Math.sqrt(Math.max(0, -front))
        points.push({
          x: centerX + offsetX,
          z: centerZ + depth * front + 10 * beak * Math.max(0, front),
          shade: luminance * portraitWeight + backShade * (1 - portraitWeight - rearWeight) + rearTexture[texel]! * rearWeight,
        })
      }
    }
  }

  function ellipsoid(x: number, y: number, z: number, width: number, height: number, depth: number, shade: number) {
    for (let row = Math.ceil(y - height); row <= y + height; row++) {
      const edge = Math.sqrt(1 - ((row - y) / height) ** 2)
      for (let step = 0; step < 140; step++) {
        const angle = step / 140 * tau
        const front = Math.cos(angle)
        surface[row]!.push({
          x: x + Math.sin(angle) * width * edge,
          z: z + front * depth * edge,
          shade: shade + front * 15 - ((row - y) / height) * 8,
        })
      }
    }
  }

  // Крылья и веер хвоста выступают за корпус при повороте в профиль.
  for (const side of [-1, 1]) ellipsoid(side * 34, 112, -9, 8, 32, 25, 81)
  const tailStart = 133
  const tailEnd = 159
  for (let row = tailStart; row <= tailEnd; row++) {
    const length = (row - tailStart) / (tailEnd - tailStart)
    const width = (10 + 8 * length) * Math.sqrt(unit((1.03 - length) * 6))
    for (let step = 0; step < 140; step++) {
      const angle = step / 140 * tau
      const side = Math.sin(angle)
      const feather = Math.cos(side * 7 * Math.PI)
      surface[row]!.push({
        x: side * width,
        z: -38 - 17 * length + Math.cos(angle) * 5,
        shade: 77 + feather * 17 - length * 12,
      })
    }
  }
  return surface
}

export function PigeonVolume() {
  const canvas = useRef<HTMLCanvasElement>(null)
  const render = useRef<(() => void) | null>(null)
  const angle = useRef(0)
  const resumeAt = useRef(0)
  const pointer = useRef<{ id: number; x: number } | null>(null)

  useEffect(() => {
    const element = canvas.current
    const ctx = element?.getContext('2d')
    if (!element || !ctx) return
    const projection = document.createElement('canvas')
    projection.width = columns
    projection.height = rows
    const projectionContext = projection.getContext('2d')
    if (!projectionContext) return
    const pixels = projectionContext.createImageData(columns, rows)
    const depth = new Float32Array(columns)
    const source = new Image()
    let surface: Point[][] = []
    let request = 0
    let previousFrame = 0
    let visible = true
    const motion = matchMedia('(prefers-reduced-motion: reduce)')

    function draw() {
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
      if (!surface.length || !projectionContext) return
      const cosine = Math.cos(angle.current)
      const sine = Math.sin(angle.current)

      pixels.data.fill(0)
      for (const [y, points] of surface.entries()) {
        depth.fill(-Infinity)
        for (const point of points) {
          const x = Math.floor(columns / 2 + point.x * cosine + point.z * sine)
          if (x < 0 || x >= columns) continue
          const z = point.z * cosine - point.x * sine
          if (z <= depth[x]!) continue
          depth[x] = z
          const index = (y * columns + x) * 4
          pixels.data[index] = point.shade
          pixels.data[index + 1] = point.shade
          pixels.data[index + 2] = point.shade
          pixels.data[index + 3] = 255
        }
      }
      projectionContext.putImageData(pixels, 0, 0)
      ctx.drawImage(projection, 0, 0, width, displayHeight)
      element.dataset.ready = 'true'
    }

    function tick(now: number) {
      request = 0
      if (now - previousFrame >= frameIntervalMs) {
        const elapsed = now - Math.max(previousFrame || now, resumeAt.current)
        previousFrame = now
        if (elapsed > 0) {
          angle.current = (angle.current + elapsed * tau / rotationPeriodMs) % tau
          draw()
        }
      }
      schedule()
    }
    function schedule() {
      if (motion.matches || !visible || document.hidden || !surface.length || pointer.current) {
        cancelAnimationFrame(request)
        request = 0
        previousFrame = 0
      } else if (!request) {
        request = requestAnimationFrame(tick)
      }
    }
    render.current = () => {
      draw()
      schedule()
    }
    const observer = new ResizeObserver(() => render.current?.())
    const visibility = new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting ?? false
      if (visible) render.current?.()
      else schedule()
    })
    observer.observe(element)
    visibility.observe(element)
    motion.addEventListener('change', schedule)
    document.addEventListener('visibilitychange', schedule)
    source.onload = () => {
      surface = buildSurface(source)
      resumeAt.current = performance.now() + rotationPauseMs
      render.current?.()
    }
    source.src = pigeon
    return () => {
      source.onload = null
      observer.disconnect()
      visibility.disconnect()
      motion.removeEventListener('change', schedule)
      document.removeEventListener('visibilitychange', schedule)
      cancelAnimationFrame(request)
      render.current = null
    }
  }, [])

  function turn(change: number) {
    resumeAt.current = performance.now() + rotationPauseMs
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
        event.currentTarget.setPointerCapture(event.pointerId)
        pointer.current = { id: event.pointerId, x: event.clientX }
        render.current?.()
      }}
      onPointerMove={(event) => {
        if (pointer.current?.id !== event.pointerId) return
        turn((event.clientX - pointer.current.x) * .018)
        pointer.current.x = event.clientX
      }}
      onLostPointerCapture={() => {
        pointer.current = null
        resumeAt.current = performance.now() + rotationPauseMs
        render.current?.()
      }}>
      Чёрно-белый голубь.
    </canvas>
  </figure>
}
