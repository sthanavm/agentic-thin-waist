// Proves the selection chain on the element sets actually measured on vm-4.
// Extracts pickVideo + videoOrigin from collect.py so the test runs the shipped
// source, not a copy that can drift from it.
const fs = require("fs");
const SRC = process.argv[2];
const src = fs.readFileSync(SRC, "utf8");
const origin = src.match(/function videoOrigin\(v\)[\s\S]*?\n}\n/)[0];
const pick = src.match(/function pickVideo\(\)[\s\S]*?\n}\n/)[0];

let fails = 0;
function check(name, got, want) {
  const ok = got === want;
  if (!ok) fails++;
  console.log(`  ${ok ? "PASS" : "FAIL"}  ${name}: ${got}${ok ? "" : "  (want " + want + ")"}`);
}

// A <video> whose srcObject carries the given track ids.
function mk(vw, vh, bw, bh, trackIds, label) {
  return {
    videoWidth: vw,
    videoHeight: vh,
    getBoundingClientRect: () => ({ width: bw, height: bh }),
    srcObject: trackIds
      ? { getTracks: () => trackIds.map((id) => ({ id, label: label || "" })) }
      : null,
    tag: `${vw}x${vh}@${bw}x${bh}`,
  };
}
function run(els, localIds) {
  global.document = { querySelectorAll: () => els };
  global.window = { __netgentLocalTrackIds: localIds || [] };
  const f = new Function(origin + pick + ";return {pickVideo, videoOrigin};")();
  return f;
}

// ---- the exact set measured in the 2026-10-04 sweep -----------------------
// A local 1280x720 fake camera in a 207x117 self-view thumbnail, and the remote
// peer downscaled to 320x180 in the 1458x820 main view. Size ranking picks the
// self-view; origin ranking must pick the main view.
console.log("case 1: self-view thumbnail vs remote main view");
{
  const selfView = mk(1280, 720, 207, 117, ["LOCAL1"], "fake_device_0");
  const remoteMain = mk(320, 180, 1458, 820, ["REMOTE1"], "");
  const els = [
    mk(0, 0, 0, 0, null),
    selfView,
    mk(320, 180, 207, 117, ["REMOTE1"], ""),
    remoteMain,
    mk(0, 0, 0, 0, null),
    mk(1280, 720, 0, 0, ["LOCAL1"], "fake_device_0"),
  ];
  const { pickVideo, videoOrigin } = run(els, ["LOCAL1"]);
  check("picks the remote main view", pickVideo().tag, "320x180@1458x820");
  check("self-view classified local", videoOrigin(selfView).local, true);
  check("remote classified not local", videoOrigin(remoteMain).local, false);
}

// ---- the degenerate set from cell 4: no remote view composited at all -----
// Nothing valid exists to measure. The picker still has to return something,
// but the abort check must see is_local true and refuse the cell.
console.log("case 2: only a local self-view is rendered");
{
  const els = [
    mk(1280, 720, 207, 117, ["LOCAL1"], "fake_device_0"),
    mk(1280, 720, 0, 0, ["REMOTE1"], ""),
    mk(0, 0, 0, 0, null),
  ];
  const { pickVideo, videoOrigin } = run(els, ["LOCAL1"]);
  const got = pickVideo();
  check("falls back to the only rendered element", got.tag, "1280x720@207x117");
  check("and reports it as local so the cell can abort", videoOrigin(got).local, true);
}

// ---- the earlier good layout ---------------------------------------------
console.log("case 3: earlier good layout (remote fills the window)");
{
  const els = [
    mk(1280, 720, 1756, 988, ["REMOTE1"], ""),
    mk(1280, 720, 207, 117, ["LOCAL1"], "fake_device_0"),
    mk(0, 0, 0, 0, null),
  ];
  const { pickVideo } = run(els, ["LOCAL1"]);
  check("picks the full-size remote view", pickVideo().tag, "1280x720@1756x988");
}

// ---- a plain file/MSE player (Vimeo) must not be excluded ----------------
// srcObject is null there, so videoOrigin().local is null, not false. If the
// filter required local === false this would break every non-conferencing app.
console.log("case 4: non-conferencing player (no MediaStream)");
{
  const els = [mk(1440, 1080, 1317, 988, null)];
  const { pickVideo, videoOrigin } = run(els, []);
  check("still selected", pickVideo().tag, "1440x1080@1317x988");
  check("origin is no-stream", videoOrigin(els[0]).kind, "no-stream");
  check("local is null not false", videoOrigin(els[0]).local, null);
}

// ---- pin behaviour -------------------------------------------------------
console.log("case 5: pin upgrades when the main view appears late");
{
  const thumb = mk(320, 180, 207, 117, ["REMOTE1"], "");
  const els = [thumb];
  global.document = { querySelectorAll: () => els };
  global.window = { __netgentLocalTrackIds: [] };
  const f = new Function(origin + pick + ";return {pickVideo};")();
  check("first pick is the thumbnail", f.pickVideo().tag, "320x180@207x117");
  els.push(mk(320, 180, 1458, 820, ["REMOTE1"], ""));
  check("upgrades to the main view", f.pickVideo().tag, "320x180@1458x820");
}

// ---- a self-view must never be pinned ------------------------------------
console.log("case 6: an existing pin on a self-view is abandoned");
{
  const selfView = mk(1280, 720, 900, 500, ["LOCAL1"], "fake_device_0");
  const remote = mk(640, 360, 800, 450, ["REMOTE1"], "");
  const els = [selfView, remote];
  global.document = { querySelectorAll: () => els };
  global.window = { __netgentLocalTrackIds: ["LOCAL1"], __netgentPinnedVideo: selfView };
  const f = new Function(origin + pick + ";return {pickVideo};")();
  // the self-view is excluded from the pool, so the stale pin cannot be returned
  check("returns the remote tile, not the pinned self-view", f.pickVideo().tag, "640x360@800x450");
}

console.log(fails ? `\n${fails} FAILURE(S)` : "\nall selection cases pass");
process.exit(fails ? 1 : 0);
