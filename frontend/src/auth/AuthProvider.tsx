import { createContext, useContext, type ReactNode } from 'react';

import { useMe } from '../api/hooks';
import type { User } from '../api/types';

export type Permission = 'VIEW' | 'PLAN' | 'WHAT_IF' | 'AUDIT' | 'ADMIN';

const ROLE_PERMISSIONS: Record<string, Permission[]> = {
  VIEWER: ['VIEW'],
  PLANNER: ['VIEW', 'PLAN', 'WHAT_IF'],
  MAINTENANCE_CONTROLLER: ['VIEW', 'WHAT_IF', 'AUDIT'],
  ADMIN: ['VIEW', 'PLAN', 'WHAT_IF', 'AUDIT', 'ADMIN'],
};

interface AuthState {
  user: User | null;
  loading: boolean;
  can: (p: Permission) => boolean;
}

const AuthContext = createContext<AuthState>({ user: null, loading: true, can: () => false });

export function AuthProvider({ children }: { children: ReactNode }) {
  const me = useMe();
  const user = me.data ?? null;
  const can = (p: Permission) => Boolean(user && ROLE_PERMISSIONS[user.role]?.includes(p));
  return <AuthContext.Provider value={{ user, loading: me.isPending, can }}>{children}</AuthContext.Provider>;
}

// UI hides controls only; the server enforces every permission (SECURITY.md §3.3).
export function useAuth(): AuthState {
  return useContext(AuthContext);
}
