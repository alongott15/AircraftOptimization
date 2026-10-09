import { Button, Card, Group, Pagination, Stack, Table, Text, Title } from '@mantine/core';
import { IconPlus } from '@tabler/icons-react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { useMissions } from '../api/hooks';
import { useAuth } from '../auth/AuthProvider';
import { EmptyState, ErrorState, LoadingState, Ltr } from '../components/common';
import { ToneBadge } from '../components/StatusBadge';
import { formatDateTime, formatHours } from '../i18n/format';
import { t, tk } from '../i18n';

const PAGE_SIZE = 25;

export function MissionsPage() {
  const [page, setPage] = useState(1);
  const missions = useMissions({ page, page_size: PAGE_SIZE });
  const navigate = useNavigate();
  const { can } = useAuth();

  return (
    <Stack mt="md">
      <Group justify="space-between">
        <Title order={2}>{t('mission.listTitle')}</Title>
        {can('PLAN') && (
          <Button component={Link} to="/missions/new" leftSection={<IconPlus size={16} />}>{t('action.newMission')}</Button>
        )}
      </Group>
      {missions.isPending ? <LoadingState /> : missions.isError ? <ErrorState error={missions.error} /> : (
        <Card p={0}>
          {missions.data.items.length === 0 ? <EmptyState /> : (
            <Table highlightOnHover verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t('field.name')}</Table.Th>
                  <Table.Th>{t('field.missionType')}</Table.Th>
                  <Table.Th>{t('field.startAt')}</Table.Th>
                  <Table.Th>{t('field.duration')}</Table.Th>
                  <Table.Th>{t('field.requiredCount')}</Table.Th>
                  <Table.Th>{t('field.status')}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {missions.data.items.map((m) => (
                  <Table.Tr key={m.mission_id} style={{ cursor: 'pointer' }} onClick={() => navigate(`/missions/${m.mission_id}`)}>
                    <Table.Td fw={600}>{m.name}</Table.Td>
                    <Table.Td>{tk(`missionType.${m.mission_type}`)}</Table.Td>
                    <Table.Td className="ao-num">{formatDateTime(m.start_at)}</Table.Td>
                    <Table.Td className="ao-num"><Ltr>{formatHours(m.est_duration_hours)}</Ltr></Table.Td>
                    <Table.Td className="ao-num">{m.required_aircraft_count}</Table.Td>
                    <Table.Td>
                      <ToneBadge tone={m.status === 'ASSIGNED' ? 'OK' : m.status === 'CANCELLED' ? 'UNKNOWN' : 'INFO'} size="sm">
                        {tk(`missionStatus.${m.status}`)}
                      </ToneBadge>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Card>
      )}
      {missions.data && missions.data.total > PAGE_SIZE && (
        <Group justify="center">
          <Pagination value={page} onChange={setPage} total={Math.ceil(missions.data.total / PAGE_SIZE)} />
          <Text size="sm" c="dimmed">{t('common.total')}: {missions.data.total}</Text>
        </Group>
      )}
    </Stack>
  );
}
