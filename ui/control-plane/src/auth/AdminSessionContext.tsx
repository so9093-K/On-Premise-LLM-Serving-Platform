import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useMemo,
  useState,
} from 'react';

interface AdminSessionValue {
  token: string | null;
  setToken: (token: string) => void;
  clearToken: () => void;
}

const AdminSessionContext = createContext<AdminSessionValue | null>(null);

export function AdminSessionProvider({ children }: PropsWithChildren) {
  const [token, setTokenState] = useState<string | null>(null);
  const setToken = useCallback((value: string) => setTokenState(value), []);
  const clearToken = useCallback(() => setTokenState(null), []);
  const value = useMemo(() => ({ token, setToken, clearToken }), [token, setToken, clearToken]);
  return <AdminSessionContext.Provider value={value}>{children}</AdminSessionContext.Provider>;
}

export function useAdminSession(): AdminSessionValue {
  const value = useContext(AdminSessionContext);
  if (value === null) {
    throw new Error('useAdminSession must be used inside AdminSessionProvider');
  }
  return value;
}
