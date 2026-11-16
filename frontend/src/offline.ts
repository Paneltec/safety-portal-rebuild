/** Work saved on this device while there is no connection, sent once when the connection returns.
 *
 * Only the person's own unsent work is kept here (a timesheet day, a form's answers); nothing
 * fetched from the server is stored. Each item carries a reference the server recognises, so
 * sending it twice cannot make two records. An item the server refuses is kept and shown with
 * the reason; it is never dropped or forced through. Items belong to the person who made them
 * and are only ever sent while that person is signed in.
 */
import { useEffect, useState } from "react";
import { ApiError, api } from "./api";

export interface QueuedItem {
  id: string;            // also the client reference the server de-duplicates on
  kind: "timesheet_day" | "form_submission";
  label: string;         // what the person sees: "Timesheet for Monday 9 November"
  path: string;
  method: "PUT" | "POST";
  body: unknown;
  queuedAt: string;
  owner: string;         // user id: one person's queue is never sent as another's
  error?: string;        // set when the server refused it
}

const KEY = "sp:queue:v1";
const listeners = new Set<() => void>();
let flushing = false;

function read(): QueuedItem[] {
  try { return JSON.parse(localStorage.getItem(KEY) ?? "[]") as QueuedItem[]; } catch { return []; }
}
function write(items: QueuedItem[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(items)); } catch { /* storage full or blocked: nothing more can be queued */ }
  listeners.forEach((fn) => fn());
}

export function queued(owner?: string): QueuedItem[] {
  return read().filter((i) => !owner || i.owner === owner);
}
export function pendingCount(owner?: string): number {
  return queued(owner).length;
}
export function enqueue(item: Omit<QueuedItem, "queuedAt">): void {
  write([...read().filter((i) => i.id !== item.id), { ...item, queuedAt: new Date().toISOString() }]);
}
export function discard(id: string): void {
  write(read().filter((i) => i.id !== id));
}
export function clearQueue(): void {
  write([]);
}
export function newReference(): string {
  return crypto.randomUUID();
}

/** True when a request failed because the server could not be reached at all (not because it said no). */
export function isOffline(error: unknown): boolean {
  return error instanceof ApiError && error.status === 0;
}

/** Try to send everything waiting for this person. Stops at the first item that cannot reach the server. */
export async function flush(owner: string): Promise<{ sent: number; refused: number }> {
  if (flushing) return { sent: 0, refused: 0 };
  flushing = true;
  let sent = 0;
  let refused = 0;
  try {
    for (const item of queued(owner)) {
      if (item.error) continue;  // already refused: waits for the person to look at it
      try {
        await api(item.path, { method: item.method, body: item.body });
        discard(item.id);
        sent += 1;
      } catch (e) {
        if (isOffline(e)) break;  // still no connection: keep everything and try later
        write(read().map((i) => (i.id === item.id ? { ...i, error: e instanceof Error ? e.message : "The server refused this" } : i)));
        refused += 1;
      }
    }
  } finally {
    flushing = false;
  }
  return { sent, refused };
}

/** Number of items waiting on this device; re-renders when it changes. */
export function useQueue(owner?: string): number {
  const [count, setCount] = useState(() => pendingCount(owner));
  useEffect(() => {
    const update = () => setCount(pendingCount(owner));
    listeners.add(update);
    window.addEventListener("storage", update);
    return () => { listeners.delete(update); window.removeEventListener("storage", update); };
  }, [owner]);
  return count;
}

/** Send what is waiting when the app opens and whenever the connection comes back. */
export function useAutoFlush(owner: string | undefined): void {
  useEffect(() => {
    if (!owner) return;
    const go = () => { void flush(owner); };
    go();
    window.addEventListener("online", go);
    return () => window.removeEventListener("online", go);
  }, [owner]);
}
