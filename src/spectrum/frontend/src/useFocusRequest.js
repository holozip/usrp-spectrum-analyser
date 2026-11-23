import {useEffect, useRef, useState} from 'react';
import {writeReceiverSetting} from './receiverApi.js';

/** Debounce wheel input and send at most one focus POST at a time. */
export function useFocusRequest() {
  const [error, setError] = useState(null);
  const connection = useRef(null);

  useEffect(() => {
    const session = {controller: new AbortController(), timer: undefined, queued: undefined, sending: false};
    connection.current = session;
    return () => {
      clearTimeout(session.timer);
      session.controller.abort();
      connection.current = null;
    };
  }, []);

  function cancelDelay() {
    clearTimeout(connection.current?.timer);
  }

  function request(range, delay = 0) {
    const session = connection.current;
    if (!session) return;
    cancelDelay();
    setError(null);

    async function sendLatest() {
      session.queued = range;
      if (session.sending) return;
      session.sending = true;
      const {signal} = session.controller;
      while (!signal.aborted && session.queued !== undefined) {
        const nextRange = session.queued;
        session.queued = undefined;
        try {
          await writeReceiverSetting('focus', nextRange, signal);
          if (!signal.aborted) setError(null);
        } catch {
          if (!signal.aborted) setError('Could not update the receiver sweep range. Adjust the range to retry.');
        }
      }
      session.sending = false;
    }

    if (delay) session.timer = setTimeout(sendLatest, delay);
    else void sendLatest();
  }

  return {request, cancelDelay, error};
}
