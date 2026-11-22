import type { Artifact } from './api'

export function isImageTranscript(artifact: Artifact) {
  return artifact.kind === 'quote' && /^Текст · .+\.(?:png|jpe?g|gif|webp|avif|bmp|tiff?|heic|svg)(?:\s*\([^)]*\))?$/i.test(artifact.title)
}
