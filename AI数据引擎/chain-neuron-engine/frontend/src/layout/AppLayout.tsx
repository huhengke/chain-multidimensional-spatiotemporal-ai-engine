import {
  ApiOutlined,
  ApartmentOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  MessageOutlined,
  SettingOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import { Avatar, Dropdown, Layout, Menu, Tag, Typography } from "antd";
import type { MenuProps } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

const { Header, Sider, Content } = Layout;
const { Text } = Typography;

const ROLE_LABEL: Record<string, string> = {
  admin: "管理员",
  operator: "运营",
  viewer: "查看者",
};

const ROLE_COLOR: Record<string, string> = {
  admin: "red",
  operator: "blue",
  viewer: "default",
};

export default function AppLayout() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const canManage = user?.role !== "viewer";
  const selectedPath = location.pathname.startsWith("/agent/") ? "/agent" : location.pathname;

  const menuItems: MenuProps["items"] = [
    { key: "/agent", icon: <MessageOutlined />, label: "Agent 工作台" },
    { key: "/records", icon: <DatabaseOutlined />, label: "数据详情" },
    canManage
      ? { key: "/system-params", icon: <SettingOutlined />, label: "系统参数" }
      : null,
    canManage
      ? { key: "/runtime-profiles", icon: <SwapOutlined />, label: "AI 引擎与数据集" }
      : null,
    canManage
      ? { key: "/datasources", icon: <CloudServerOutlined />, label: "数据源" }
      : null,
    canManage
      ? { key: "/datasets", icon: <DatabaseOutlined />, label: "数据集管理" }
      : null,
    canManage
      ? { key: "/foundation-features", icon: <ExperimentOutlined />, label: "基座特征管理" }
      : null,
    user?.role === "admin"
      ? { key: "/model-config", icon: <SettingOutlined />, label: "模型配置" }
      : null,
  ].filter(Boolean) as MenuProps["items"];

  const userMenu: MenuProps["items"] = [
    {
      key: "logout",
      icon: <ApartmentOutlined />,
      label: "退出登录",
      onClick: () => {
        signOut();
        navigate("/login", { replace: true });
      },
    },
  ];

  return (
    <Layout style={{ height: "100vh", minHeight: 0, overflow: "hidden" }}>
      <Sider
        theme="dark"
        width={220}
        breakpoint="lg"
        collapsedWidth={0}
        style={{ height: "100vh", overflow: "hidden" }}
      >
        <div
          style={{
            height: 56,
            margin: 16,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <ApiOutlined style={{ color: "#ffffff", fontSize: 20 }} />
          <Text strong style={{ color: "#fff", fontSize: 15 }}>
            链式神经元
          </Text>
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedPath]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout style={{ minWidth: 0, minHeight: 0, overflow: "hidden" }}>
        <Header
          style={{
            background: "#fff",
            padding: "0 24px",
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: 12,
          }}
        >
          <Tag color={ROLE_COLOR[user?.role || "viewer"]}>
            {ROLE_LABEL[user?.role || "viewer"]}
          </Tag>
          <Dropdown menu={{ items: userMenu }} placement="bottomRight">
            <div style={{ cursor: "pointer", display: "flex", gap: 8 }}>
              <Avatar style={{ backgroundColor: "#1677ff" }}>
                {user?.display_name?.[0] || user?.username?.[0]}
              </Avatar>
              <Text>{user?.display_name || user?.username}</Text>
            </div>
          </Dropdown>
        </Header>
        <Content
          style={{
            margin: 16,
            minWidth: 0,
            minHeight: 0,
            overflowY: "auto",
            overflowX: "hidden",
          }}
        >
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
