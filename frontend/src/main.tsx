import { createRoot } from 'react-dom/client'
import './style.css'

const root = document.getElementById('root')
if (!root) throw new Error('Missing application root.')

createRoot(root).render(
  <main>
    <h1>NETI Next</h1>
    <p>Сервис вопросов о вузах Новосибирска.</p>
  </main>,
)
