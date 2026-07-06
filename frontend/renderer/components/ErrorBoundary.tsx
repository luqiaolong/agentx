import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RotateCcw, FileText } from "lucide-react";
import { useSettingsStore } from "@/stores/settings";

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
        <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-rose-500/10 text-rose-500 ring-1 ring-rose-500/20">
            <AlertTriangle className="h-6 w-6" />
          </div>
          <div className="font-semibold text-primary-c" style={{ fontSize: 'var(--fs-msg-heading)' }}>渲染出错</div>
          {this.state.error && (
            <div className="max-w-md break-words rounded-lg border border-default bg-subtle/50 px-3 py-2 text-secondary-c" style={{ fontSize: 'var(--fs-empty-desc)' }}>
              {this.state.error.message}
            </div>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => {
                // 打开独立日志窗口，便于用户排查渲染错误
                useSettingsStore.getState().setLogsModalOpen(true);
              }}
            >
              <FileText className="h-3.5 w-3.5" />
              查看日志
            </button>
            <button type="button" className="btn-primary" onClick={() => void window.api.app.restart()}>
              <RotateCcw className="h-3.5 w-3.5" />
              重启应用
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
