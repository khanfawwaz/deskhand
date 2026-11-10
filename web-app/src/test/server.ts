import { setupServer } from 'msw/node';

/** A fake Deskhand server for tests. Each test adds the routes it needs with server.use(...); unknown calls fail loudly. */
export const server = setupServer();
