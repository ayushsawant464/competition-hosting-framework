import { Outlet, Link, useLocation } from 'react-router-dom';
import { Terminal, Activity, Trophy, Code2 } from 'lucide-react';

export default function Layout() {
  const location = useLocation();

  const navItems = [
    { name: 'Overview', path: '/', icon: Terminal },
    { name: 'Dashboard', path: '/dashboard', icon: Code2 },
    { name: 'Leaderboard', path: '/leaderboard', icon: Trophy },
  ];

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground overflow-hidden">
      {/* Dynamic Background */}
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-primary/20 via-background to-background -z-10" />

      {/* Navigation Bar */}
      <header className="border-b border-border/50 bg-background/50 backdrop-blur-md sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center space-x-3">
              <Activity className="w-8 h-8 text-primary animate-pulse" />
              <span className="text-xl font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white to-gray-500">
                IICPC Hackathon '26
              </span>
            </div>
            <nav className="hidden md:flex space-x-8">
              {navItems.map((item) => {
                const Icon = item.icon;
                const isActive = location.pathname === item.path;
                return (
                  <Link
                    key={item.name}
                    to={item.path}
                    className={`flex items-center space-x-2 text-sm font-medium transition-colors hover:text-primary ${
                      isActive ? 'text-primary' : 'text-muted-foreground'
                    }`}
                  >
                    <Icon className="w-4 h-4" />
                    <span>{item.name}</span>
                  </Link>
                );
              })}
            </nav>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 w-full max-w-7xl mx-auto p-4 sm:p-6 lg:p-8">
        <Outlet />
      </main>
    </div>
  );
}
