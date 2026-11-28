import {useEffect, useEffectEvent, useRef, useState} from 'react';
import {BookmarkPanel} from './BookmarkPanel.jsx';
import {antennaLabel, ReceiverControls, useReceiverSetting} from './ReceiverControls.jsx';
import {SpectrumPanel} from './SpectrumPanel.jsx';
import {FULL_RANGE, sameRange} from './spectrumData.js';
import {useFocusRequest} from './useFocusRequest.js';
import {useSweep} from './useSweep.js';
import {useBookmarks} from './useBookmarks.js';
import {observeBookmarkNavigation} from './bookmarkNavigation.js';
import {newBookmarkDraft} from './bookmarkSelection.js';
import {bookmarkChannelSetting, ChannelPanel, useChannelReceiver} from './ChannelPanel.jsx';

function connectionStatus(latest, frozen, disconnected) {
  if (disconnected) return 'Disconnected · Retrying';
  if (!latest) return 'Connecting';
  const mode = latest.demo ? 'Demo' : latest.fpga ? 'B210 FPGA' : 'B210';
  if (latest.error) return mode + ' · Retrying';
  if (frozen) return mode + ' · Trace frozen';
  return mode + (latest.continuous ? ' · Continuous capture' : ' · Sweeping');
}

function captureDescription(shown) {
  if (!shown?.number) return 'Waiting for the first sweep';
  let description;
  if (shown.continuous) {
    const tuning = shown.full_window ? 'Full 56 MHz window · DC and filter edges visible' : 'Fixed tuning';
    description = tuning + ' · ' + (shown.rate_hz / 1e6) + ' MS/s · '
      + shown.fft_size.toLocaleString() + '-point FFT' + (shown.gaps ? ' · RX gaps ' + shown.gaps : '');
  } else {
    const focused = shown.low > FULL_RANGE[0] || shown.high < FULL_RANGE[1];
    description = (focused ? 'Focused sweep' : 'Full sweep') + ' · '
      + shown.hops + ' hops · ' + shown.seconds + ' s per pass';
  }
  const averages = shown.fft_averages;
  if (averages != null) description += ' · ' + averages + (averages === 1 ? ' average' : ' averages');
  if (shown.antenna) description += ' · ' + antennaLabel(shown.antenna);
  return description;
}

function detailStatus({latest, shown, frozen, view}, antenna, gain, averages, imageEnabled) {
  if (!latest) return 'Each trace combines sequential receiver captures.';
  if (antenna.pendingValue !== null || (latest.requested_antenna && latest.antenna !== latest.requested_antenna)) {
    return 'Switching RX antenna…';
  }
  if (gain.pendingValue !== null || (!latest.demo && latest.gain != null
      && latest.gain !== Math.trunc(latest.requested_gain))) return 'Applying RX gain…';
  if (latest.number && !frozen && !latest.listen?.settings && !sameRange(latest.capture_focus, view)) {
    return 'Updating frequency range…';
  }
  if (averages.pendingValue !== null || (latest.fft_averages != null
      && latest.fft_averages !== latest.requested_averages)) return 'Applying FFT averages…';

  let image = '';
  if (imageEnabled && latest.continuous) image = ' · Image rejection available in swept views';
  else if (imageEnabled && !shown?.imageSet) image = ' · Waiting for three sweeps';
  else if (shown?.imageSet) image = ' · One result per three sweeps';
  return captureDescription(shown) + image;
}

