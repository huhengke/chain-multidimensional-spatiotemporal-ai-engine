import { SaveOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Descriptions, Input, Select, Space, Switch, Tag, message } from "antd";
import { useEffect, useState } from "react";

import { errorMessage } from "../api/client";
import { fetchModelConfig, updateModelConfig } from "../api/endpoints";
import type { ModelConfig } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export default function ModelConfigPage() {
  const { user } = useAuth();
  const [config, setConfig] = useState<ModelConfig | null>(null);
  const [active, setActive] = useState("");
  const [baseUrls, setBaseUrls] = useState<Record<string, string>>({});
  const [models, setModels] = useState<Record<string, string>>({});
  const [supportsImages, setSupportsImages] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const next = await fetchModelConfig();
      setConfig(next);
      setActive(next.active);
      setBaseUrls(Object.fromEntries(next.providers.map((p) => [p.id, String(p.base_url || "")] )));
      setModels(Object.fromEntries(next.providers.map((p) => [p.id, String(p.model || "")] )));
      setSupportsImages(Object.fromEntries(next.providers.map((p) => [p.id, Boolean(p.supports_images)])));
    } catch (e) {
      message.error(errorMessage(e));
    }
  };

  useEffect(() => { void load(); }, []);

  const save = async () => {
    setSaving(true);
    try {
      const next = await updateModelConfig({
        active,
        providers: Object.fromEntries((config?.providers || []).map((p) => [p.id, {
          base_url: baseUrls[p.id], model: models[p.id], supports_images: Boolean(supportsImages[p.id]),
        }])),
      });
      setConfig(next);
      message.success("模型运行配置已保存；密钥仍由环境变量或本机凭据提供");
    } catch (e) {
      message.error(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  if (user?.role !== "admin") {
    return <Alert type="warning" showIcon message="模型配置需要管理员权限" />;
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="模型配置只管理非敏感运行参数"
        description="API Key 不会回显或写入页面配置；Codex 可读取本机认证文件，其他通道仍从环境变量读取。字段分析不可用时会明确回退为规则建议。"
      />
      <Card title="活动模型通道" extra={<Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void save()}>保存配置</Button>}>
        <Select
          style={{ width: 320 }}
          value={active || undefined}
          options={(config?.providers || []).map((p) => ({ value: p.id, label: `${p.name || p.id}（${p.id}）` }))}
          onChange={setActive}
        />
      </Card>
      {(config?.providers || []).map((provider) => (
        <Card key={provider.id} title={provider.name || provider.id}>
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="通道 id">{provider.id}</Descriptions.Item>
            <Descriptions.Item label="密钥状态">
              {provider.api_key_configured ? <Tag color="green">已配置</Tag> : <Tag>未配置</Tag>}
            </Descriptions.Item>
          </Descriptions>
          <Space direction="vertical" style={{ width: "100%", marginTop: 12 }}>
            <Input addonBefore="Base URL" value={baseUrls[provider.id]} onChange={(e) => setBaseUrls({ ...baseUrls, [provider.id]: e.target.value })} />
            <Input addonBefore="模型" value={models[provider.id]} onChange={(e) => setModels({ ...models, [provider.id]: e.target.value })} />
            <Space>
              <Switch checked={Boolean(supportsImages[provider.id])} onChange={(checked) => setSupportsImages({ ...supportsImages, [provider.id]: checked })} />
              <span>允许图片输入</span>
              <Tag color={supportsImages[provider.id] ? "blue" : "default"}>{supportsImages[provider.id] ? "视觉通道" : "仅文本"}</Tag>
            </Space>
          </Space>
        </Card>
      ))}
    </Space>
  );
}
