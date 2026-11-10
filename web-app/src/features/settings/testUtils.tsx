import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { MemoryRouter } from 'react-router-dom';
import { makeQueryClient } from '../../api/queries';
import type { DeviceView, Me } from '../../api/types';
import { useSession } from '../../state/session';
import { useUi } from '../../state/ui';
import { server } from '../../test/server';
import { ConnectDialog } from '../connect/ConnectDialog';
import { SettingsDialog } from './SettingsDialog';

export const DEVICE: DeviceView = { device_id: 'd1', name: 'Office PC', online: true, last_seen: 1, extension: false };

export function makeMe(over: Partial<Me> = {}): Me {
  return { client_id: 'c1', name: 'Alex', workspace: { id: 'w', name: 'Alex' }, devices: [DEVICE], extension_url: '', ...over };
}

/** Only the two dialogs, signed in, with /v1/me answering `me`. Tests that need more routes add them with server.use. */
export function renderDialogs(me: Me = makeMe()) {
  useSession.setState({ token: 'tok' });
  useUi.setState({ settingsOpen: false, connectOpen: false, settingsSection: null });
  server.use(http.get('*/v1/me', () => HttpResponse.json(me)));
  const client = makeQueryClient();
  client.setDefaultOptions({ queries: { retry: false, staleTime: 0, refetchOnWindowFocus: false } });
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ConnectDialog />
        <SettingsDialog />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { client, ...view };
}
