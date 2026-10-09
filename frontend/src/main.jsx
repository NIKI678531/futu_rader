import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { APP_BASE_PATH } from './config/runtime.js'
import RemoteApp from './RemoteApp'
import './styles/standalone.css'

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter basename={APP_BASE_PATH}>
      <RemoteApp basePath="" />
    </BrowserRouter>
  </React.StrictMode>,
)