export default function Dashboard() {
  // Before the first local gesture, use the focus the Python receiver reports on connection.
  const [localView, previewRange] = useState(undefined);
  const sweep = useSweep(localView);
  const {latest, shown, view, frozen, disconnected, toggleFreeze, confirmSetting} = sweep;
  const channel = useChannelReceiver(latest?.listen, confirmSetting, view, latest?.focus);
  const focus = useFocusRequest();
  const [bookmarksOpen, setBookmarksOpen] = useState(false);
  const [selectedBookmarks, setSelectedBookmarks] = useState(null);
  const [bookmarkDraft, setBookmarkDraft] = useState(null);
  const [bookmarkBusy, setBookmarkBusy] = useState(false);
  const [bookmarkError, setBookmarkError] = useState(null);
  const bookmarks = useBookmarks(bookmarksOpen);
  const navigationRevision = useRef(undefined);
  const localNavigation = useRef(0);
  const bookmarksToggle = useRef(null);
  const channelToggle = useRef(null);
  const revision = latest?.navigation_revision;
  const receiverFocus = latest?.focus;
  const cancelFocusDelay = focus.cancelDelay;
  const selectRecalledChannel = useEffectEvent(() => {
    if (latest?.bookmark_channel && channel.state?.available) {
      channel.selectBookmark(latest.bookmark_channel, latest.listen.revision);
    }
  });

  useEffect(() => {
    // The initial revision is historical: retain the connection's normal focus.
    const observed = observeBookmarkNavigation(navigationRevision.current, revision);
    navigationRevision.current = observed.revision;
    if (observed.recall) {
      cancelFocusDelay();
      previewRange(receiverFocus ?? null);
      selectRecalledChannel();
    }
  }, [revision, receiverFocus, cancelFocusDelay]);
  const antenna = useReceiverSetting('antenna', 'requested_antenna', confirmSetting,
    'Could not change RX antenna. Select an input to retry.');
  const gain = useReceiverSetting('gain', 'requested_gain', confirmSetting,
    'Could not change RX gain. Adjust the slider or press Enter to retry.');
  const averages = useReceiverSetting('averages', 'requested_averages', confirmSetting,
    'Could not change FFT averages. Press Enter or leave the field to retry.');
  const imageSetting = useReceiverSetting('image-reject', 'image_reject_requested', confirmSetting,
    'Could not change image rejection. Select Image reject to retry.');

  const imageEnabled = imageSetting.pendingValue ?? latest?.image_reject_requested ?? false;
  const image = {
    enabled: imageEnabled,
    available: Boolean(latest?.image_reject_available),
    pending: imageSetting.pendingValue !== null,
    toggle: () => void imageSetting.save(!imageEnabled),
  };
  const error = latest?.antenna_error || latest?.error || antenna.error || focus.error || gain.error || imageSetting.error;

  function commitRange(range, delay) {
    localNavigation.current++;
    previewRange(range);
    focus.request(range, delay);
  }

  async function stopListening(full = false) {
    if (!full) {
      await channel.configure(null);
      return;
    }
    await focus.settle();
    const result = await channel.configure({stop: 'full'});
    if (result) {
      localNavigation.current++;
      confirmSetting('focus', result.survey_focus);
      previewRange(result.survey_focus);
    }
  }

  function listenToBookmark(bookmark) {
    channel.setOpen(true);
    void channel.configure(bookmarkChannelSetting(bookmark), true);
  }

  async function tuneBookmark(bookmark) {
    channel.cancelEdits();
    const started = localNavigation.current;
    await focus.settle();
    // A later local gesture wins, including at the receiver: do not send a
    // superseded recall merely because an older focus request finally settled.
    if (started !== localNavigation.current) return;
    const sentRevision = navigationRevision.current;
    const span = view ? view[1] - view[0] : Infinity;
    const result = await bookmarks.mutate('/' + encodeURIComponent(bookmark.id) + '/tune', 'POST',
      bookmark.bandwidth_hz != null ? {} : {span_hz: span <= 56e6 ? span : 2e6});
    // A newer observed recall can supersede this response, but a counter that
    // reset while reconnecting must not make a successful new recall obsolete.
    if (navigationRevision.current !== sentRevision && result.navigation_revision < navigationRevision.current) return;
    navigationRevision.current = result.navigation_revision;
    // Prevent an older sweep GET undoing this confirmed recall.
    confirmSetting('navigation_revision', result.navigation_revision);
    confirmSetting('focus', result.focus);
    if (result.channel) confirmSetting('bookmark_channel', result.channel);
    if (result.listen) confirmSetting('listen', result.listen);
    if (started === localNavigation.current) {
      previewRange(result.focus);
      if (result.channel && result.listen?.available) channel.selectBookmark(result.channel, result.listen.revision);
    }
  }

  async function performBookmarkAction(action, errorMessage) {
    if (bookmarkBusy) return false;
    setBookmarkBusy(true);
    setBookmarkError(null);
    try {
      await action();
      return true;
    } catch (failure) {
      setBookmarkError(`${errorMessage}: ${failure.message}. Try again.`);
      return false;
    } finally {
      setBookmarkBusy(false);
    }
  }

  function closeBookmarks() {
    if (bookmarkDraft?.saving) return;
    setBookmarkDraft(null);
    setBookmarkError(null);
    setBookmarksOpen(false);
    bookmarksToggle.current?.focus();
  }

  function closeChannel() {
    channel.setOpen(false);
    channelToggle.current?.focus();
  }

  function editBookmark(bookmark = null, selecting = false) {
    const range = view ?? FULL_RANGE;
    setBookmarkDraft({...newBookmarkDraft(bookmark, (range[0] + range[1]) / 2), selecting: selecting || Boolean(bookmark)});
    setBookmarksOpen(true);
    setSelectedBookmarks(null);
  }

  function changeBookmarkDraft(fields) {
    setBookmarkDraft(previous => previous ? {...previous, ...fields} : previous);
  }

  const bookmarkSelection = bookmarkDraft?.selecting && !bookmarkDraft.saving ? {
    draft: bookmarkDraft, change: changeBookmarkDraft, cancel: () => setBookmarkDraft(null),
  } : null;
  const bookmarkActions = {
    inspect: setSelectedBookmarks,
    tune: bookmark => performBookmarkAction(() => tuneBookmark(bookmark), 'Could not tune to bookmark'),
    remove: bookmark => performBookmarkAction(() => bookmarks.mutate('/' + encodeURIComponent(bookmark.id), 'DELETE'),
      'Could not delete bookmark'),
    busy: bookmarkBusy,
    error: bookmarkError,
    tuneDisabled: bookmarkBusy || Boolean(bookmarkDraft?.saving) || channel.pending,
  };

  return (
    <main>
      <ReceiverControls
        latest={latest} disconnected={disconnected} antenna={antenna} gain={gain} averages={averages}
        canReset={!channel.pending && (channel.active || view !== null || Boolean(focus.error))}
        resetRange={() => channel.active ? stopListening(true) : commitRange(null)}
      />
      <div className={'spectrum-workspace' + (bookmarksOpen ? ' bookmarks-open' : '') + (channel.open ? ' channel-open' : '')}>
      <SpectrumPanel
        latest={latest} shown={shown} view={view} frozen={frozen} status={connectionStatus(latest, frozen, disconnected)}
        toggleFreeze={toggleFreeze} previewRange={previewRange} commitRange={commitRange}
        cancelFocusDelay={focus.cancelDelay} image={image}
        channel={channel.active || channel.open ? channel : null}
        channelTools={channel.state && {open: channel.open, buttonRef: channelToggle,
          toggle: () => channel.open ? closeChannel() : channel.setOpen(true), disabled: !channel.state.available,
          title: channel.state.available ? undefined : 'Install SciPy on the receiver to enable channels'}}
        bookmarks={bookmarksOpen ? bookmarks.bookmarks : null}
        bookmarkTools={{open: bookmarksOpen, toggle: () => bookmarksOpen ? closeBookmarks() : setBookmarksOpen(true),
          buttonRef: bookmarksToggle, select: () => bookmarkDraft ? changeBookmarkDraft({selecting: true}) : editBookmark(null, true)}}
        bookmarkSelection={bookmarkSelection}
        bookmarkActions={bookmarkActions}
      />
      {(channel.open || bookmarksOpen) && <div className="receiver-sidebar">
      {channel.open && <ChannelPanel receiver={channel} stop={() => stopListening()} close={closeChannel} />}
      {bookmarksOpen && <BookmarkPanel store={bookmarks} close={closeBookmarks}
        draft={bookmarkDraft} changeDraft={changeBookmarkDraft} edit={editBookmark} cancelDraft={() => setBookmarkDraft(null)}
        actions={bookmarkActions} listen={channel.state?.available ? listenToBookmark : null}
        selectedIds={selectedBookmarks} showAll={() => setSelectedBookmarks(null)} />}
      </div>}
      </div>
      <footer>
        <p id="detail">{error || detailStatus(sweep, antenna, gain, averages, imageEnabled)}</p>
        <span className="visually-hidden" role="status">{error}</span>
        <p>Scroll to zoom · Drag to pan · Shift-drag to select a range</p>
        <p>dBFS axis · Drag to pan · Scroll to scale · Double-click to reset</p>
        <span id="frequency-keys" hidden>Plus and minus to zoom, left and right arrows to pan, Home for full spectrum.</span>
        <span id="power-keys" hidden>Plus and minus to scale, up and down arrows to pan, Home to reset.</span>
      </footer>
    </main>
  );
}
