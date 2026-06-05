import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './components/Layout';
import Landing from './pages/Landing';
import Dashboard from './pages/Dashboard';
import Leaderboard from './pages/Leaderboard';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Landing />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="leaderboard" element={<Leaderboard />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
