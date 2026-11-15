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
        <Route path="documents" element={<Documents />} />
        <Route path="documents/:folderId" element={<Documents />} />
        <Route path="settings/organisation" element={<OrganisationSettings />} />
        <Route path="settings/users" element={<Users />} />
        <Route path="settings/users/:userId" element={<UserDetail />} />
        <Route path="settings/roles" element={<Roles />} />
        <Route path="settings/sessions" element={<Sessions />} />
        <Route path="*" element={<Navigate to="/app/home" replace />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
