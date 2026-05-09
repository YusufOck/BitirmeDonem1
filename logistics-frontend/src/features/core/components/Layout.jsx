import React from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import './Layout.css';

const navItems = [
  {
    to: '/',
    label: 'Dashboard',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 13h8V3H3z" />
        <path d="M13 21h8V11h-8z" />
        <path d="M13 9h8V3h-8z" />
        <path d="M3 21h8v-6H3z" />
      </svg>
    ),
  },
  {
    to: '/optimization',
    label: 'Route Planner',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="6" cy="6" r="3" />
        <circle cx="18" cy="18" r="3" />
        <path d="M8.6 7.4 15.4 16.6" />
        <path d="M18 6h.01" />
        <path d="M6 18h.01" />
      </svg>
    ),
  },
  {
    to: '/scenarios',
    label: 'Live Monitor',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2v4" />
        <path d="M12 18v4" />
        <path d="M4.93 4.93 7.76 7.76" />
        <path d="M16.24 16.24 19.07 19.07" />
        <path d="M2 12h4" />
        <path d="M18 12h4" />
        <path d="M4.93 19.07 7.76 16.24" />
        <path d="M16.24 7.76 19.07 4.93" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    ),
  },
  {
    to: '/admin',
    label: 'System & Reports',
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M4 19V5" />
        <path d="M4 19h16" />
        <path d="M8 16v-5" />
        <path d="M12 16V8" />
        <path d="M16 16v-3" />
      </svg>
    ),
  },
];

export default function Layout() {
  return (
    <div className="layout-container">
      <aside className="sidebar glass-panel">
        <div className="sidebar-header">
          <div className="logo-icon">S</div>
          <h2>SBTU Logistics</h2>
        </div>

        <nav className="sidebar-nav" aria-label="Main navigation">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}
            >
              {item.icon}
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="system-status">
            <span className="status-indicator online"></span>
            <span>System online</span>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}