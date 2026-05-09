import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './features/core/components/Layout';
import DashboardPage from './pages/DashboardPage';
import OptimizationPage from './pages/OptimizationPage';
import ScenarioPage from './pages/ScenarioPage';
import AdminPage from './pages/AdminPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<DashboardPage />} />
          <Route path="optimization" element={<OptimizationPage />} />
          <Route path="scenarios" element={<ScenarioPage />} />
          <Route path="admin" element={<AdminPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}