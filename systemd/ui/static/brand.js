/*
  Shared across every screen via base.html. Currently just the header
  clock — a natural place for any other truly cross-screen behaviour
  later, so individual screens don't each reimplement it.
*/

function formatHeaderDateTime(date) {
  const day = date.getDate();
  const month = date.getMonth() + 1; // NZ/DD-MM convention, matching the sketch's "8/7/26" = 8 July
  const year = String(date.getFullYear()).slice(-2);

  let hours = date.getHours();
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const ampm = hours >= 12 ? "pm" : "am";
  hours = hours % 12;
  if (hours === 0) hours = 12;

  return `${day}/${month}/${year} ${hours}:${minutes}${ampm}`;
}

function tickHeaderClock() {
  const el = document.getElementById("headerDateTime");
  if (!el) return;
  el.textContent = formatHeaderDateTime(new Date());
}

tickHeaderClock();
setInterval(tickHeaderClock, 1000 * 15); // minute-granularity display, no need to tick every second

/*
  Shared by any screen showing mob/session dates in a content area rather
  than the header chrome — a friendlier "9 Jul" reads better in a list or
  card than the header's compact "9/7/26" does, so this is deliberately
  a different (not wrong, just differently-suited) format from
  formatHeaderDateTime above.
*/
function formatShortDate(unixSeconds) {
  if (!unixSeconds) return "—";
  const d = new Date(unixSeconds * 1000);
  return d.toLocaleDateString([], { day: "numeric", month: "short" });
}

/*
  Session duration in a farmer-friendly form — "1h 15m", "45 min", or
  "30 sec" for anything under a minute. Drops the hours component
  entirely when there isn't one, rather than always showing "0h 12m".
*/
function formatDuration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} sec`;

  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);

  if (hours === 0) return `${minutes} min`;
  return `${hours}h ${minutes}m`;
}
