import pigeon from './assets/pigeon.webp'

export function PigeonAvatar({ speaking }: { speaking: boolean }) {
  return <figure className="pigeon-avatar" role="img" aria-label="Голубь" data-speaking={speaking || undefined}>
    <div className="pigeon-portrait" aria-hidden="true">
      <img src={pigeon} alt="" width={516} height={1000}/>
      <div className="pigeon-mouth">
        <img src={pigeon} alt="" width={516} height={1000}/>
      </div>
    </div>
  </figure>
}
