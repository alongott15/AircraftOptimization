import { createTheme, type MantineColorsTuple } from '@mantine/core';

import { t } from '../i18n';

const brand: MantineColorsTuple = [
  '#EAF1FB', '#D3E1F5', '#A9C3EA', '#7EA4DF', '#5B8DEF', '#2F64B0', '#1D4E89', '#173F70', '#113158', '#0B2240',
];

export const theme = createTheme({
  primaryColor: 'brand',
  primaryShade: { light: 6, dark: 4 },
  colors: { brand },
  fontFamily: 'Heebo, Arial, sans-serif',
  headings: { fontFamily: 'Heebo, Arial, sans-serif', fontWeight: '700' },
  defaultRadius: 'md',
  focusRing: 'auto',
  components: {
    Card: { defaultProps: { withBorder: true, radius: 'md', padding: 'md' } },
    Paper: { defaultProps: { radius: 'md' } },
    // toasts: the close button needs an accessible name (axe button-name)
    Notification: { defaultProps: { closeButtonProps: { 'aria-label': t('action.close') } } },
  },
});
