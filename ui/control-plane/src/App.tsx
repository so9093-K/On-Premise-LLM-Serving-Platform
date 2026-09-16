import { useState, type FormEvent, type ReactNode } from 'react';
import { Alert, Button, Card, CardBody, CardTitle, Label, Spinner } from '@patternfly/react-core';
import { useQuery } from '@tanstack/react-query';
import { Navigate, NavLink, Route, Routes } from 'react-router-dom';

import { ApiError, fetchBootstrap, type BootstrapResponse, verifyAdminToken } from './api';
import { useAdminSession } from './auth/AdminSessionContext';
import { ConfigurationPage } from './ConfigurationPage';
import { MainModelPage } from './MainModelPage';
import { OperationsPage } from './OperationsPage';
import { RuntimePage } from './RuntimePage';

const SUPPORTED_BOOTSTRAP_VERSION = 1;

type Section = {
  path: string;
  label: string;
  enabled: (bootstrap: BootstrapResponse) => boolean;
};

const SECTIONS: Section[] = [
  { path: '/', label: 'Overview', enabled: () => true },
  {
    path: '/runtimes',
    label: 'Runtimes',
    enabled: (bootstrap) => bootstrap.deployment.features.includes('runtime_control'),
  },
  {
    path: '/main-model',
    label: 'Main Model',
    enabled: (bootstrap) => bootstrap.deployment.features.includes('model_switching'),
  },
  { path: '/configuration', label: 'Configuration', enabled: () => true },
  { path: '/operations', label: 'Operations', enabled: () => true },
  { path: '/history', label: 'History', enabled: () => true },
];

function LoadingScreen() {
  return (
    <main className="centered-state" aria-live="polite">
      <Spinner aria-label="Control Plane bootstrap loading" />
      <p>Control Plane 정보를 불러오는 중입니다.</p>
    </main>
  );
}

function FailureScreen({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="centered-state">
      <Alert isInline variant="danger" title={title}>
        {children}
      </Alert>
    </main>
  );
}

