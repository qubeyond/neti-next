const tonalRange = '.,:-=+ox%&@#'

export function pigeonShade(color: string, glyph: string, eye = false) {
  const red = Number.parseInt(color.slice(1, 3), 16)
  const green = Number.parseInt(color.slice(3, 5), 16)
  const blue = Number.parseInt(color.slice(5, 7), 16)
  const tone = tonalRange.indexOf(glyph)
  const brightness = red * .21 + green * .72 + blue * .07
  const value = Math.round(eye ? 18 + brightness * .36 : 78 + brightness * .72 - Math.max(0, tone) * 4)

  return `rgb(${value} ${value} ${value})`
}
