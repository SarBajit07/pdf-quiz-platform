export function Notice({
  message,
  dismiss,
}: {
  message: string;
  dismiss: () => void;
}) {
  return message ? (
    <div className="toast" role="alert">
      {message}
      <button aria-label="Dismiss error" onClick={dismiss}>
        ×
      </button>
    </div>
  ) : null;
}
