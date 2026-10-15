import { Component, type ErrorInfo, type ReactNode } from 'react';
import { devLog } from '../api/devlog';

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    devLog('error', `UI error: ${error.message} ${info.componentStack?.split('\n')[1]?.trim() ?? ''}`);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="blocking" role="alert">
        <h1>Something broke on this screen</h1>
        <p>The rest of your data is safe. Reload to start again, or go back to the landing page.</p>
        <p className="mono muted">{this.state.error.message}</p>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn primary" onClick={() => location.reload()}>
            Reload
          </button>
          <button
            className="btn"
            onClick={() => {
              location.hash = '#/';
              this.setState({ error: null });
            }}
          >
            Landing page
          </button>
        </div>
      </div>
    );
  }
}
