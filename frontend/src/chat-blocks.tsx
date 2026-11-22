import { useState } from 'react'
import { type BlockCell, type ChatBlock } from './api'

import { CitationLink, type CitationReference } from './citations'

function Cell({ cell, sources }: { cell: BlockCell | null; sources: ReadonlyMap<number, CitationReference> }) {
  if (!cell) return <span>Нет данных</span>
  const reference = sources.get(cell.ref)
  return <>{cell.value}{reference && <CitationLink reference={reference}/>}</>
}

export function ChatBlockView({ block, sources }: { block: ChatBlock; sources: ReadonlyMap<number, CitationReference> }) {
  const [filter, setFilter] = useState('')
  return <section className="artifact-card chat-data-block">
    <strong>{block.title}</strong>
    {block.kind === 'gap' ? <p>{block.note}</p> : block.kind === 'calculation' ?
      <p><Cell cell={block.operands[0]} sources={sources}/> {block.operator} <Cell cell={block.operands[1]} sources={sources}/> = <output>{block.result}</output></p> :
      <>
        {block.kind === 'table' && block.rows.length > 3 && <input type="search" aria-label="Фильтр таблицы"
          placeholder="Фильтр таблицы" value={filter} onChange={(event) => setFilter(event.target.value)}/>}
        <div className="table-scroll markdown"><table>
          <thead><tr>{block.columns.map((column, index) => <th key={index} scope="col">{column}</th>)}</tr></thead>
          <tbody>{block.rows.filter((row) => row.some((cell) => cell?.value.toLowerCase().includes(filter.toLowerCase()))).map((row, index) =>
            <tr key={index}>{row.map((cell, position) => <td key={position}><Cell cell={cell} sources={sources}/></td>)}</tr>,
          )}</tbody>
        </table></div>
      </>}
  </section>
}
