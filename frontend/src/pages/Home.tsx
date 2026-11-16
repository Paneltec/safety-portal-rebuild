import { useState } from "react";
import { Link } from "react-router-dom";
import { api, formatDateTime, formatDuration, type Attendance } from "../api";
import { useAuth } from "../auth";
import { Chip, Notice, errorMessage } from "../components/ui";
import { useLoad } from "../hooks";
import { Dashboard } from "./Dashboard";

/** A pointer to today's job, when there is one. */
function JobToday() {
  const today = useLoad(() => api<{ assignment: { site_name: string | null; start_time: string | null; status_label: string } | null }>("/api/daily-jobs/mine/today"), []);
  const job = today.data?.assignment;
  if (!job) return null;
  return (
    <section className="job-card" style={{ marginBottom: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span><strong>Today's job: {job.site_name}</strong>{job.start_time ? ` · start ${job.start_time}` : ""} · {job.status_label}</span>
        <Link className="btn primary" to="/app/my-work">Open my work</Link>
      </div>
    </section>
  );
}

/** Where the signed-in person is signed on right now, with a way to sign off without the QR code. */
export function SignedOnNow({ timezone }: { timezone: string }) {
  const mine = useLoad(() => api<{ items: Attendance[] }>("/api/sites/my-attendance"), []);
  const [error, setError] = useState<string | null>(null);
  if (!mine.data || mine.data.items.length === 0) return null;
  return (
    <section className="card card-pad stack">
      <h2>You are signed on</h2>
      {error && <Notice tone="bad">{error}</Notice>}
      {mine.data.items.map((visit) => (
        <div key={visit.id} className="row" style={{ justifyContent: "space-between" }}>
          <span>
            <strong>{visit.site_name}</strong> since {formatDateTime(visit.signed_in_at, timezone)} ({formatDuration(visit.minutes)})
          </span>
          <button type="button" className="btn" onClick={async () => {
            setError(null);
            try {
              await api(`/api/sites/${visit.site_id}/signoff`, { method: "POST" });
              mine.reload();
            } catch (e) { setError(errorMessage(e)); }
          }}>Sign off</button>
        </div>
      ))}
    </section>
  );
}

// Honest build status. A stage is only "Built" once it works end to end with tests.
const STAGES: { name: string; detail: string; built: boolean | "partial" }[] = [
  { name: "1. Sign-in, users, roles and permissions", detail: "Logins, invitations, sessions, role catalogue, per-person access", built: true },
  { name: "2. Organisation, documents and the form engine", detail: "Organisation settings, logo, file storage, document library, form builder, form PDFs and assignments", built: true },
  { name: "3. Safety capture", detail: "SWMS, pre-starts, site diary, incidents and hazards, inspections, risk assessments, SSRA", built: true },
  { name: "4. Workers, suppliers, sites and jobs", detail: "Workers, certifications, inductions and training, sites, QR sign-in, visitors, suppliers, renewal links and daily jobs are built. Still to come: Simpro imports (stage 5), worker ID cards, supplier QR induction, spreadsheet import of inductions", built: "partial" },
  { name: "5. Integrations and communications", detail: "Connections, safe mode, the outbox, notifications, the Simpro import of workers, sites and suppliers, and expiry reminders are built. Still to come: Simpro jobs and attachments, and Navixy and SmartFill data (with fleet and fuel)", built: "partial" },
  { name: "6. Fleet, service and fuel", detail: "The fleet and equipment register, QR labels, meters, the Navixy vehicle list, service schedules, fuel import, the weekly diesel rate, fuel reports and the fuel check list are built. Navixy, SmartFill and FuelCheck have only been tested against stand-ins, not the real services. Still to come: trips and maps, service check sheets, scheduled fuel import", built: "partial" },
  { name: "7. Leave, timesheets and pay (test-only)", detail: "Leave, timesheets, named payroll access and the test pay workbench (pay rules, employee pay settings, weekly runs, payslips in the portal, evidence recorded by hand) are built. Nothing is ever paid, lodged or sent, and the tax table has not been checked against the Tax Office schedule. Still to come: leave balances, more pay reports", built: "partial" },
  { name: "8. Phone app", detail: "The phone layout (open /m and add it to the home screen), phone home, scanning with a typed-code fallback, what each person's phone shows, and saving a timesheet day or form when the signal drops are built. It has only been tested in a desktop browser at phone size, not on real phones. There is no app-store app. Still to come: PIN sign-in, push notifications, photos with no signal", built: "partial" },
  { name: "9. Dashboard, search, audit, backups and system check", detail: "The dashboard, search by name, the activity log, audit packs, backups with checking and a restore rehearsal, and the system check are built. Backups only run when asked or on a timer set up on the server, and are not sent to a second place. Still to come: a NAS agent, updates from inside the portal, the built-in manual, AI questions", built: "partial" },
];

export function Home() {
  const { me, can } = useAuth();
  if (!me) return null;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Welcome, {me.name.split(" ")[0]}</h1>
          <p className="muted">
            {me.org_name} · signed in as {me.role_name}
          </p>
        </div>
      </div>

      <SignedOnNow timezone={me.org_timezone} />
      <JobToday />
      <Dashboard />

      <div className="grid-2">
        <section className="card">
          <div className="card-head">
            <h2>What you can do now</h2>
          </div>
          <div className="card-pad stack" style={{ paddingTop: 4 }}>
            {can("users", "view") ? (
              <>
                <Link to="/app/settings/users">Manage users and invitations</Link>
                <Link to="/app/settings/roles">Review roles and the permission matrix</Link>
              </>
            ) : (
              <p className="muted">Your role does not include user administration.</p>
            )}
            {me.role_id === "admin" && <Link to="/app/settings/sessions">See who is signed in</Link>}
            <Link to="/app/profile">Update your profile or password</Link>
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h2>Build progress</h2>
            <span className="small muted">Independent rebuild · synthetic data only</span>
          </div>
          <ul className="stage-list">
            {STAGES.map((stage) => (
              <li key={stage.name}>
                <div>
                  <div style={{ fontWeight: 600 }}>{stage.name}</div>
                  <div className="small muted">{stage.detail}</div>
                </div>
                {stage.built === true ? <Chip tone="ok">Built and tested</Chip> : stage.built === "partial" ? <Chip tone="warn">Partly built</Chip> : <Chip tone="dashed" icon={false}>Not built yet</Chip>}
              </li>
            ))}
          </ul>
        </section>
      </div>
    </>
  );
}
