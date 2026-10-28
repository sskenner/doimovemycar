// "When is my trash pickup?" — runs entirely in the browser:
// address -> lat/lon (NYC Planning GeoSearch) -> DSNY collection area (NYC Open Data spatial query)
// -> next pickup dates, using the site's calendar.json for holiday/snow suspensions.
(function () {
  "use strict";
  var base = document.body.getAttribute("data-base") || "/";
  var GEOSEARCH = "https://geosearch.planninglabs.nyc/v2/";
  var AREAS = "https://data.cityofnewyork.us/resource/rv63-53db.json";
  var DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var form = document.getElementById("pickup-form"), input = document.getElementById("addr");
  var list = document.getElementById("addr-list"), out = document.getElementById("pickup-result");
  if (!form) return;

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function getJSON(url) { return fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }

  // New York's date for "today", as YYYY-MM-DD, and a helper to step days
  function nycIso(d) {
    var p = {};
    new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" })
      .formatToParts(d).forEach(function (x) { p[x.type] = x.value; });
    return p.year + "-" + p.month + "-" + p.day;
  }
  function addDays(iso, n) { return new Date(Date.parse(iso + "T12:00:00Z") + n * 86400000).toISOString().slice(0, 10); }
  function weekday(iso) { return (new Date(iso + "T12:00:00Z").getUTCDay() + 6) % 7; }  // Mon = 0
  function label(iso) {
    var d = new Date(iso + "T12:00:00Z");
    return d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
  }
  function dayNums(s) { return (s || "").split(",").map(function (d) { return DAYS.indexOf(d.trim().slice(0, 3)); }).filter(function (n) { return n >= 0; }); }

  var calendar = getJSON(base + "calendar.json").catch(function () { return {}; });
  var streets = null;
  function streetIndex() {
    if (!streets) streets = getJSON(base + "search.json").catch(function () { return []; });
    return streets;
  }

  // next pickup on or after today for a "Mon, Thu" schedule, skipping days 311 says are suspended
  function nextPickup(days, cal, today) {
    var nums = dayNums(days), skipped = [];
    for (var i = 0; i < 14; i++) {
      var iso = addDays(today, i);
      if (nums.indexOf(weekday(iso)) < 0) continue;
      var t = (cal[iso] || [])[2];
      if (t && t[0] === "suspended") { skipped.push(iso + "|" + (t[1] || "")); continue; }
      return { iso: iso, delayed: t && t[0] === "delayed", skipped: skipped };
    }
    return null;
  }

  function line(name, days, cal, today) {
    if (!days) return "";
    var nx = nextPickup(days, cal, today), when = "";
    if (nx) {
      var rel = nx.iso === today ? "today" : nx.iso === addDays(today, 1) ? "tomorrow" : label(nx.iso);
      when = " — next: <b>" + esc(rel) + "</b>" + (nx.delayed ? " (may be delayed)" : "");
      if (nx.skipped.length) when += '<span class="muted"> · no pickup ' + nx.skipped.map(function (s) {
        var p = s.split("|"); return esc(label(p[0]) + (p[1] ? " (" + p[1] + ")" : ""));
      }).join(", ") + "</span>";
    }
    return '<li><b>' + name + "</b> " + esc(days.replace(/, /g, " · ")) + when + "</li>";
  }

  function show(feature, area, cal, idx) {
    var p = feature.properties, today = nycIso(new Date()), tomorrow = addDays(today, 1);
    var trashNext = nextPickup(area.freq_refuse, cal, today);
    var tonight = trashNext && trashNext.iso === tomorrow
      ? '<p class="tonight">🗑️ Trash pickup is tomorrow: set it out tonight after 6 PM in a lidded bin, or bags after 8 PM (10+ unit buildings). Everything out by midnight.</p>' : "";
    var street = (p.street || "").toLowerCase().replace(/\s+/g, " ").trim(), boro = (p.borough || "").toLowerCase();
    var match = idx.filter(function (r) { return r[0].toLowerCase() === street && r[1].toLowerCase() === boro; })[0];
    var asp = match ? '<p><a href="' + base + match[2] + '">Street-cleaning (ASP) times on ' + esc(match[0]) + " →</a></p>" : "";
    out.innerHTML = '<h2>' + esc(p.label.replace(/, USA$/, "")) + "</h2>" + tonight +
      '<ul class="pickup-days">' + line("Trash", area.freq_refuse, cal, today) + line("Recycling", area.freq_recycling, cal, today) +
      line("Compost", area.freq_organics, cal, today) + line("Bulk items", area.freq_bulk, cal, today) + "</ul>" + asp +
      '<p class="fine">DSNY section ' + esc(area.section || "") + ". Holidays and snow change pickup days; this uses the city's 311 calendar for the next two weeks.</p>";
    out.hidden = false;
  }

  function lookup(text) {
    out.hidden = false;
    out.innerHTML = '<p class="muted">Looking up ' + esc(text) + "…</p>";
    getJSON(GEOSEARCH + "search?size=1&text=" + encodeURIComponent(text)).then(function (g) {
      var f = g.features && g.features[0];
      if (!f) throw new Error("no address");
      var c = f.geometry.coordinates;
      var q = AREAS + "?$select=section,freq_refuse,freq_recycling,freq_organics,freq_bulk&$where=" +
        encodeURIComponent("intersects(multipolygon,'POINT(" + c[0] + " " + c[1] + ")')");
      return Promise.all([getJSON(q), calendar, streetIndex()]).then(function (r) {
        if (!r[0].length) throw new Error("no area");
        try { localStorage.setItem("pickup-address", text); } catch (e) {}
        show(f, r[0][0], r[1], r[2]);
      });
    }).catch(function (e) {
      out.innerHTML = '<p>Couldn\'t find pickup days for that address. Try the house number and street, like "1225 Boston Rd, Bronx".</p>';
    });
  }

  var timer = null;
  input.addEventListener("input", function () {
    clearTimeout(timer);
    var text = input.value.trim();
    if (text.length < 4) return;
    timer = setTimeout(function () {
      getJSON(GEOSEARCH + "autocomplete?size=5&text=" + encodeURIComponent(text)).then(function (g) {
        list.innerHTML = (g.features || []).map(function (f) {
          return '<option value="' + esc(f.properties.label.replace(/, USA$/, "")) + '">';
        }).join("");
      }).catch(function () {});
    }, 250);
  });
  form.addEventListener("submit", function (ev) { ev.preventDefault(); if (input.value.trim()) lookup(input.value.trim()); });

  // ?address=... from the home page form, or the last address this browser looked up
  var start = new URLSearchParams(location.search).get("address");
  if (!start) { try { start = localStorage.getItem("pickup-address"); } catch (e) {} }
  if (start) { input.value = start; lookup(start); }
})();
