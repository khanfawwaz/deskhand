/**
 * The ONE place that says where the app lives. While the old app still owns /app/, this is '/app-next/'.
 * At cut-over change it to '/app/' (and the server mount in src/taskpilot/spa.py), rebuild, done.
 */
export const APP_BASE = '/app-next/';
