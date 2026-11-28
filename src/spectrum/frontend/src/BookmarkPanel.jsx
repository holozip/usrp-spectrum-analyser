import {Fragment, useEffect, useId, useRef, useState} from 'react';
import {frequencyLabel, FULL_RANGE} from './spectrumData.js';
import {MAX_BOOKMARK_BANDWIDTH} from './bookmarkSelection.js';
import {SidebarPanel} from './SidebarPanel.jsx';

function BookmarkForm({fields, change, mutate, done}) {
  const notesId = useId();
  const [error, setError] = useState(null);
  const saving = fields.saving;
  const title = useRef(null);
  useEffect(() => {title.current?.focus();}, []);
  function field(name, value) {change({[name]: value});}
  async function save(event) {
    event.preventDefault();
    const frequency = Number(fields.frequency) * 1e6;
    if (!fields.frequency.trim() || !Number.isFinite(frequency) || frequency < FULL_RANGE[0] || frequency > FULL_RANGE[1]) {
      setError('Enter a frequency from 70 to 6000 MHz.'); return;
    }
    if (!fields.title.trim() || fields.title.trim().length > 120) {setError('Enter a title from 1 to 120 characters.'); return;}
    const bandwidth = fields.bandwidth.trim() ? Number(fields.bandwidth) * 1000 : null;
    if (bandwidth !== null && (!Number.isFinite(bandwidth) || bandwidth < 1 || bandwidth > MAX_BOOKMARK_BANDWIDTH)) {
      setError('Enter a bandwidth from 0.001 to 56000 kHz, or leave it blank for unknown.'); return;
    }
    change({saving: true}); setError(null);
    try {
      await mutate(fields.id ? '/' + encodeURIComponent(fields.id) : '', fields.id ? 'PATCH' : 'POST',
        {frequency_hz: frequency, bandwidth_hz: bandwidth, title: fields.title.trim(), modulation: fields.modulation, tag: fields.tag.trim(), notes: fields.notes});
      done();
    } catch (failure) {setError('Could not save bookmark: ' + failure.message + '. Your changes are kept; try Save again.');}
    finally {change({saving: false});}
  }
  return <form className="bookmark-form" onSubmit={save} aria-label={fields.id ? 'Edit bookmark' : 'New bookmark'}
    onKeyDown={event => {if (event.key === 'Escape' && !saving) {event.preventDefault(); done();}}}>
    <h3>{fields.id ? 'Edit bookmark' : 'New bookmark'}</h3>
    <p>Selection and saving never tune the receiver. Use Tune to recall a saved signal.</p>
    <label>Title<input ref={title} value={fields.title} maxLength={120} required disabled={saving}
      onChange={event => field('title', event.target.value)} /></label>
    <label>Frequency (MHz)<input type="number" min="70" max="6000" step="any" required value={fields.frequency} disabled={saving}
      onChange={event => field('frequency', event.target.value)} /></label>
    <label>Bandwidth (kHz)<input type="number" min="0.001" max="56000" step="any" value={fields.bandwidth}
      placeholder="Unknown" disabled={saving} onChange={event => field('bandwidth', event.target.value)} /></label>
    <button type="button" disabled={saving} aria-pressed={fields.selecting} onClick={() => change({selecting: !fields.selecting})}>
      {fields.selecting ? 'Stop selecting on spectrum' : 'Select on spectrum'}</button>
    {fields.selecting && <p>Click or drag in the spectrum, then drag the centre or band edges. Arrow keys adjust a focused handle; Shift makes larger steps. Escape cancels the draft.</p>}
    <label>Modulation<input value={fields.modulation} maxLength={64} placeholder="Unknown" disabled={saving}
      onChange={event => field('modulation', event.target.value)} /></label>
    <label>Tag<input value={fields.tag} maxLength={64} placeholder="e.g. Local FM" disabled={saving}
      onChange={event => field('tag', event.target.value)} /></label>
    <label htmlFor={notesId}>Notes</label>
    <textarea id={notesId} value={fields.notes} maxLength={4096} rows={4} disabled={saving}
      onChange={event => field('notes', event.target.value)} />
    {error && <p className="input-error" role="alert">{error}</p>}
    <div className="bookmark-actions"><button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save bookmark'}</button>
      <button type="button" disabled={saving} onClick={done}>Cancel</button></div>
  </form>;
}

