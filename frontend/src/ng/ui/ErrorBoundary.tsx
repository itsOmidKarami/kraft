import { Component, type ReactNode } from "react";
import { Button } from "./Button";
import "./ui.css";

/** One bad payload stops the page it was drawn on, not the whole app: a short message and a reload. `resetKey` (the
 *  route) clears a caught error, so leaving the page that failed shows the next one. */
export class ErrorBoundary extends Component<{ resetKey?: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidUpdate(prev: { resetKey?: string }) {
    if (this.state.failed && prev.resetKey !== this.props.resetKey) this.setState({ failed: false });
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="not-found" role="alert">
        <h1>This page could not be shown</h1>
        <p>Something in what the server sent was not what the page expected. Reloading usually clears it.</p>
        <Button variant="primary" onClick={() => window.location.reload()}>Reload</Button>
      </div>
    );
  }
}
