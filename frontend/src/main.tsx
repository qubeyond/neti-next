import { createRoot } from 'react-dom/client'
import { App } from './app'
import '@fontsource/press-start-2p/cyrillic-400.css'
import './style.css'

const root = document.getElementById('root')
if (!root) throw new Error('Missing application root.')

createRoot(root).render(<App/>)
