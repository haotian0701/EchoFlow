import { BrowserRouter, Route, Routes } from 'react-router-dom'
import ContentStudio from './ContentStudio'
import ScriptReview from './ScriptReview'
import AudioStudio from './AudioStudio'
import JobMonitor from './JobMonitor'
import Dashboard from './Dashboard'

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
