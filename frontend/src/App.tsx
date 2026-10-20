import { BrowserRouter, Route, Routes } from 'react-router-dom'
import ContentStudio from './ContentStudio'
import ScriptReview from './ScriptReview'
import AudioStudio from './AudioStudio'

export default function App() {
  return <BrowserRouter>
    <Routes>
      <Route path="/" element={<ContentStudio />} />
      <Route path="/review/:scriptId" element={<ScriptReview />} />
      <Route path="/audio" element={<AudioStudio />} />
      <Route path="/audio/:scriptId" element={<AudioStudio />} />
    </Routes>
  </BrowserRouter>
}
