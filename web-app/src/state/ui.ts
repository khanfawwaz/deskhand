import { create } from 'zustand';

/**
 * Which app-wide dialogs are open. Any screen can open one (`useUi.getState().openSettings()`), and the dialog
 * components in features/settings and features/connect read these flags, so features never import each other.
 */
interface UiState {
  settingsOpen: boolean;
  connectOpen: boolean;
  openSettings: () => void;
  closeSettings: () => void;
  openConnect: () => void;
  closeConnect: () => void;
  /** which Settings box to scroll to when it opens ('skills' = What Deskhand has learned); null = the top */
  settingsSection: string | null;
  openSettingsAt: (section: string) => void;
  /** the phone sidebar drawer (owned by the shell) */
  drawerOpen: boolean;
  openDrawer: () => void;
  closeDrawer: () => void;
}

export const useUi = create<UiState>((set) => ({
  settingsOpen: false,
  connectOpen: false,
  openSettings: () => set({ settingsOpen: true, settingsSection: null }),
  closeSettings: () => set({ settingsOpen: false }),
  openConnect: () => set({ connectOpen: true, settingsOpen: false }), // one dialog at a time
  closeConnect: () => set({ connectOpen: false }),
  settingsSection: null,
  openSettingsAt: (section) => set({ settingsOpen: true, settingsSection: section }),
  drawerOpen: false,
  openDrawer: () => set({ drawerOpen: true }),
  closeDrawer: () => set({ drawerOpen: false }),
}));
