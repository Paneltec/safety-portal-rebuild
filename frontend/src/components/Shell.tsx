import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { Bell } from "./Bell";

interface NavItem {
  to: string;
  label: string;
  show: boolean;
}

export function Shell() {
  const { me, can, signOut, logoUrl } = useAuth();
  const [open, setOpen] = useState(false);
  if (!me) return null;

  const isAdmin = me.role_id === "admin";
  // Only screens that exist are listed, so no menu item leads to a blank page.
  const groups: { label: string; items: NavItem[] }[] = [
    { label: "Overview", items: [{ to: "/app/home", label: "Home", show: true }, { to: "/app/my-work", label: "My work", show: true }, { to: "/app/my-leave", label: "My leave", show: true }] },
    {
      label: "Capture",
      items: [
        { to: "/app/r/swms", label: "SWMS", show: can("swms", "view") },
        { to: "/app/r/pre-starts", label: "Pre-starts", show: can("pre_starts", "view") },
        { to: "/app/r/site-diary", label: "Site diary", show: can("site_diary", "view") },
        { to: "/app/r/incidents", label: "Incidents & hazards", show: can("incidents", "view") || can("hazards", "view") },
        { to: "/app/r/inspections", label: "Inspections", show: can("inspections", "view") },
        { to: "/app/r/risk-assessments", label: "Risk assessments", show: can("risk_assessments", "view") },
        { to: "/app/r/ssra", label: "SSRA", show: can("risk_assessments", "view") },
        { to: "/app/forms", label: "Forms", show: can("forms", "view") },
      ],
    },
    {
      label: "Compliance",
      items: [
        { to: "/app/workers", label: can("workers", "team_view") ? "Workers" : "My worker record", show: can("workers", "view") },
        { to: "/app/certifications", label: "Certifications", show: can("certifications", "view") },
        { to: "/app/inductions", label: "Inductions & training", show: can("inductions", "view") },
        { to: "/app/daily-jobs", label: "Daily jobs", show: isAdmin },
        { to: "/app/leave", label: "Leave", show: can("workers", "team_view") },
        { to: "/app/suppliers", label: can("suppliers", "view") ? "Suppliers" : "My company", show: can("suppliers", "view") || can("contractors", "view") },
        { to: "/app/renewals", label: "Renewals", show: can("renewals", "view") },
        { to: "/app/fleet", label: "Fleet and equipment", show: can("assets", "view") },
        { to: "/app/fuel", label: "Fuel", show: can("assets", "view") },
        { to: "/app/sites", label: "Sites", show: can("sites", "view") },
        { to: "/app/visitors", label: "Visitors", show: can("sites_visitors", "view") },
        { to: "/app/documents", label: "Document library", show: can("documents", "view") },
      ],
    },
    {
      label: "Settings",
      items: [
        { to: "/app/settings/organisation", label: "Organisation", show: true },
        { to: "/app/settings/users", label: "Users", show: can("users", "view") },
        { to: "/app/settings/roles", label: "Roles & permissions", show: can("users", "view") },
        { to: "/app/settings/connections", label: "Connections", show: can("integrations", "view") },
        { to: "/app/settings/outbox", label: "Outbox", show: can("notifications", "view") || can("users", "edit") || can("renewals", "edit") },
        { to: "/app/settings/sessions", label: "Active sessions", show: isAdmin },
        { to: "/app/profile", label: "My profile", show: true },
      ],
    },
  ];

  return (
    <div className="shell">
      <nav className={`sidebar${open ? " open" : ""}`} aria-label="Main menu">
        <div className="brand">
          {logoUrl ? <img src={logoUrl} alt="" className="brand-img" /> : <div className="brand-logo" aria-hidden="true">LOGO</div>}
          <div>
            <div className="brand-name">{me.org_name}</div>
            <div className="brand-sub">Safety Portal</div>
          </div>
          <button type="button" className="btn on-ink small menu-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
            Menu
          </button>
        </div>
        {groups.map((group) => {
          const items = group.items.filter((item) => item.show);
          if (items.length === 0) return null;
          return (
            <div className="nav-group" key={group.label}>
              <div className="nav-label">{group.label}</div>
              {items.map((item) => (
                <NavLink key={item.to} to={item.to} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`} onClick={() => setOpen(false)}>
                  {item.label}
                </NavLink>
              ))}
            </div>
          );
        })}
        <div className="nav-note">Leave, timesheets and pay arrive in later build stages. See Home for progress.</div>
      </nav>
      <div className="content">
        <header className="topbar">
          <div className="topbar-user">
            <span style={{ fontWeight: 600 }}>{me.name}</span>
            <span className="small muted">
              {me.role_name} · {me.org_name}
            </span>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <Bell />
            <button type="button" className="btn" onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        </header>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
