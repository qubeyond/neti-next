import pigeon from './assets/pigeon.webp'
import pigeonThinking from './assets/pigeon-thinking.webp'

export function PigeonAvatar({ speaking, thinking = false }: { speaking: boolean; thinking?: boolean }) {
  const isThinking = thinking && !speaking

  return <figure className="pigeon-avatar" role="img" aria-label={isThinking ? 'Голубь думает' : 'Голубь'} data-speaking={speaking || undefined} data-thinking={isThinking || undefined}>
    <div className="pigeon-portrait" aria-hidden="true">
      <img src={isThinking ? pigeonThinking : pigeon} alt="" width={516} height={1000}/>
      <div className="pigeon-mouth">
        <img src={pigeon} alt="" width={516} height={1000}/>
      </div>
    </div>
  </figure>
}
