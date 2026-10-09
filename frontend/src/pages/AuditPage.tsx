import { Card, Code, Group, Pagination, Select, Stack, Table, Text, Title } from '@mantine/core';
import { useState } from 'react';

import { useAudit } from '../api/hooks';
import { ErrorState, LoadingState, Ltr } from '../components/common';
import { formatDateTime } from '../i18n/format';
import { t, tk } from '../i18n';

const PAGE_SIZE = 50;
const ACTIONS = [
  'LOGIN', 'LOGIN_FAILED', 'LOGOUT', 'MISSION_CREATE', 'MISSION_UPDATE', 'MISSION_CANCEL', 'OPTIMIZE', 'ASSIGN',
  'ASSIGN_CANCEL', 'POLICY_UPDATE', 'HANGAR_CAPACITY_UPDATE', 'MODEL_RETRAIN', 'FORCE_INCLUDE_REJECTED', 'HORIZON_PLAN',
  'HORIZON_ASSIGN',
];

export default function AuditPage() {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState<string | null>(null);
  const audit = useAudit({ page, page_size: PAGE_SIZE, action });

  return (
    <Stack mt="md">
      <Group justify="space-between">
        <Title order={2}>{t('audit.title')}</Title>
        <Select placeholder={t('field.action')} clearable value={action} w={220}
          data={ACTIONS.map((a) => ({ value: a, label: tk(`audit.${a}`) }))}
          onChange={(v) => { setAction(v); setPage(1); }} />
      </Group>
      {audit.isPending ? <LoadingState /> : audit.isError ? <ErrorState error={audit.error} /> : (
        <Card p={0}>
          <Table striped verticalSpacing="xs" data-testid="audit-table">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t('field.time')}</Table.Th>
                <Table.Th>{t('field.user')}</Table.Th>
                <Table.Th>{t('field.action')}</Table.Th>
                <Table.Th>{t('field.entity')}</Table.Th>
                <Table.Th>{t('field.details')}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {audit.data.items.map((a) => (
                <Table.Tr key={a.audit_id}>
                  <Table.Td className="ao-num">{formatDateTime(a.at)}</Table.Td>
                  <Table.Td><Ltr>{a.username ?? '—'}</Ltr></Table.Td>
                  <Table.Td data-action={a.action}>{tk(`audit.${a.action}`, undefined, a.action)}</Table.Td>
                  <Table.Td><Ltr>{a.entity_type ? `${a.entity_type}${a.entity_id ? ` #${a.entity_id}` : ''}` : '—'}</Ltr></Table.Td>
                  <Table.Td>
                    {a.details ? <Code block dir="ltr" style={{ maxWidth: 420, whiteSpace: 'pre-wrap' }}>{JSON.stringify(a.details)}</Code> : '—'}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}
      {audit.data && audit.data.total > PAGE_SIZE && (
        <Group justify="center">
          <Pagination value={page} onChange={setPage} total={Math.ceil(audit.data.total / PAGE_SIZE)} />
          <Text size="sm" c="dimmed">{t('common.total')}: {audit.data.total}</Text>
        </Group>
      )}
    </Stack>
  );
}
