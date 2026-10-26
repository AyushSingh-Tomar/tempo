import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { DevIdentitySwitcher } from './DevIdentitySwitcher';

/**
 * The one persistent application shell. Pages render inside `children`;
 * the sidebar slot (run progress) is supplied per-page since it only
 * applies while a run is open.
 */
export function AppShell({ sidebar, children }: { sidebar?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 flex h-12 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4">
        <Link to="/" className="font-semibold text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500">
          Bordereaux Processing Accelerator
        </Link>
        <DevIdentitySwitcher />
      </header>
      <div className="flex flex-1 items-start">
        {sidebar}
        <main id="main-content" className="flex-1 p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
