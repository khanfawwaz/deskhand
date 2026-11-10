import type { RouteObject } from 'react-router-dom';
import { ChatPane } from './features/chat/ChatPane';
import { PrivacyPage, TermsPage } from './features/legal/LegalPages';
import { AppLayout } from './features/shell/AppLayout';
import { NotFound } from './features/shell/NotFound';

/** Every route of the app. They live under the base path (see app-base.ts), which the router strips. */
export const routes: RouteObject[] = [
  {
    path: '/',
    element: <AppLayout />,
    children: [
      { index: true, element: <ChatPane /> },
      { path: 'chat/:id', element: <ChatPane /> },
    ],
  },
  { path: '/privacy', element: <PrivacyPage /> },
  { path: '/terms', element: <TermsPage /> },
  { path: '*', element: <NotFound /> },
];
