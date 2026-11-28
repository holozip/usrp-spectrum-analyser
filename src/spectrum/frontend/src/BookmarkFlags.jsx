import {frequencyLabel} from './spectrumData.js';
import {placeBookmarkFlags} from './bookmarkLayout.js';

export function BookmarkFlags({bookmarks, data, layout, power, inspect, tune, tuneDisabled}) {
  const flags = placeBookmarkFlags(bookmarks, data, layout, power);
  return (
    <div className="bookmark-flags" role="group" aria-label="Visible spectrum bookmarks">
      <svg width={layout.width} height={layout.height} aria-hidden="true">
        {flags.flatMap(flag => flag.members.map(member => (
          <g key={member.id}>
            <path d={`M${flag.left + flag.width / 2} ${flag.top + flag.height} L${member.x} ${member.y}`} />
            <circle cx={member.x} cy={member.y} r="3" />
          </g>
        )))}
      </svg>
      {flags.map(flag => {
        const first = flag.members[0];
        const grouped = flag.members.length > 1;
        const description = flag.members.map(member => `${member.title}, ${frequencyLabel(member.frequency_hz, true)}${member.power == null ? ', no trace data' : ', ' + member.power.toFixed(1) + ' dBFS'}`).join('; ');
        const label = (grouped ? 'Inspect bookmarks: ' : 'Tune to ') + description;
        return <button key={first.id} type="button" className="bookmark-flag"
          style={{left: flag.left, top: flag.top, width: flag.width, height: flag.height}}
          title={label} aria-label={label} disabled={!grouped && tuneDisabled}
          onClick={() => grouped ? inspect(flag.members.map(member => member.id)) : tune(first)}>
          <span aria-hidden="true">⚑ </span>{grouped ? `${flag.members.length} bookmarks` : first.title}
        </button>;
      })}
    </div>
  );
}
