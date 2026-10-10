import { Alert, Button, Center, Paper, PasswordInput, Stack, Text, TextInput, Title } from '@mantine/core';
import { IconAlertOctagon } from '@tabler/icons-react';
import { useState, type FormEvent } from 'react';

import { ApiError } from '../api/client';
import { useAuthConfig, useLogin } from '../api/hooks';
import { errorMessage } from '../components/common';
import { LogoMark } from '../components/Logo';
import { t, tk } from '../i18n';

export function LoginPage() {
  const login = useLogin();
  const config = useAuthConfig();
  const ssoError = new URLSearchParams(window.location.search).get('sso_error');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ username: username.trim(), password });
  };

  const error = login.error;
  const message =
    error instanceof ApiError && error.code === 'UNAUTHENTICATED' ? t('login.failed') : error ? errorMessage(error) : null;

  return (
    <Center mih="100vh" p="md">
      <Paper withBorder shadow="sm" p="xl" w={380} maw="100%">
        <form onSubmit={submit}>
          <Stack>
            <Stack gap={4} align="center">
              <LogoMark size={56} />
              <Title order={3}>{t('app.title')}</Title>
              <Text c="dimmed" size="sm">
                {t('login.title')}
              </Text>
            </Stack>
            {(message || ssoError) && (
              <Alert color="red" icon={<IconAlertOctagon />} role="alert">
                {message ?? tk(`err.${ssoError ?? ''}`, undefined, t('err.UNAUTHENTICATED'))}
              </Alert>
            )}
            {config.data?.mode === 'oidc' ? (
              <Stack gap="xs">
                <Button component="a" href="/api/v1/auth/oidc/login" fullWidth data-testid="sso-login">
                  {t('login.sso')}
                </Button>
                <Text size="xs" c="dimmed" ta="center">{t('login.ssoHint')}</Text>
              </Stack>
            ) : (
              <>
            <TextInput
              label={t('field.username')}
              value={username}
              onChange={(e) => setUsername(e.currentTarget.value)}
              autoComplete="username"
              required
              dir="ltr"
            />
            <PasswordInput
              label={t('field.password')}
              value={password}
              onChange={(e) => setPassword(e.currentTarget.value)}
              autoComplete="current-password"
              required
            />
            <Button type="submit" loading={login.isPending} fullWidth>
              {t('action.login')}
            </Button>
              </>
            )}
          </Stack>
        </form>
      </Paper>
    </Center>
  );
}
