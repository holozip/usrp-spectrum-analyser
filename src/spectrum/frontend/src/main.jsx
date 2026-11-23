import {Component, StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import Dashboard from './Dashboard.jsx';
import './dashboard.css';

class DashboardBoundary extends Component {
  state = {failed: false};

  static getDerivedStateFromError() {
    return {failed: true};
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="dashboard-error">
        <h1>The spectrum dashboard stopped rendering</h1>
        <p>Reload the page to reconnect to the receiver.</p>
        <button type="button" onClick={() => window.location.reload()}>Reload dashboard</button>
      </main>
    );
  }
}

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <DashboardBoundary><Dashboard /></DashboardBoundary>
  </StrictMode>,
);
