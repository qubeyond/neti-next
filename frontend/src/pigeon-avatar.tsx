import pigeon from './assets/pigeon.webp'

export function PigeonAvatar({ speaking, thinking = false }: { speaking: boolean; thinking?: boolean }) {
  return <figure className="pigeon-avatar" role="img" aria-label="Голубь" data-speaking={speaking || undefined} data-thinking={thinking && !speaking || undefined}>
    <div className="pigeon-portrait" aria-hidden="true">
      <img src={pigeon} alt="" width={516} height={1000}/>
      <div className="pigeon-mouth">
        <img src={pigeon} alt="" width={516} height={1000}/>
      </div>
    </div>
  </figure>
}
