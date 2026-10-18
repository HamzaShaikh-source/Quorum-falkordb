import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/schibsted-grotesk/400.css';
import '@fontsource/schibsted-grotesk/500.css';
import '@fontsource/schibsted-grotesk/700.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource-variable/bricolage-grotesque';
import '@fontsource/silkscreen/400.css';
import './styles/tokens.css';
import './styles/app.css';
import './styles/proof-room.css';
import './styles/tools.css';
import './styles/layers.css';
import './styles/workspace.css';
import './landing/landing.css';
import './styles/overview.css';
import './styles/responsive.css';
import './styles/clarity.css';
import './styles/tour.css';
import { App } from './App';
import { ErrorBoundary } from './components/ErrorBoundary';

// A deep link for judges and demos: /?play=1 opens the console and plays the guided story by itself.
try {
  if (new URLSearchParams(location.search).get('play') === '1') {
    sessionStorage.setItem('quorum:autoplay', '1');
    if (!location.hash.startsWith('#/app')) location.hash = '#/app';
  }
} catch {
  /* storage can be unavailable: the link then just opens the console */
}

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
