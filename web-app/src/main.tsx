import { QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter, RouterProvider } from 'react-router-dom';
import { makeQueryClient } from './api/queries';
import { routes } from './routes';
import { initTheme } from './state/theme';
import './styles/tokens.css';
import './styles/base.css';

initTheme();

const queryClient = makeQueryClient();
// Vite's base is '/app-next/' (see app-base.ts); the router wants it without the trailing slash
const router = createBrowserRouter(routes, { basename: import.meta.env.BASE_URL.replace(/\/$/, '') });

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