function AuthGate() {
  const { setToken } = useAdminSession();
  const [draft, setDraft] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = draft.trim();
    if (!token) {
      setError('Admin key를 입력하세요.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await verifyAdminToken(token);
      setToken(token);
      setDraft('');
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) {
        setError('Admin key가 유효하지 않습니다.');
      } else {
        setError(reason instanceof Error ? reason.message : 'Admin 인증 확인에 실패했습니다.');
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-shell">
      <Card className="auth-card">
        <CardTitle>Admin 인증</CardTitle>
        <CardBody>
          <p>
            이 Access Profile은 Admin Bearer 인증이 필요합니다. Key는 이 브라우저 탭의 메모리에만
            유지되며 새로고침하면 사라집니다.
          </p>
          <form className="auth-form" onSubmit={submit}>
            <label htmlFor="admin-key">Admin key</label>
            <input
              id="admin-key"
              type="password"
              autoComplete="off"
              value={draft}
              onChange={(event) => setDraft(event.currentTarget.value)}
            />
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            <Button type="submit" variant="primary" isDisabled={submitting}>
              {submitting ? '확인 중…' : '확인'}
            </Button>
          </form>
        </CardBody>
      </Card>
    </main>
  );
}

function Overview({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const release = bootstrap.platform.release_id ?? 'development';
  return (
    <section className="page-grid">
      <Card>
        <CardTitle>Platform</CardTitle>
        <CardBody>
          <dl className="facts">
            <dt>Version</dt><dd>{bootstrap.platform.version}</dd>
            <dt>Release</dt><dd>{release}</dd>
            <dt>Deployment</dt><dd>{bootstrap.deployment.display_name}</dd>
            <dt>Target</dt><dd>{bootstrap.deployment.target}</dd>
          </dl>
        </CardBody>
      </Card>
      <Card>
        <CardTitle>Access posture</CardTitle>
        <CardBody>
          <dl className="facts">
            <dt>Profile</dt><dd>{bootstrap.access.profile}</dd>
            <dt>Admin auth</dt><dd>{bootstrap.access.admin_auth_required ? 'Required' : 'Not required'}</dd>
            <dt>Configuration revision</dt><dd>{bootstrap.configuration.revision}</dd>
            <dt>Configuration write</dt>
            <dd>{bootstrap.configuration.write_available ? 'Available' : 'Unavailable'}</dd>
          </dl>
        </CardBody>
      </Card>
      <Card>
        <CardTitle>Capabilities</CardTitle>
        <CardBody className="capability-list">
          {bootstrap.deployment.features.map((feature) => <Label key={feature}>{feature}</Label>)}
        </CardBody>
      </Card>
    </section>
  );
}

function Placeholder({ title }: { title: string }) {
  return (
    <Card>
      <CardTitle>{title}</CardTitle>
      <CardBody>
        <p>이 화면의 operation UX는 backend 계약을 그대로 소비하는 다음 기능 단계에서 연결합니다.</p>
      </CardBody>
    </Card>
  );
}

function Shell({ bootstrap }: { bootstrap: BootstrapResponse }) {
  const { token, clearToken } = useAdminSession();
  const sections = SECTIONS.filter((section) => section.enabled(bootstrap));
  const externalLinks = [
    ['API Docs', bootstrap.links.docs],
    ['Grafana', bootstrap.links.grafana],
  ] as const;

  return (
    <div className="console-shell">
      <header className="topbar">
        <div>
          <strong>AI Model Serving Control Plane</strong>
          <span>{bootstrap.deployment.display_name}</span>
        </div>
        {bootstrap.access.admin_auth_required ? (
          <Button variant="secondary" onClick={clearToken}>Admin key 지우기</Button>
        ) : null}
      </header>
      <div className="workspace">
        <aside className="sidebar" aria-label="Control Plane navigation">
          <nav>
            {sections.map((section) => (
              <NavLink key={section.path} to={section.path} end={section.path === '/'}>
                {section.label}
              </NavLink>
            ))}
          </nav>
          <div className="external-links">
            {externalLinks.map(([label, href]) => href ? (
              <a key={label} href={href} target="_blank" rel="noreferrer">{label} ↗</a>
            ) : null)}
          </div>
        </aside>
        <main className="content">
          <Routes>
            <Route path="/" element={<Overview bootstrap={bootstrap} />} />
            <Route path="/runtimes" element={<RuntimePage token={token} onUnauthorized={clearToken} />} />
            <Route path="/main-model" element={<MainModelPage token={token} onUnauthorized={clearToken} />} />
            <Route path="/configuration" element={<ConfigurationPage token={token} onUnauthorized={clearToken} deploymentFeatures={bootstrap.deployment.features} />} />
            <Route path="/operations" element={<OperationsPage token={token} onUnauthorized={clearToken} deploymentFeatures={bootstrap.deployment.features} />} />
            <Route path="/history" element={<Placeholder title="History" />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const { token } = useAdminSession();
  const bootstrapQuery = useQuery({
    queryKey: ['control-plane-bootstrap'],
    queryFn: fetchBootstrap,
    retry: false,
    staleTime: 30_000,
  });

  if (bootstrapQuery.isPending) {
    return <LoadingScreen />;
  }
  if (bootstrapQuery.isError) {
    return <FailureScreen title="Control Plane bootstrap을 불러오지 못했습니다.">{bootstrapQuery.error.message}</FailureScreen>;
  }

  const bootstrap = bootstrapQuery.data;
  if (bootstrap.bootstrap_version !== SUPPORTED_BOOTSTRAP_VERSION) {
    return (
      <FailureScreen title="지원하지 않는 Control Plane 계약입니다.">
        현재 Console은 bootstrap version {SUPPORTED_BOOTSTRAP_VERSION}만 지원합니다. 안전을 위해 모든 operation을 잠급니다.
      </FailureScreen>
    );
  }
  if (bootstrap.access.admin_auth_required && token === null) {
    return <AuthGate />;
  }
  return <Shell bootstrap={bootstrap} />;
}
