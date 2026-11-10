import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useMe } from '../../api/queries';
import { addDevice, settingsKeys } from '../../api/settings';
import type { Me } from '../../api/types';
import { useSession } from '../../state/session';
import { useUi } from '../../state/ui';
import { Modal } from '../settings/Modal';
import styles from '../settings/dialog.module.css';
import { EXTENSION_WHY, extensionLink } from './extension';

/** An install command works for 15 minutes: get a fresh one a minute before it runs out. */
export const CONNECT_LIFE_MS = 14 * 60 * 1000;
/** How long "X is connected." stays on screen before the dialog moves on to the Chrome step. */
export const ARRIVED_PAUSE_MS = 1300;

/** The sign-in this dialog already opened itself for: it offers itself once per visit, not every time the list is empty. */
let askedFor: string | null = null;
/** For tests: forget that the dialog already offered itself. */
export function resetConnectAsked(): void {
  askedFor = null;
}

/**
 * "Connect your computer": opens by itself right after sign-in when there is nothing to run tasks on, and from the
 * "Connect a computer" buttons. Step 1 is the install command with a live status; once the computer shows up it moves
 * to step 2, "Use your own Chrome".
 */
export function ConnectDialog() {
  const me = useMe().data;
  const token = useSession((s) => s.token);
  const open = useUi((s) => s.connectOpen);
  const close = useUi((s) => s.closeConnect);
  const none = !!me && me.devices.length === 0;

  useEffect(() => {
    if (token && none && askedFor !== token) {
      askedFor = token;
      useUi.getState().openConnect();
    }
  }, [token, none]);

  return (
    <Modal open={open && !!me} onClose={close} labelledBy="connectTitle">
      {me ? <ConnectBody me={me} onClose={close} /> : null}
    </Modal>
  );
}

/** Mounted only while the dialog is open, so every opening starts again at step 1 with a fresh command. */
function ConnectBody({ me, onClose }: { me: Me; onClose: () => void }) {
  // the computers that were already here when the dialog opened: anything else is the one being connected
  const [known] = useState(() => new Set(me.devices.map((d) => d.device_id)));
  const [chromeFor, setChromeFor] = useState<string | null>(null); // step 2, for this computer
  const [copied, setCopied] = useState<string | null>(null); // the command that was copied

  const arrived = me.devices.find((d) => !known.has(d.device_id));
  const arrivedId = arrived?.device_id;

  const cmd = useQuery({
    queryKey: settingsKeys.connectCommand,
    queryFn: addDevice,
    enabled: !arrivedId && !chromeFor, // once the computer is here no more commands are needed
    refetchInterval: CONNECT_LIFE_MS,
    refetchOnMount: 'always',
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  });

  useEffect(() => {
    if (!arrivedId || chromeFor) return;
    const t = setTimeout(() => setChromeFor(arrivedId), ARRIVED_PAUSE_MS); // say "connected" for a moment, then move on
    return () => clearTimeout(t);
  }, [arrivedId, chromeFor]);

  if (chromeFor) return <ChromeStep me={me} deviceId={chromeFor} onClose={onClose} />;

  const command = cmd.isFetching || !cmd.data ? '' : cmd.data.command;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(command);
    } catch {
      /* the command is selectable: copy it by hand */
    }
  };

  return (
    <div className={`${styles.menu} ${styles.connect}`}>
      <h2 id="connectTitle">Connect your computer</h2>
      <p className="lead">Deskhand does its work on a Windows computer. Install its small helper once, it takes about a minute.</p>

      <ol className={styles.stepcards}>
        <li className={styles.stepcard}>
          <span className={styles.num}>1</span>
          <div className={styles.body}>
            <b>Copy your install command</b>
            <code className={styles.cmd} data-testid="connect-command">
              {cmd.isFetching ? 'Getting your command…' : command}
            </code>
            <button type="button" className="primary wide" disabled={!command} onClick={() => void copy()}>
              {command && copied === command ? 'Copied. Now paste it into PowerShell.' : 'Copy command'}
            </button>
          </div>
        </li>
        <li className={styles.stepcard}>
          <span className={styles.num}>2</span>
          <div className={styles.body}>
            <b>Open PowerShell on that computer</b>
            <p className="hint">
              Press the Windows key, type <i>PowerShell</i>, press Enter.
            </p>
          </div>
        </li>
        <li className={styles.stepcard}>
          <span className={styles.num}>3</span>
          <div className={styles.body}>
            <b>Paste it and press Enter</b>
            <p className="hint">This page notices by itself when the computer is ready.</p>
          </div>
        </li>
      </ol>

      <div className={styles.statusbox} data-state={arrived ? 'done' : 'waiting'} role="status" aria-live="polite">
        <span className={styles.spin} aria-hidden="true" />
        <span>{arrived ? `${arrived.name || 'Your computer'} is connected.` : 'Waiting for your computer…'}</span>
      </div>
      <p className="err" role="alert">
        {cmd.isError && !cmd.isFetching ? `Could not get a command: ${cmd.error.message}` : ''}
      </p>
      <div className={styles.actions}>
        <button type="button" className="ghost" onClick={onClose}>
          Not now
        </button>
        <button type="button" className="ghost" disabled={cmd.isFetching || !!arrived} onClick={() => void cmd.refetch()}>
          Get a new command
        </button>
      </div>
    </div>
  );
}

/** Step 2, once the computer is connected: use the person's own Chrome through the extension. */
function ChromeStep({ me, deviceId, onClose }: { me: Me; deviceId: string; onClose: () => void }) {
  const on = !!me.devices.find((d) => d.device_id === deviceId)?.extension;
  const url = extensionLink(me.extension_url);
  return (
    <div className={`${styles.menu} ${styles.connect}`}>
      <h2 id="connectTitle">Use your own Chrome</h2>
      <p className="lead">Your computer is connected. One more thing, so Deskhand can use your own Chrome:</p>
      <div className={styles.extcard}>
        <b>Use your own Chrome</b>
        <p className="hint">{EXTENSION_WHY}</p>
        <div className={styles.statusbox} data-state={on ? 'done' : url ? 'waiting' : 'soon'} role="status" aria-live="polite">
          <span className={styles.spin} aria-hidden="true" />
          <span>{on ? 'Chrome extension connected' : url ? 'Waiting for the extension to connect…' : 'Coming soon'}</span>
        </div>
        {!on && url ? (
          <a className={styles.extlink} href={url} target="_blank" rel="noopener noreferrer">
            Add to Chrome
          </a>
        ) : null}
      </div>
      <div className={styles.actions}>
        {on ? null : (
          <button type="button" className="ghost" onClick={onClose}>
            Skip for now
          </button>
        )}
        <button type="button" className="primary" onClick={onClose}>
          Done
        </button>
      </div>
    </div>
  );
}
