import { BrowserRouter, Route, Routes } from 'react-router-dom'
import ContentStudio from './pages/ContentStudio'
import ScriptReview from './pages/ScriptReview'
import AudioStudio from './pages/AudioStudio'
import JobMonitor from './pages/JobMonitor'
import Dashboard from './pages/Dashboard'

export default function App() {
  return <BrowserRouter>
    <Routes>
      <Route path="/jobs" element={<JobMonitor />} />
      <Route path="/jobs/:jobId" element={<JobMonitor />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/" element={<ContentStudio />} />
      <Route path="/review/:scriptId" element={<ScriptReview />} />
      <Route path="/audio" element={<AudioStudio />} />
      <Route path="/audio/:scriptId" element={<AudioStudio />} />
    </Routes>
  </BrowserRouter>
}
