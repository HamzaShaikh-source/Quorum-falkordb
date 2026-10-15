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
import { App } from './App';
import { ErrorBoundary } from './components/ErrorBoundary';

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
