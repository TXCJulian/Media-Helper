import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error('[ErrorBoundary]', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="relative z-1 mx-auto mt-12 max-w-lg">
          <div className="glass-strong p-8 text-center">
            <h2 className="mb-3 text-xl font-bold text-[var(--error)]">Something went wrong</h2>
            <p className="mb-6 text-[0.875rem] text-[var(--text-secondary)]">
              {this.state.error?.message}
            </p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="cursor-pointer rounded-xl border-none bg-[var(--accent)] px-6 py-3 font-[Geist,sans-serif] text-[0.875rem] font-semibold text-white transition-all duration-300 hover:-translate-y-px hover:shadow-[0_0_32px_var(--accent-glow-strong),0_4px_20px_rgba(0,0,0,0.3)]"
            >
              Try Again
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
