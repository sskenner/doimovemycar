(function () {
  "use strict";
  var base = document.body.getAttribute("data-base") || "/";

  // today's date in New York, as YYYY-MM-DD, and its weekday with Monday = 0
  function nycToday() {
    var p = {};
    new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" })
      .formatToParts(new Date()).forEach(function (x) { p[x.type] = x.value; });
    var wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(p.weekday);
    return { iso: p.year + "-" + p.month + "-" + p.day, weekday: wd };
  }

  function describe(entry) {
    var status = entry[0], reason = entry[1];
    if (status === "in_effect") return ["YES", "Alternate side parking is in effect today. Move your car for the times on your block's signs."];
    if (status === "suspended") return ["NO", "Alternate side parking is suspended today" + (reason ? " for " + reason : "") + "."];
    return ["NO", "Alternate side parking isn't in effect on Sundays."];
  }

  function withCalendar(fn) {
    var el = document.getElementById("cal");
    if (el) { fn(JSON.parse(el.textContent)); return; }
    fetch(base + "calendar.json").then(function (r) { return r.json(); }).then(fn).catch(function () {});
  }

  var today = nycToday();

  // home page: keep the answer right even if the page was built before midnight
  var live = document.querySelector("[data-live]");
  if (live) withCalendar(function (cal) {
    var e = cal[today.iso];
    if (!e) return;
    var d = describe(e);
    live.textContent = d[0];
    live.className = "a " + (d[0] === "YES" ? "yes" : "no");
    document.querySelector("[data-why]").textContent = d[1];
    var tl = document.querySelector("[data-trash]"), t = e[2];
    if (tl && t) {
      tl.hidden = false;
      tl.textContent = "🗑️ Trash & recycling pickup today is " +
        (t[0] === "normal" ? "on schedule" : t[0] + (t[1] ? " for " + t[1] : "")) + ".";
    }
    document.querySelectorAll(".week .d").forEach(function (li) {
      if (li.getAttribute("data-date") < today.iso) li.hidden = true;
    });
  });

  // street pages: today's status and which blocks are cleaned today
  var box = document.querySelector("[data-street-status]");
  if (box) withCalendar(function (cal) {
    var e = cal[today.iso] || ["in_effect", ""];
    var text = box.querySelector("[data-today-text]");
    var hits = 0;
    if (e[0] === "in_effect") {
      document.querySelectorAll("table.sched tbody tr").forEach(function (tr) {
        var days = (tr.getAttribute("data-days") || "").split(",");
        if (days.indexOf(String(today.weekday)) >= 0) { tr.classList.add("today"); hits++; }
      });
      text.textContent = hits ? "ASP is in effect. " + hits + " block side" + (hits === 1 ? " is" : "s are") + " cleaned today (highlighted below)."
                              : "ASP is in effect, but no blocks on this street are cleaned today.";
      box.classList.add(hits ? "yes" : "no");
    } else {
      text.textContent = describe(e)[1] + " You don't need to move your car.";
      box.classList.add("no");
    }
  });

  // street search
  var q = document.getElementById("q"), results = document.getElementById("results"), index = null;
  var ABBR = { ave: "avenue", av: "avenue", st: "street", rd: "road", blvd: "boulevard", pl: "place", pkwy: "parkway",
               dr: "drive", ln: "lane", ct: "court", ter: "terrace", hwy: "highway", sq: "square", e: "east", w: "west",
               n: "north", s: "south", ft: "fort", mt: "mount" };
  function norm(s) {
    return s.toLowerCase().replace(/(\d+)(st|nd|rd|th)\b/g, "$1").replace(/[^a-z0-9 ]/g, " ").split(/\s+/)
      .filter(Boolean).map(function (w) { return ABBR[w] || w; });
  }
  function load() {
    if (index) return Promise.resolve(index);
    return fetch(base + "search.json").then(function (r) { return r.json(); }).then(function (rows) {
      index = rows.map(function (r) { return { name: r[0], boro: r[1], path: r[2], words: norm(r[0]) }; });
      return index;
    });
  }
  function search() {
    var words = norm(q.value);
    if (!words.length) { results.innerHTML = ""; return; }
    load().then(function (rows) {
      var hits = rows.filter(function (r) {
        return words.every(function (w) { return r.words.some(function (x) { return x.indexOf(w) === 0; }); });
      }).slice(0, 12);
      results.innerHTML = hits.length ? hits.map(function (r) {
        return '<li><a href="' + base + r.path + '">' + r.name.replace(/</g, "&lt;") + '</a> <span class="muted">' + r.boro + "</span></li>";
      }).join("") : '<li class="muted">No street-cleaning signs found for that street.</li>';
    });
  }
  if (q) { q.addEventListener("input", search); q.addEventListener("focus", load, { once: true }); }
})();
