import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { App as AntApp, Button, Card, Form, Input, Typography } from "antd";
import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { errorMessage } from "../api/client";
import { useAuth } from "../auth/AuthContext";

const { Title, Text } = Typography;

export default function Login() {
  const { message } = AntApp.useApp();
  const { signIn } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [loading, setLoading] = useState(false);

  const from = (location.state as { from?: string })?.from || "/agent";

  const onFinish = async (values: { username: string; password: string }) => {
    setLoading(true);
    try {
      await signIn(values.username, values.password);
      message.success("登录成功");
      navigate(from, { replace: true });
    } catch (e) {
      message.error(errorMessage(e, "登录失败"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "linear-gradient(135deg,#0b1f3a,#123c6c 60%,#1b5f94)",
      }}
    >
      <Card style={{ width: 380 }} variant="borderless">
        <div style={{ textAlign: "center", marginBottom: 24 }}>
          <Title level={3} style={{ marginBottom: 4 }}>
            链式神经元
          </Title>
          <Text type="secondary">多维时空 AI 数据引擎</Text>
        </div>
        <Form layout="vertical" onFinish={onFinish} autoComplete="off">
          <Form.Item
            name="username"
            rules={[{ required: true, message: "请输入用户名" }]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" size="large" />
          </Form.Item>
          <Form.Item
            name="password"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password
              prefix={<LockOutlined />}
              placeholder="密码"
              size="large"
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 8 }}>
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              block
              loading={loading}
            >
              登录
            </Button>
          </Form.Item>
        </Form>
        <Text type="secondary" style={{ fontSize: 12 }}>
          默认账号：admin/admin123、operator/operator123、viewer/viewer123
        </Text>
      </Card>
    </div>
  );
}
