const asciiRows = [
  '01<>#{}+*..',
  '>>/=09?[]#*',
  '{0}::++<>01',
]

export function PigeonThought() {
  return <div className="pigeon-thought" aria-hidden="true">
    <div className="pigeon-thought-cloud">
      <div className="pigeon-thought-glyphs">
        {asciiRows.map((row) => <span className="pigeon-thought-line" key={row}>{`${row}   ${row}   `}</span>)}
      </div>
    </div>
    <span className="pigeon-thought-puff pigeon-thought-puff-large"/>
    <span className="pigeon-thought-puff pigeon-thought-puff-small"/>
  </div>
}
