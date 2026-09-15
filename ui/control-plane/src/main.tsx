import '@patternfly/react-core/dist/styles/base.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';

import App from './App';
import { AdminSessionProvider } from './auth/AdminSessionContext';
import './styles.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: false,
    },
    mutations: {
      retry: false,
    },
  },
});

const root = document.getElementById('root');
if (root === null) {
  throw new Error('Control Plane root element is missing');
}

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <AdminSessionProvider>
        <BrowserRouter basename="/admin/console">
          <App />
        </BrowserRouter>
      </AdminSessionProvider>
    </QueryClientProvider>
  </StrictMode>,
);
