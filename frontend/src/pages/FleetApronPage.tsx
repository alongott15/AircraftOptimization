import { Button, Group, Stack, Text, Title } from '@mantine/core';
import { IconTable } from '@tabler/icons-react';
import { lazy, Suspense } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { useAircraftList, useReference } from '../api/hooks';
import { ErrorState, LoadingState } from '../components/common';
import { hasWebGL } from '../features/aircraft3d/webgl';
import { t } from '../i18n';
import { FleetPage } from './FleetPage';

const FleetApron = lazy(() => import('../features/aircraft3d/FleetApron'));

export default function FleetApronPage() {
  const list = useAircraftList({ page_size: 200, sort: 'tail_number' });
  const reference = useReference();
  const navigate = useNavigate();
  if (!hasWebGL()) return <FleetPage />; // the table carries the same information
  if (list.isPending || reference.isPending) return <LoadingState />;
  if (list.isError) return <ErrorState error={list.error} />;
  if (reference.isError) return <ErrorState error={reference.error} />;
  const shapes = Object.fromEntries(reference.data.aircraft_types.map((x) => [String(x.type_code), String(x.model_shape)]));
  const bases = reference.data.bases.map((b) => ({ base_code: String(b.base_code), name_he: String(b.name_he) }));
  return (
    <Stack mt="md">
      <Group justify="space-between">
        <Title order={2}>{t('fleet3d.title')}</Title>
        <Button component={Link} to="/fleet" variant="default" leftSection={<IconTable size={16} />}>
          {t('action.viewTable')}
        </Button>
      </Group>
      <Text c="dimmed" size="sm">{t('fleet3d.hint')}</Text>
      <Suspense fallback={<LoadingState />}>
        <FleetApron aircraft={list.data.items} shapes={shapes} bases={bases} onOpen={(tail) => navigate(`/aircraft/${tail}`)} />
      </Suspense>
    </Stack>
  );
}
