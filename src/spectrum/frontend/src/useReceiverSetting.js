import {useEffect, useRef, useState} from 'react';
import {writeReceiverSetting} from './receiverApi.js';

export function useReceiverSetting(endpoint, field, confirmSetting, failureMessage) {
  const request = useRef(null);
  const [pendingValue, setPendingValue] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => () => request.current?.abort(), []);

  async function save(value) {
    if (request.current) return false;
    const controller = new AbortController();
    request.current = controller;
    setError(null);
    setPendingValue(value);
    try {
      const confirmed = await writeReceiverSetting(endpoint, value, controller.signal);
      if (controller.signal.aborted) return false;
      confirmSetting(field, confirmed);
      return true;
    } catch {
      if (!controller.signal.aborted) setError(failureMessage);
      return false;
    } finally {
      request.current = null;
      if (!controller.signal.aborted) setPendingValue(null);
    }
  }

  return {pendingValue, error, save};
}
