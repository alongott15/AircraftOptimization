import {
  ActionIcon,
  AppShell,
  Burger,
  Center,
  Group,
  Menu,
  NavLink,
  Text,
  Title,
  Tooltip,
  useComputedColorScheme,
  useMantineColorScheme,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconCalendarStats,
  IconHistory,
  IconLayoutDashboard,
  IconListDetails,
  IconLogout,
  IconMoon,
  IconPlane,
  IconRoute,
  IconSettings,
  IconSun,
  IconUser,
} from '@tabler/icons-react';
import { lazy, Suspense } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { Link } from 'react-router-dom';

import { useFleetSummary, useLogout } from './api/hooks';
import { useAuth, type Permission } from './auth/AuthProvider';
import { LogoMark } from './components/Logo';
import { AlertBanner, DataFreshnessIndicator, LoadingState, ServerNowProvider } from './components/common';
import { t, tk, type MessageKey } from './i18n';
import { DashboardPage } from './pages/DashboardPage';
import { FleetPage } from './pages/FleetPage';
import { LoginPage } from './pages/LoginPage';
import { MissionFormPage } from './pages/MissionFormPage';
import { MissionPage } from './pages/MissionPage';
import { MissionsPage } from './pages/MissionsPage';

const AircraftPage = lazy(() => import('./pages/AircraftPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const AuditPage = lazy(() => import('./pages/AuditPage'));
const PlanningPage = lazy(() => import('./pages/PlanningPage'));
const FleetApronPage = lazy(() => import('./pages/FleetApronPage'));

const NAV: { to: string; label: MessageKey; icon: typeof IconPlane; perm: Permission }[] = [
  { to: '/', label: 'nav.dashboard', icon: IconLayoutDashboard, perm: 'VIEW' },
  { to: '/fleet', label: 'nav.fleet', icon: IconPlane, perm: 'VIEW' },
  { to: '/missions/new', label: 'nav.missionNew', icon: IconRoute, perm: 'PLAN' },
  { to: '/planning', label: 'nav.planning', icon: IconCalendarStats, perm: 'PLAN' },
  { to: '/missions', label: 'nav.missions', icon: IconListDetails, perm: 'VIEW' },
  { to: '/settings', label: 'nav.settings', icon: IconSettings, perm: 'VIEW' },
  { to: '/audit', label: 'nav.audit', icon: IconHistory, perm: 'AUDIT' },
];

function ThemeToggle() {
  const { setColorScheme } = useMantineColorScheme();
  const computed = useComputedColorScheme('light');
  return (
    <Tooltip label={t('action.toggleTheme')}>
      <ActionIcon
        variant="subtle"
        aria-label={t('action.toggleTheme')}
        onClick={() => setColorScheme(computed === 'dark' ? 'light' : 'dark')}
      >
        {computed === 'dark' ? <IconSun size={18} /> : <IconMoon size={18} />}
      </ActionIcon>
    </Tooltip>
  );
}

function Shell() {
  const [opened, { toggle, close }] = useDisclosure();
  const { user, can } = useAuth();
  const summary = useFleetSummary();
  const logout = useLogout();
  const location = useLocation();
  const isActive = (to: string) =>
    to === '/' ? location.pathname === '/' : location.pathname === to || (to !== '/missions' && to !== '/fleet' && location.pathname.startsWith(to))
      || (to === '/fleet' && location.pathname.startsWith('/fleet'))
      || (to === '/missions' && location.pathname.startsWith('/missions/') && !location.pathname.startsWith('/missions/new'));

  return (
    <ServerNowProvider value={summary.data?.as_of ?? undefined}>
      <AppShell
        header={{ height: 56 }}
        navbar={{ width: 240, breakpoint: 'sm', collapsed: { mobile: !opened } }}
        padding="md"
      >
        <AppShell.Header>
          <Group h="100%" px="md" justify="space-between" wrap="nowrap">
            <Group gap="sm" wrap="nowrap">
              <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" aria-label={t('nav.menu')} />
              <LogoMark size={30} />
              <Title order={4}>{t('app.title')}</Title>
              <Text size="sm" c="dimmed" visibleFrom="sm">{t('app.tagline')}</Text>
            </Group>
            <Group gap="md" wrap="nowrap">
              <DataFreshnessIndicator />
              <ThemeToggle />
              <Menu position="bottom-end" withinPortal>
                <Menu.Target>
                  <ActionIcon variant="subtle" aria-label={user?.display_name ?? ''}>
                    <IconUser size={18} />
                  </ActionIcon>
                </Menu.Target>
                <Menu.Dropdown>
                  <Menu.Label>
                    {user?.display_name} · {tk(`role.${user?.role ?? ''}`)}
                  </Menu.Label>
                  <Menu.Item leftSection={<IconLogout size={16} />} onClick={() => logout.mutate()}>
                    {t('action.logout')}
                  </Menu.Item>
                </Menu.Dropdown>
              </Menu>
            </Group>
          </Group>
        </AppShell.Header>
        <AppShell.Navbar p="xs">
          {NAV.filter((n) => can(n.perm)).map((n) => (
            <NavLink
              key={n.to}
              component={Link}
              to={n.to}
              label={t(n.label)}
              leftSection={<n.icon size={18} />}
              active={isActive(n.to)}
              onClick={close}
            />
          ))}
          <Text size="xs" c="dimmed" mt="auto" p="xs">
            {t('app.subtitle')}
          </Text>
        </AppShell.Navbar>
        <AppShell.Main>
          <AlertBanner count={summary.data?.aircraft_with_expired_items ?? 0} />
          <Suspense fallback={<LoadingState />}>
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/fleet" element={<FleetPage />} />
              <Route path="/fleet/3d" element={<FleetApronPage />} />
              <Route path="/aircraft/:tail" element={<AircraftPage />} />
              <Route path="/missions" element={<MissionsPage />} />
              <Route path="/missions/new" element={can('PLAN') ? <MissionFormPage /> : <Navigate to="/" />} />
              <Route path="/missions/:id" element={<MissionPage />} />
              <Route path="/missions/:id/edit" element={can('PLAN') ? <MissionFormPage /> : <Navigate to="/" />} />
              <Route path="/planning" element={can('PLAN') ? <PlanningPage /> : <Navigate to="/" />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/audit" element={can('AUDIT') ? <AuditPage /> : <Navigate to="/" />} />
              <Route path="*" element={<Navigate to="/" />} />
            </Routes>
          </Suspense>
        </AppShell.Main>
      </AppShell>
    </ServerNowProvider>
  );
}

export function App() {
  const { user, loading } = useAuth();
  if (loading) {
    return (
      <Center h="100vh">
        <LoadingState />
      </Center>
    );
  }
  return user ? <Shell /> : <LoginPage />;
}
