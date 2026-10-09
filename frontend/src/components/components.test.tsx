import { DirectionProvider, MantineProvider } from '@mantine/core';
import { render, screen } from '@testing-library/react';
import type { ReactNode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import { theme } from '../theme/theme';
import { AlertBanner, Ltr } from './common';
import { OpStatusBadge, StatusBadge } from './StatusBadge';

function wrap(ui: ReactNode) {
  return render(
    <DirectionProvider initialDirection="rtl" detectDirection={false}>
      <MantineProvider theme={theme}>
        <MemoryRouter>{ui}</MemoryRouter>
      </MantineProvider>
    </DirectionProvider>,
  );
}

describe('StatusBadge', () => {
  it.each([
    ['EXPIRED', 'פג תוקף'],
    ['EXPIRING_SOON', 'עומד לפוג'],
    ['OK', 'בתוקף'],
    ['UNKNOWN', 'נתונים חסרים'],
  ])('renders %s as icon + Hebrew text', (status, label) => {
    const { container } = wrap(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(container.querySelector('svg')).not.toBeNull();
    expect(container.querySelector(`[data-tone="${status}"]`)).not.toBeNull();
  });

  it('maps derived status to tones', () => {
    const { container } = wrap(<OpStatusBadge status="GROUNDED" />);
    expect(screen.getByText('מקורקע')).toBeInTheDocument();
    expect(container.querySelector('[data-tone="EXPIRED"]')).not.toBeNull();
  });
});

describe('AlertBanner', () => {
  it('is hidden when nothing is expired', () => {
    wrap(<AlertBanner count={0} />);
    expect(screen.queryByTestId('expired-banner')).toBeNull();
  });

  it('shows the count and a link to the filtered fleet', () => {
    wrap(<AlertBanner count={4} />);
    expect(screen.getByRole('alert')).toHaveTextContent('מטוסים עם פריטים שפג תוקפם: 4');
    expect(screen.getByRole('link', { name: 'הצג' })).toHaveAttribute('href', '/fleet?compliance_status=EXPIRED');
  });
});

describe('Ltr', () => {
  it('isolates tail numbers left-to-right', () => {
    const { container } = wrap(<Ltr>4X-P07</Ltr>);
    const bdi = container.querySelector('bdi');
    expect(bdi).toHaveAttribute('dir', 'ltr');
    expect(bdi).toHaveTextContent('4X-P07');
  });
});
