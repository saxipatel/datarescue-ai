import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import { Layout } from "@/components/Layout";
import { Skeleton } from "@/components/ui";
import { useAuth } from "@/hooks/useAuth";
import { AuditPage } from "@/pages/AuditPage";
import { BatchDetailPage } from "@/pages/BatchDetailPage";
import { BatchesPage } from "@/pages/BatchesPage";
import { FailureLabPage } from "@/pages/FailureLabPage";
import { IncidentDetailPage } from "@/pages/IncidentDetailPage";
import { IncidentsPage } from "@/pages/IncidentsPage";
import { LineagePage } from "@/pages/LineagePage";
import { LoginPage } from "@/pages/LoginPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { QuarantinePage } from "@/pages/QuarantinePage";
import { RecoveryPage } from "@/pages/RecoveryPage";
import { RulesPage } from "@/pages/RulesPage";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading)
    return (
      <div className="p-8">
        <Skeleton rows={4} />
      </div>
    );
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<OverviewPage />} />
        <Route path="batches" element={<BatchesPage />} />
        <Route path="batches/:id" element={<BatchDetailPage />} />
        <Route path="incidents" element={<IncidentsPage />} />
        <Route path="incidents/:id" element={<IncidentDetailPage />} />
        <Route path="quarantine" element={<QuarantinePage />} />
        <Route path="recovery" element={<RecoveryPage />} />
        <Route path="lineage" element={<LineagePage />} />
        <Route path="failure-lab" element={<FailureLabPage />} />
        <Route path="rules" element={<RulesPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
