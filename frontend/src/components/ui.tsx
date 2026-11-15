import { useEffect, useRef, useState, type ReactNode } from "react";
import { fetchBlobUrl } from "../api";

type Tone = "ok" | "warn" | "bad" | "info" | "neutral" | "dashed";

const PATHS: Record<string, ReactNode> = {
  ok: <path d="M5 12l5 5L20 7" />,
  warn: (
    <>
      <path d="M12 4l9 16H3z" />
      <path d="M12 10v4" />
      <path d="M12 17v.5" />
    </>
  ),
  bad: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v6" />
      <path d="M12 16.5v.5" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6" />
      <path d="M12 7.5v.5" />
    </>
  ),
};

export function Icon({ name, size = 14 }: { name: keyof typeof PATHS | string; size?: number }) {
  const body = PATHS[name];
  if (!body) return null;
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.3" strokeLinecap="round" strokeLinejoin="round">
      {body}
    </svg>
  );
}

/** A status label. The word carries the meaning; colour and icon only support it. */
export function Chip({ tone, children, icon = true }: { tone: Tone; children: ReactNode; icon?: boolean }) {
  return (
    <span className={`chip ${tone}`}>
      {icon && <Icon name={tone} size={13} />}
      {children}
    </span>
  );
}

export function Notice({ tone, children }: { tone: "ok" | "warn" | "bad" | "info"; children: ReactNode }) {
  return (
    <div className={`notice ${tone}`} role={tone === "bad" ? "alert" : "status"}>
      <Icon name={tone} size={16} />
      <div>{children}</div>
    </div>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="empty" role="status">
      {label}
    </div>
  );
}

export function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="card card-pad stack">
      <Notice tone="bad">{message}</Notice>
      <div>
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      </div>
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function StatusChip({ status }: { status: string }) {
  if (status === "active") return <Chip tone="ok">Active</Chip>;
  if (status === "invited") return <Chip tone="warn">Not activated</Chip>;
  if (status === "disabled") return <Chip tone="bad">Disabled</Chip>;
  return <Chip tone="neutral" icon={false}>{status}</Chip>;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}

/** An image that needs the session to load (worker photos). Shows initials until it arrives. */
export function Avatar({ url, name, size = 40 }: { url: string | null; name: string; size?: number }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    let made: string | null = null;
    setSrc(null);
    if (url) {
      fetchBlobUrl(url)
        .then((blob) => {
          made = blob;
          if (!cancelled) setSrc(blob);
        })
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
    };
  }, [url]);
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("");
  const style = { width: size, height: size, fontSize: Math.round(size * 0.38) };
  return src ? <img className="avatar" style={style} src={src} alt="" /> : <span className="avatar" style={style} aria-hidden="true">{initials}</span>;
}

const CERT_TONE: Record<string, "ok" | "warn" | "bad" | "dashed"> = { valid: "ok", expiring: "warn", expired: "bad", unknown: "dashed", missing: "bad" };

/** Certificate state: always a word, with colour and icon in support. */
export function CertChip({ status, label }: { status: string; label: string }) {
  const tone = CERT_TONE[status] ?? "dashed";
  return <Chip tone={tone} icon={tone !== "dashed"}>{label}</Chip>;
}
