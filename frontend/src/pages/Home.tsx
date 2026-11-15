import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import { Chip } from "../components/ui";

// Honest build status. A stage is only "Built" once it works end to end with tests.
const STAGES: { name: string; detail: string; built: boolean | "partial" }[] = [
  { name: "1. Sign-in, users, roles and permissions", detail: "Logins, invitations, sessions, role catalogue, per-person access", built: true },
  { name: "2. Organisation, documents and the form engine", detail: "Organisation settings, document library and the form builder are built; form PDFs and assignments are next", built: "partial" },
  { name: "3. Safety capture", detail: "SWMS, pre-starts, site diary, incidents and hazards, inspections, risk", built: false },
  { name: "4. Workers, suppliers, sites and jobs", detail: "Certifications, inductions, renewals, visitors, daily jobs", built: false },
  { name: "5. Integrations and communications", detail: "Simpro, Navixy, SmartFill, Microsoft 365, TextMagic, outbox, safe mode", built: false },
  { name: "6. Fleet, service and fuel", detail: "Fleet register, service schedules, weekly diesel rate, fuel reports, anomalies", built: false },
  { name: "7. Leave, timesheets and pay (test-only)", detail: "Approvals, weekly workbench, reports, payslips, ABA export", built: false },
  { name: "8. Phone app", detail: "Home, My Work, forms, QR scanning, offline queue, preview", built: false },
  { name: "9. Backups, system and help", detail: "Snapshots, NAS agent, restore rehearsal, diagnostics, manuals", built: false },
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
