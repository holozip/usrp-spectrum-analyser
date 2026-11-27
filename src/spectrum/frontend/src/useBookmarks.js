import {useEffect, useRef, useState} from 'react';

export async function bookmarkRequest(path = '', method = 'GET', body, signal) {
  const response = await fetch('/api/bookmarks' + path, {method, signal,
    ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || 'Server returned HTTP ' + response.status);
  return data;
}

export function useBookmarks(open) {
  const [bookmarks, setBookmarks] = useState([]);
  const [error, setError] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  const session = useRef(null);

  useEffect(() => {
    const controller = new AbortController();
    session.current = controller;
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer;
    async function poll() {
      const started = generation.current;
      try {
        const result = await bookmarkRequest('', 'GET', undefined, controller.signal);
        if (!controller.signal.aborted && started === generation.current) {
          setBookmarks(result.bookmarks);
          setLoaded(true);
          setError(null);
        }
      } catch (failure) {
        if (!controller.signal.aborted) setError('Could not load bookmarks: ' + failure.message + '. Retry or reopen the panel.');
      } finally {
        if (open && !controller.signal.aborted) timer = setTimeout(poll, 1500);
      }
    }
    void poll();
    return () => {controller.abort(); clearTimeout(timer);};
  }, [open, refresh]);

  async function mutate(path, method, body) {
    generation.current++;
    const result = await bookmarkRequest(path, method, body, session.current?.signal);
    generation.current++;
    if (!session.current?.signal.aborted) {
      // Confirmed writes appear immediately; refresh then includes changes from other clients.
      if (method === 'DELETE') setBookmarks(previous => previous.filter(bookmark => bookmark.id !== result.deleted));
      else if (!path.endsWith('/tune')) setBookmarks(previous => [...previous.filter(bookmark => bookmark.id !== result.id), result]);
      setRefresh(previous => previous + 1);
    }
    return result;
  }

  return {bookmarks, error, loaded, mutate, retry: () => setRefresh(previous => previous + 1)};
}
