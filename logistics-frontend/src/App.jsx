import React from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import Layout from './features/core/components/Layout';
import Dashboard from './features/dashboard/components/Dashboard';
import AnalyticsDashboard from './features/analytics/components/AnalyticsDashboard';
import CourierView from './features/courier/components/CourierView';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="analytics" element={<AnalyticsDashboard />} />
        </Route>
        <Route path="/courier/:id" element={<CourierView />} />
      </Routes>
    </BrowserRouter>
  );
}