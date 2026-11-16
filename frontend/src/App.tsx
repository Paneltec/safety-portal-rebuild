import { AssetDetail, AssetScan, Fleet } from "./pages/Fleet";
import { ServiceDue, ServicePanel } from "./pages/Service";
import { Fuel } from "./pages/Fuel";
import { Leave, MyLeave } from "./pages/Leave";
import { MyTimesheet, Timesheets } from "./pages/Timesheets";
import { MyPayslips, PayProfiles, PayRules, PayRunPage, PayRuns } from "./pages/Pay";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import { Shell } from "./components/Shell";
import { Loading } from "./components/ui";
import { ForgotPassword, Login, RedeemLink, Setup } from "./pages/SignIn";
import { OrganisationSettings } from "./pages/Organisation";
import { Documents } from "./pages/Documents";
import { FormEditor } from "./pages/FormEditor";
import { FormSubmissions, Forms } from "./pages/Forms";
import { SubmissionPage } from "./pages/Submission";
import { Home } from "./pages/Home";
import { Profile } from "./pages/Profile";
import { RegisterList, RegisterNew, RegisterRecord } from "./pages/Register";
import { Roles } from "./pages/Roles";
import { Sessions } from "./pages/Sessions";
import { UserDetail } from "./pages/UserDetail";
import { Users } from "./pages/Users";
import { Certifications } from "./pages/Certifications";
import { WorkerDetail } from "./pages/WorkerDetail";
import { Workers } from "./pages/Workers";
import { SiteDetail, Sites } from "./pages/Sites";
import { PublicSignOn, StaffSignOn } from "./pages/SiteSignOn";
import { Visitors } from "./pages/Visitors";
import { Connections } from "./pages/Connections";
import { Inductions } from "./pages/Inductions";
import { Outbox } from "./pages/Outbox";
import { DailyJobs, MyWork } from "./pages/Jobs";
import { Renew } from "./pages/Renew";
import { Renewals } from "./pages/Renewals";
import { SupplierDetail, Suppliers } from "./pages/Suppliers";

function Protected() {
  const { status, me } = useAuth();
  const location = useLocation();
  if (status === "loading") return <Loading label="Checking your sign-in…" />;
  if (status === "anonymous" || !me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  // A required password change blocks every other screen (the server enforces this too).
  if (me.must_change_password && location.pathname !== "/app/profile") return <Navigate to="/app/profile" replace />;
  return <Shell />;
}

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/app/home" replace />} />
      <Route path="/login" element={<Login />} />
      <Route path="/setup" element={<Setup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/accept-invite" element={<RedeemLink kind="invite" />} />
      <Route path="/reset-password" element={<RedeemLink kind="reset" />} />
      <Route path="/sign-on/:token" element={<PublicSignOn />} />
      <Route path="/renew/:token" element={<Renew />} />
      <Route path="/app" element={<Protected />}>
        <Route index element={<Navigate to="/app/home" replace />} />
        <Route path="home" element={<Home />} />
        <Route path="profile" element={<Profile />} />
        <Route path="r/:register" element={<RegisterList />} />
        <Route path="r/:register/new" element={<RegisterNew />} />
        <Route path="r/:register/:recordId" element={<RegisterRecord />} />
        <Route path="forms" element={<Forms />} />
        <Route path="forms/templates/:templateId/edit" element={<FormEditor />} />
        <Route path="forms/templates/:templateId/submissions" element={<FormSubmissions />} />
        <Route path="forms/submissions/:submissionId" element={<SubmissionPage />} />
        <Route path="workers" element={<Workers />} />
        <Route path="workers/:workerId" element={<WorkerDetail />} />
        <Route path="certifications" element={<Certifications />} />
        <Route path="sites" element={<Sites />} />
        <Route path="sites/:siteId" element={<SiteDetail />} />
        <Route path="visitors" element={<Visitors />} />
        <Route path="suppliers" element={<Suppliers />} />
        <Route path="suppliers/:supplierId" element={<SupplierDetail />} />
        <Route path="renewals" element={<Renewals />} />
        <Route path="inductions" element={<Inductions />} />
        <Route path="daily-jobs" element={<DailyJobs />} />
        <Route path="my-work" element={<MyWork />} />
        <Route path="site-sign-on/:token" element={<StaffSignOn />} />
        <Route path="fleet" element={<Fleet />} />
        <Route path="fleet/service" element={<ServiceDue />} />
        <Route path="fleet/:assetId" element={<AssetDetail extra={(asset, reload) => (asset.kind === "equipment" ? null : <ServicePanel asset={asset} reload={reload} />)} />} />
        <Route path="scan/asset/:token" element={<AssetScan />} />
        <Route path="fuel" element={<Fuel />} />
        <Route path="leave" element={<Leave />} />
        <Route path="my-leave" element={<MyLeave />} />
        <Route path="my-timesheet" element={<MyTimesheet />} />
        <Route path="pay/timesheets" element={<Timesheets />} />
        <Route path="pay/runs" element={<PayRuns />} />
        <Route path="pay/runs/:runId" element={<PayRunPage />} />
        <Route path="pay/rules" element={<PayRules />} />
        <Route path="pay/employees" element={<PayProfiles />} />
        <Route path="my-payslips" element={<MyPayslips />} />
        <Route path="documents" element={<Documents />} />
        <Route path="documents/:folderId" element={<Documents />} />
        <Route path="settings/organisation" element={<OrganisationSettings />} />
        <Route path="settings/users" element={<Users />} />
        <Route path="settings/users/:userId" element={<UserDetail />} />
        <Route path="settings/roles" element={<Roles />} />
        <Route path="settings/connections" element={<Connections />} />
        <Route path="settings/outbox" element={<Outbox />} />
        <Route path="settings/sessions" element={<Sessions />} />
        <Route path="*" element={<Navigate to="/app/home" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
