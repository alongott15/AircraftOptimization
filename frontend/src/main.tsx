import '@fontsource/heebo/400.css';
import '@fontsource/heebo/500.css';
import '@fontsource/heebo/700.css';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import './theme/global.css';

import { DirectionProvider, MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import { AuthProvider } from './auth/AuthProvider';
import { theme } from './theme/theme';

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: true } },
});

const root = document.getElementById('root');
if (root) {
  createRoot(root).render(
    <StrictMode>
      <DirectionProvider initialDirection="rtl" detectDirection={false}>
        <MantineProvider theme={theme} defaultColorScheme="auto">
          <Notifications position="top-left" />
          <QueryClientProvider client={queryClient}>
            <BrowserRouter>
              <AuthProvider>
                <App />
              </AuthProvider>
            </BrowserRouter>
          </QueryClientProvider>
        </MantineProvider>
      </DirectionProvider>
    </StrictMode>,
  );
}
