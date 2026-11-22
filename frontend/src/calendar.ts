import { safeURL, type CalendarEvent, type Citation } from './api'

function calendarText(value: string) {
  return value.replaceAll('\\', '\\\\').replace(/\r\n|\r|\n/g, '\\n').replace(/[,;]/g, '\\$&')
}

function foldCalendarLine(value: string) {
  const encoder = new TextEncoder()
  let folded = ''
  let bytes = 0
  for (const character of value) {
    const size = encoder.encode(character).length
    if (bytes + size > 75) {
      folded += '\r\n '
      bytes = 1
    }
    folded += character
    bytes += size
  }
  return folded
}

export function downloadDeadline(event: CalendarEvent, citation: Citation, id: string) {
  const url = safeURL(citation.url)
  if (!url || !/^\d{4}-\d{2}-\d{2}$/.test(event.date)) return
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//NETI Next//Deadline//RU',
    'BEGIN:VEVENT',
    'UID:' + id + '@neti-next',
    'DTSTAMP:' + new Date().toISOString().replace(/[-:]|\.\d{3}/g, ''),
    'DTSTART;VALUE=DATE:' + event.date.replaceAll('-', ''),
    'SUMMARY:' + calendarText(event.title),
    'DESCRIPTION:' + calendarText(citation.quote + '\n' + url),
    'END:VEVENT',
    'END:VCALENDAR',
  ]
  const file = new Blob([lines.map(foldCalendarLine).join('\r\n') + '\r\n'], { type: 'text/calendar;charset=utf-8' })
  const address = URL.createObjectURL(file)
  const link = document.createElement('a')
  link.href = address
  link.download = 'neti-' + event.date + '.ics'
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(address), 2000)
}