export function BookmarkPanel({store, close, actions, listen, selectedIds, showAll, draft, changeDraft, edit, cancelDraft}) {
  const [confirmDelete, setConfirmDelete] = useState(null);
  const {busy, error, tuneDisabled} = actions;
  const heading = useRef(null);
  useEffect(() => {heading.current?.focus();}, [selectedIds]);
  const bookmarks = store.bookmarks.filter(bookmark => !selectedIds || selectedIds.includes(bookmark.id))
    .sort((a, b) => a.frequency_hz - b.frequency_hz || a.title.localeCompare(b.title));

  async function removeBookmark(bookmark) {
    if (await actions.remove(bookmark)) setConfirmDelete(null);
  }
  return <SidebarPanel id="bookmarks-panel" label="Spectrum bookmarks" title="Bookmarks"
    closeLabel="Close bookmarks" close={close} headingRef={heading}>
    <p>Saved on this receiver · Tune recalls the spectrum view and selects the channel.</p>
    <button type="button" className="bookmark-add" disabled={draft?.saving} onClick={() => edit()}>Add bookmark</button>
    {store.error && <div><p className="input-error" role="alert">{store.error}</p><button type="button" onClick={store.retry}>Retry loading</button></div>}
    {error && <p className="input-error" role="alert">{error}</p>}
    {draft && <BookmarkForm key={draft.id ?? 'new'} fields={draft} change={changeDraft} mutate={store.mutate} done={cancelDraft} />}
    {selectedIds && <div className="bookmark-selection"><p>Selected plot group</p><button type="button" onClick={showAll}>Show all bookmarks</button></div>}
    {!store.loaded && !store.error && <p role="status">Loading bookmarks…</p>}
    {store.loaded && !bookmarks.length && <p>{selectedIds ? 'No bookmarks remain in this group.' : 'No bookmarks yet. Add the current view centre, or enter a frequency.'}</p>}
    <div className="bookmark-table-scroll" role="region" aria-label="Bookmark table" tabIndex={0}>
    <table className="bookmark-table"><caption className="visually-hidden">Saved signal bookmarks</caption>
      <thead><tr>{['Title', 'Frequency', 'Bandwidth', 'Modulation', 'Tag', 'Actions'].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
      <tbody>{bookmarks.map(bookmark => <Fragment key={bookmark.id}><tr>
      <th scope="row">{bookmark.title}</th>
      <td className="bookmark-frequency">{frequencyLabel(bookmark.frequency_hz, true)}</td>
      <td>{bookmark.bandwidth_hz == null ? 'Unknown' : (bookmark.bandwidth_hz / 1000).toLocaleString(undefined, {maximumFractionDigits: 3}) + ' kHz'}</td>
      <td>{bookmark.modulation || 'Unknown'}</td><td>{bookmark.tag || '—'}</td>
      <td><div className="bookmark-actions">
        <button type="button" disabled={tuneDisabled}
          aria-label={`Tune to ${frequencyLabel(bookmark.frequency_hz, true)}, ${bookmark.title}`}
          onClick={() => void actions.tune(bookmark)}>Tune</button>
        {listen && <button type="button" disabled={tuneDisabled}
          aria-label={'Listen to ' + bookmark.title} onClick={() => listen(bookmark)}>Listen</button>}
        <button type="button" disabled={busy || draft?.saving} onClick={() => edit(bookmark)} aria-label={'Edit ' + bookmark.title}>Edit</button>
        <button type="button" disabled={busy || draft?.saving} onClick={() => setConfirmDelete(bookmark.id)} aria-label={'Delete ' + bookmark.title}>Delete</button>
      </div></td></tr>
      {bookmark.notes && <tr className="bookmark-notes-row"><td colSpan={6}><p className="bookmark-notes">{bookmark.notes}</p></td></tr>}
      {confirmDelete === bookmark.id && <tr><td colSpan={6}><div className="bookmark-delete" role="group" aria-label={'Confirm deletion of ' + bookmark.title}>
        <p>Delete “{bookmark.title}”? This cannot be undone.</p>
        <button type="button" disabled={busy} onClick={() => void removeBookmark(bookmark)}>Confirm delete</button>
        <button type="button" disabled={busy} onClick={() => setConfirmDelete(null)}>Keep bookmark</button>
      </div></td></tr>}
    </Fragment>)}</tbody></table></div>
  </SidebarPanel>;
}
