import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

// Keeps a rendering bug in one page from blanking the whole console.
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("page render failed", error, info.componentStack);
  }

  render() {
    if (this.state.error)
      return (
        <div role="alert" className="rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
          <p className="font-medium">This page failed to render.</p>
          <p className="mt-1 font-mono text-xs">{this.state.error.message}</p>
          <button className="mt-3 underline" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      );
    return this.props.children;
  }
}
