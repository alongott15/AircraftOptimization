import { Badge, Button, Card, Group, Popover, Select, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { useState } from 'react';
import { IconCube, IconFilterOff, IconHourglass, IconSearch } from '@tabler/icons-react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';

import { useAircraftList, useReference } from '../api/hooks';
import { ErrorState, LoadingState, Ltr } from '../components/common';
import { bandError, decodeBands, encodeBands, ExpiryBandsEditor, type BandRow } from '../components/ExpiryBands';
import { useNames, useRemainingText } from '../components/results';
import { OpStatusBadge, RiskBadge, StatusBadge } from '../components/StatusBadge';
import { t, tk } from '../i18n';

const FILTER_KEYS = [
  'type_code', 'base_code', 'squadron_code', 'status', 'compliance_status', 'qualification_code', 'q',
  'expiry',  // FR-14: per-unit "תוך … ועד …" bands, e.g. FH:0:50,CAL_DAY:0:7
] as const;

/** FR-14: filter by "תוך … ועד …" expiration bands per activity unit (what remains now; days for calendar). */
function ExpiryFilterButton({ value, onChange }: { value: string | null; onChange: (v: string | null) => void }) {
  const [opened, setOpened] = useState(false);
  const [rows, setRows] = useState<BandRow[]>([]);
  const active = decodeBands(value);
  const error = bandError(rows);
  return (
    <Popover opened={opened} onChange={setOpened} position="bottom-end" width={620} trapFocus shadow="md">
      <Popover.Target>
        <Button variant={active.length ? 'filled' : 'default'} leftSection={<IconHourglass size={16} />}
          rightSection={active.length ? <Badge size="xs" color="white" c="dark">{active.length}</Badge> : undefined}
          onClick={() => { setRows(active.length ? active : [{ unit: 'CAL_DAY', within: 0, until: 30 }]); setOpened((o) => !o); }}
          data-testid="filter-expiry">
          {t('expiry.filterButton')}
        </Button>
      </Popover.Target>
      <Popover.Dropdown>
        <Text size="sm" c="dimmed" mb="xs">{t('expiry.filterHint')}</Text>
        <ExpiryBandsEditor rows={rows} onChange={setRows} error={rows.length ? error : null} testId="filter-expiry-bands" />
        <Group mt="sm" justify="flex-end">
          <Button variant="subtle" onClick={() => { onChange(null); setOpened(false); }}>{t('action.clearFilters')}</Button>
          <Button disabled={!rows.length || Boolean(error)} data-testid="filter-expiry-apply"
            onClick={() => { onChange(encodeBands(rows)); setOpened(false); }}>
            {t('expiry.filterApply')}
          </Button>
        </Group>
      </Popover.Dropdown>
    </Popover>
  );
}

export function FleetPage() {
  const [params, setParams] = useSearchParams();
  const reference = useReference();
  const names = useNames();
  const remaining = useRemainingText();
  const navigate = useNavigate();
  const query = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? undefined]));
  const list = useAircraftList({ ...query, page_size: 200 });

  const setFilter = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const ref = reference.data;
  const typeName = (code: string) => ref?.aircraft_types.find((x) => x.type_code === code)?.name_he?.toString() ?? code;
  const baseName = (code: string) => ref?.bases.find((x) => x.base_code === code)?.name_he?.toString() ?? code;
  const squadronName = (code: string) =>
    ref?.squadrons.find((x) => x.squadron_code === code)?.name_he?.toString() ?? code;

  return (
    <Stack mt="md">
      <Group justify="space-between">
        <Title order={2}>{t('fleet.title')}</Title>
        <Group gap="sm">
          {list.data && <Text c="dimmed">{t('fleet.count', { count: list.data.total })}</Text>}
          <Button component={Link} to="/fleet/3d" variant="default" leftSection={<IconCube size={16} />} data-testid="fleet-3d-btn">
            {t('action.view3dFleet')}
          </Button>
        </Group>
      </Group>
      <Card>
        <Group gap="sm" align="flex-end">
          <TextInput label={t('field.search')} leftSection={<IconSearch size={16} />} dir="ltr" w={160}
            value={params.get('q') ?? ''} onChange={(e) => setFilter('q', e.currentTarget.value)} />
          <Select label={t('field.type')} clearable w={200} value={params.get('type_code')}
            data={(ref?.aircraft_types ?? []).map((x) => ({ value: String(x.type_code), label: String(x.name_he) }))}
            onChange={(v) => setFilter('type_code', v)} />
          <Select label={t('field.base')} clearable w={150} value={params.get('base_code')}
            data={(ref?.bases ?? []).map((x) => ({ value: String(x.base_code), label: String(x.name_he) }))}
            onChange={(v) => setFilter('base_code', v)} />
          <Select label={t('field.squadron')} clearable w={210} value={params.get('squadron_code')}
            data={(ref?.squadrons ?? []).map((x) => ({ value: String(x.squadron_code), label: String(x.name_he) }))}
            onChange={(v) => setFilter('squadron_code', v)} data-testid="filter-squadron" />
          <Select label={t('field.status')} clearable w={150} value={params.get('status')}
            data={['OPERATIONAL', 'GROUNDED', 'IN_MAINTENANCE', 'STORED'].map((v) => ({ value: v, label: tk(`opStatus.${v}`) }))}
            onChange={(v) => setFilter('status', v)} />
          <Select label={t('field.compliance')} clearable w={160} value={params.get('compliance_status')}
            data={['EXPIRED', 'UNKNOWN', 'EXPIRING_SOON', 'OK'].map((v) => ({ value: v, label: tk(`status.${v}`) }))}
            onChange={(v) => setFilter('compliance_status', v)} data-testid="filter-compliance" />
          <Select label={t('field.qualifications')} clearable w={170} value={params.get('qualification_code')}
            data={(ref?.qualifications ?? []).map((x) => ({ value: String(x.qualification_code), label: String(x.name_he) }))}
            onChange={(v) => setFilter('qualification_code', v)} />
          <ExpiryFilterButton value={params.get('expiry')} onChange={(v) => setFilter('expiry', v)} />
          <Button variant="subtle" leftSection={<IconFilterOff size={16} />} onClick={() => setParams({})}>
            {t('action.clearFilters')}
          </Button>
        </Group>
      </Card>
      {list.isPending ? <LoadingState /> : list.isError ? <ErrorState error={list.error} /> : (
        <Card p={0}>
          <Table.ScrollContainer minWidth={900}>
            <Table highlightOnHover verticalSpacing="sm" data-testid="fleet-table">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>{t('field.tailNumber')}</Table.Th>
                  <Table.Th>{t('field.type')}</Table.Th>
                  <Table.Th>{t('field.engines')}</Table.Th>
                  <Table.Th>{t('field.base')}</Table.Th>
                  <Table.Th>{t('field.squadron')}</Table.Th>
                  <Table.Th>{t('field.status')}</Table.Th>
                  <Table.Th>{t('field.compliance')}</Table.Th>
                  <Table.Th>{t('field.nextDue')}</Table.Th>
                  <Table.Th>{t('field.openMas')}</Table.Th>
                  <Table.Th>{t('risk.label')}</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {list.data.items.map((a) => (
                  <Table.Tr key={a.aircraft_id} className="ao-row" data-status={a.compliance_status}
                    data-tail={a.tail_number} onClick={() => navigate(`/aircraft/${a.tail_number}`)}>
                    <Table.Td fw={700} className="ao-tail"><Ltr>{a.tail_number}</Ltr></Table.Td>
                    <Table.Td>{typeName(a.type_code)}</Table.Td>
                    <Table.Td className="ao-num">{a.engine_count}</Table.Td>
                    <Table.Td>{baseName(a.base_code)}</Table.Td>
                    <Table.Td>{a.squadron_code ? squadronName(a.squadron_code) : t('common.dash')}</Table.Td>
                    <Table.Td><OpStatusBadge status={a.derived_status} size="sm" /></Table.Td>
                    <Table.Td>
                      <StatusBadge status={a.compliance_status} size="sm"
                        suffix={a.expired_count > 1 ? <Ltr>{` (${a.expired_count})`}</Ltr> : null} />
                    </Table.Td>
                    <Table.Td>
                      {a.next_due ? (
                        <Stack gap={0}>
                          <Text size="sm">{names.ref(a.next_due.kind, a.next_due.code)}</Text>
                          <Text size="xs" c="dimmed" className="ao-num">{remaining(a.next_due)}</Text>
                        </Stack>
                      ) : t('common.dash')}
                    </Table.Td>
                    <Table.Td className="ao-num">
                      {a.open_ma_count}
                      {a.grounding_ma_count > 0 && <Text span className="ao-text-expired" fw={700}> ({tk('severity.GROUNDING')})</Text>}
                    </Table.Td>
                    <Table.Td><RiskBadge band={a.risk_band} probability={a.risk_probability} size="sm" /></Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Card>
      )}
    </Stack>
  );
}
