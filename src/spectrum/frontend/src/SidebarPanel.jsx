export function SidebarPanel({id, label, title, closeLabel, close, headingRef, children}) {
  return <aside id={id} className="sidebar-panel" aria-label={label}>
    <div className="sidebar-heading">
      <h2 ref={headingRef} tabIndex={headingRef ? -1 : undefined}>{title}</h2>
      <button type="button" aria-label={closeLabel} onClick={close}>Close</button>
    </div>
    {children}
  </aside>;
}
