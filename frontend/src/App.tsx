import { BrowserRouter, Route, Routes } from 'react-router-dom'
import ContentStudio from './ContentStudio'
import ScriptReview from './ScriptReview'

export default function App() {
  return <BrowserRouter>
    <Routes>
      <Route path="/" element={<ContentStudio />} />
      <Route path="/review/:scriptId" element={<ScriptReview />} />
    </Routes>
  </BrowserRouter>
}
