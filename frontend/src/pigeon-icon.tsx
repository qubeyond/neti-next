const glyphs = {
  admission: <path d="M2.5 7.8 10 4l7.5 3.8L10 11.6 2.5 7.8ZM5.5 9.5v4c2.5 2.1 6.5 2.1 9 0v-4m3-1.7v5.4"/>,
  housing: <path d="M3 16.8V7.2l4.4-4h7.4l2.2 2.2v11.4M2.5 16.8h15M8 16.8v-4.1h4v4.1M6.5 7.8h2m3.2 0h2"/>,
  calendar: <path d="M5 4.8h10l1.8 1.8v8.6l-1.7 1.7H5l-1.8-1.8V6.6L5 4.8Zm1.5-1.7v3.4m7-3.4v3.4M3.2 8.6h13.6M7 12h2m2.6 0H13"/>,
  next: <path d="M3.5 10h12.7m-4.5-4.5 4.5 4.5-4.5 4.5"/>,
  external: <path d="M5 15 15 5M8.2 5H15v6.8"/>,
  send: <path d="M10 16.5v-13m-4.6 4.6L10 3.5l4.6 4.6"/>,
  close: <path d="m5.2 5.2 9.6 9.6m0-9.6-9.6 9.6"/>,
  plus: <path d="M10 4v12M4 10h12"/>,
  attach: <path d="m7 12.8 5.4-5.4a1.6 1.6 0 0 1 2.3 2.3l-6.2 6.2a3.1 3.1 0 0 1-4.4-4.4l7.1-7.1a4 4 0 0 1 5.7 5.7l-6.5 6.5"/>,
  mic: <><rect x="7.4" y="2.8" width="5.2" height="9" rx="2.6"/><path d="M4.7 9.3a5.3 5.3 0 0 0 10.6 0M10 14.6v2.8m-2.8 0h5.6"/></>,
  stop: <rect x="5.8" y="5.8" width="8.4" height="8.4" rx="1.3" fill="currentColor" stroke="none"/>,
  waveform: <path d="M3 8.5v3m3.5-5v7m3.5-9v11m3.5-9v7m3.5-5v3"/>,
  file: <path d="M5 3.1h6.4l3.6 3.6v10.2H5V3.1Zm6 0v4h4M7.8 11h4.4m-4.4 2.9h3.2"/>,
  camera: <><path d="M3 6.8h3.2l1.6-2h4.4l1.6 2H17v8.8H3V6.8Z"/><circle cx="10" cy="11.2" r="2.4"/></>,
  copy: <><rect x="7.4" y="7.4" width="8.8" height="8.8" rx="1.3"/><path d="M12.5 3.8H5.1c-.8 0-1.3.5-1.3 1.3v7.4"/></>,
  photo: <><rect x="3.3" y="4" width="13.4" height="12" rx="1.6"/><path d="m3.5 13.9 3.8-3.8 3.4 3 2.3-2.2 3.6 3.6M12.4 7.6h.2"/></>,
  download: <path d="M10 3v9.3M6.7 9l3.3 3.3L13.3 9m-9 4v3.4h11.4V13"/>,
  retry: <path d="M15.9 8.3a6 6 0 1 0 .1 4.1m-3.8-4.1h3.9V4.4"/>,
  warning: <path d="M10 3.3 17 16H3l7-12.7Zm0 4.8v3.6m0 1.9h.01"/>,
  sidebar: <><rect x="3" y="4.1" width="14" height="11.8" rx="1.6"/><path d="M7.5 4.5v11"/></>,
  search: <><circle cx="8.8" cy="8.8" r="4.8"/><path d="m12.2 12.2 4 4"/></>,
  chat: <path d="M15.1 4.2H4.9L3.3 6v6.5l1.6 1.6v2.8l3.3-2.8h7l1.5-1.6V6l-1.6-1.8ZM6.5 8h7m-7 3h4"/>,
  trash: <path d="M3.8 5.9h12.4M7.8 5.9v-2h4.4v2m2.8 0-.7 10.3H5.7L5 5.9m3.3 3v4.4m3.4-4.4v4.4"/>,
  private: <path d="M3.1 9.2h13.8M5.4 9.2l1.5-4.5 3.1 1.1 3.1-1.1 1.5 4.5m-5.9 4.9a2.2 2.2 0 1 1-4.4 0 2.2 2.2 0 0 1 4.4 0Zm7 0a2.2 2.2 0 1 1-4.4 0 2.2 2.2 0 0 1 4.4 0Zm-7-.1h2.6"/>,
  settings: <path d="M3.6 6.2h12.8M3.6 13.8h12.8M7.5 3.9v4.6m5 3v4.6"/>,
  clock: <><circle cx="10" cy="10" r="6.7"/><path d="M10 6.4v3.8l2.4 1.4"/></>,
  activity: <path d="M4 6h4l2.2-2.2h5.7M4 10h7m-7 4h4l2.2 2.2h5.7"/>,
}

export function PigeonIcon({ name, className }: { name: keyof typeof glyphs; className?: string }) {
  return <svg className={className} width="1em" height="1em" viewBox="0 0 20 20" fill="none"
    stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {glyphs[name]}
  </svg>
}
