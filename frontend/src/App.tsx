import { lazy, Suspense, useEffect } from 'react';
import { useRoute } from './landing/hooks';

// The landing page and the console are separate bundles: the graph engine and the sample backend
// are not downloaded until someone opens the console.
const Landing = lazy(() => import('./landing/Landing').then((m) => ({ default: m.Landing })));
const ConsoleApp = lazy(() => import('./ConsoleApp'));

function RouteFallback({ landing }: { landing: boolean }) {
  return (
    <div className={landing ? 'route-fallback light' : 'route-fallback'} role="status" aria-label="Loading">
      <span className="route-dot" />
    </div>
  );
}

export function App() {
  const route = useRoute();
  useEffect(() => {
    document.body.classList.toggle('on-landing', route === 'landing');
  }, [route]);

  return (
    <Suspense fallback={<RouteFallback landing={route === 'landing'} />}>
      {route === 'landing' ? <Landing /> : <ConsoleApp />}
    </Suspense>
  );
}
