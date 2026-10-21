import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import ContentStudio from './pages/ContentStudio'
import ScriptReview from './pages/ScriptReview'
import AudioStudio from './pages/AudioStudio'
import JobMonitor from './pages/JobMonitor'
import Dashboard from './pages/Dashboard'

const router = createBrowserRouter([
  { path: '/', element: <ContentStudio /> },
  { path: '/review/:scriptId', element: <ScriptReview /> },
  { path: '/audio', element: <AudioStudio /> },
  { path: '/audio/:scriptId', element: <AudioStudio /> },
  { path: '/jobs', element: <JobMonitor /> },
  { path: '/jobs/:jobId', element: <JobMonitor /> },
  { path: '/dashboard', element: <Dashboard /> },
])
export default function App() { return <RouterProvider router={router} /> }
