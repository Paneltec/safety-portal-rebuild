import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";

interface Item {
  id: string;
  category: string;
  title: string;
  subtitle: string | null;
  severity: "info" | "warn" | "bad";
  link: string;
  read: boolean;
}

/** Things that need this person's attention. Worked out by the server from live records. */
export function Bell() {
  const [items, setItems] = useState<Item[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();
  const ref = useRef<HTMLDivElement>(null);

  function load() {
    api<{ items: Item[]; unread_count: number }>("/api/notifications")
      .then((r) => { setItems(r.items); setUnread(r.unread_count); })
      .catch(() => undefined);
  }

  // Refresh when moving between screens, which is when the underlying records tend to change.
  useEffect(load, [location.pathname]);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);

  async function go(item: Item) {
    setOpen(false);
    if (!item.read) await api(`/api/notifications/${encodeURIComponent(item.id)}/read`, { method: "POST" }).catch(() => undefined);
    load();
    navigate(item.link);
  }

  return (
    <div className="bell" ref={ref}>
      <button type="button" className="btn" aria-expanded={open} aria-label={unread ? `Notifications, ${unread} unread` : "Notifications, none unread"} onClick={() => setOpen(!open)}>
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 9a6 6 0 0112 0c0 6 2.5 7 2.5 7h-17S6 15 6 9z" /><path d="M10 20a2 2 0 004 0" />
        </svg>
        {unread > 0 && <span className="bell-count">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div className="bell-panel" role="dialog" aria-label="Notifications">
          <div className="row" style={{ justifyContent: "space-between", padding: "10px 12px" }}>
            <strong>Notifications</strong>
            {unread > 0 && <button type="button" className="link-btn" onClick={() => void api("/api/notifications/mark-all-read", { method: "POST" }).then(load)}>Mark all as read</button>}
          </div>
          {items.length === 0 ? <div className="empty" style={{ padding: 16 }}>Nothing needs your attention.</div> : (
            <ul>
              {items.map((item) => (
                <li key={item.id}>
                  <button type="button" className={`bell-item ${item.severity}${item.read ? " read" : ""}`} onClick={() => void go(item)}>
                    <span style={{ fontWeight: item.read ? 400 : 600 }}>{item.title}</span>
                    {item.subtitle && <span className="small muted">{item.subtitle}</span>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
