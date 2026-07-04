import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("ErrorBoundary caught render error", error, info);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
          <div className="text-base font-medium text-red-700">渲染出错</div>
          {this.state.error && (
            <div className="max-w-md break-words text-sm text-neutral-600">
              {this.state.error.message}
            </div>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              className="rounded border border-neutral-300 px-3 py-1 text-sm hover:bg-neutral-100"
              onClick={() => void window.api.app.restart()}
            >
              查看日志
            </button>
            <button
              type="button"
              className="rounded bg-neutral-800 px-3 py-1 text-sm text-white hover:bg-neutral-700"
              onClick={() => void window.api.app.restart()}
            >
              重启应用
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
