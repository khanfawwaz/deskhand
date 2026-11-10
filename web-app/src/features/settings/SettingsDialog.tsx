import { useEffect, useRef } from 'react';
import { useMe } from '../../api/queries';
import type { Me } from '../../api/types';
import { useUi } from '../../state/ui';
import { AccountBox, DataBox } from './DataAndAccount';
import {
  AppearanceBox,
  ComputersBox,
  DevicesBox,
  ExtensionBox,
  PreferencesBox,
  SavedPreferencesBox,
  SkillsBox,
  UsageBox,
} from './boxes';
import { Modal } from './Modal';
import './installPrompt'; // starts listening for the home-screen install offer as soon as the app loads
import styles from './dialog.module.css';

/**
 * Settings: one box per idea. Opens from the useUi flag (the sidebar's user row sets it). The boxes exist only while the
 * dialog is open, so each opening asks the server again and shows what is true right now.
 */
export function SettingsDialog() {
  const me = useMe().data;
  const open = useUi((s) => s.settingsOpen);
  const close = useUi((s) => s.closeSettings);
  return (
    <Modal open={open && !!me} onClose={close} labelledBy="settingsTitle">
      {me ? <SettingsBody me={me} onClose={close} /> : null}
    </Modal>
  );
}

function SettingsBody({ me, onClose }: { me: Me; onClose: () => void }) {
  const section = useUi((s) => s.settingsSection);
  const skills = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (section === 'skills') skills.current?.scrollIntoView?.({ block: 'start' });
  }, [section]);

  return (
    <div className={styles.menu}>
      <h2 id="settingsTitle">Settings</h2>
      <ComputersBox me={me} />
      <ExtensionBox me={me} />
      <DevicesBox />
      <AppearanceBox />
      <PreferencesBox />
      <UsageBox />
      <SavedPreferencesBox />
      <div ref={skills}>
        <SkillsBox forceOpen={section === 'skills'} />
      </div>
      <DataBox />
      <AccountBox me={me} />
      <button type="button" className="primary wide" onClick={onClose}>
        Done
      </button>
    </div>
  );
}
