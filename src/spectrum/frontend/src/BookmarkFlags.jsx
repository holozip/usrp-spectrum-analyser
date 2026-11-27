import {frequencyLabel} from './spectrumData.js';
import {placeBookmarkFlags} from './bookmarkLayout.js';

export function BookmarkFlags({bookmarks, data, layout, power, inspect}) {
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
        const description = flag.members.map(member => `${member.title}, ${frequencyLabel(member.frequency_hz, true)}${member.power == null ? ', no trace data' : ', ' + member.power.toFixed(1) + ' dBFS'}`).join('; ');
        return <button key={first.id} type="button" className="bookmark-flag"
          style={{left: flag.left, top: flag.top, width: flag.width, height: flag.height}}
          title={description} aria-label={'Inspect bookmarks: ' + description}
          onClick={() => inspect(flag.members.map(member => member.id))}>
          <span aria-hidden="true">⚑ </span>{flag.members.length > 1 ? `${flag.members.length} bookmarks` : first.title}
        </button>;
      })}
    </div>
  );
}
