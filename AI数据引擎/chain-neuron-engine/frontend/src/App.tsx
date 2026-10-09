import { Spin } from "antd";
import {
  Navigate,
  Route,
  BrowserRouter,
  Routes,
  useLocation,
} from "react-router-dom";

import { useAuth } from "./auth/AuthContext";
import AppLayout from "./layout/AppLayout";
import ChatPage from "./pages/Chat";
import Datasets from "./pages/Datasets";
import DatasetWizard from "./pages/DatasetWizard";
import Datasources from "./pages/Datasources";
import ImportPage from "./pages/Import";
import RecordsPage from "./pages/Records";
import ReinitializePage from "./pages/Reinitialize";
import SystemParamsPage from "./pages/SystemParams";
import Login from "./pages/Login";
import ModelConfigPage from "./pages/ModelConfig";
import FoundationFeaturesPage from "./pages/FoundationFeatures";
import TopologyPage from "./pages/Topology";
import RuntimeProfilesPage from "./pages/RuntimeProfiles";

function Protected({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div style={{ textAlign: "center", marginTop: 120 }}>
        <Spin size="large" />
      </div>
    );
  }
  if (!user) {
    return (
      <Navigate to="/login" state={{ from: location.pathname }} replace />
    );
  }
  return children;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          element={
            <Protected>
              <AppLayout />
            </Protected>
          }
        >
          <Route path="/agent" element={<ChatPage />} />
          <Route path="/agent/topology" element={<TopologyPage />} />
          <Route path="/chat" element={<Navigate to="/agent" replace />} />
          <Route path="/records" element={<RecordsPage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/rules" element={<Navigate to="/foundation-features?tab=time-rules" replace />} />
          <Route path="/system-params" element={<SystemParamsPage />} />
          <Route path="/runtime-profiles" element={<RuntimeProfilesPage />} />
          <Route path="/datasources" element={<Datasources />} />
          <Route path="/datasets" element={<Datasets />} />
          <Route path="/foundation-features" element={<FoundationFeaturesPage />} />
          <Route path="/datasets/new" element={<DatasetWizard />} />
          <Route path="/reinitialize" element={<ReinitializePage />} />
          <Route path="/model-config" element={<ModelConfigPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/agent" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
