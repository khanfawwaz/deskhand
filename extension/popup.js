const $ = (id) => document.getElementById(id);

const TEXT = {
  connected: ["Connected to Deskhand", "Ready for your tasks."],
  pairing: ["Waiting for your approval", "Click Yes in the Deskhand window on your computer."],
  denied: ["Not allowed", "The request was declined. You can try again."],
  searching: ["Not connected", "Open the Deskhand app on this computer, then press Connect."],
  noapp: ["Deskhand app not found", "Install Deskhand on this computer, then press Try again. We opened the website for you."],
};
const SITE_URL = "https://deskhand.fawwazkhan.dev/app/";

async function refresh() {
  const r = await chrome.runtime.sendMessage({ type: "status" });
  const s = (r && r.state && r.state.status) || "searching";
  const paired = !!(await chrome.storage.local.get("token")).token;
  const [title, detail] = TEXT[s] || TEXT.searching;
  $("title").textContent = title;
  $("detail").textContent = " " + (s === "searching" && paired ? "Deskhand is not running on this computer." : detail);
  $("dot").className = "dot " + s;
  $("pair").hidden = paired || s === "pairing";
  $("pair").textContent = s === "noapp" ? "Try again" : "Connect to Deskhand";
  $("get").hidden = paired || s === "pairing" || s === "connected";            // not set up yet: always show where to get the app
  $("forget").hidden = !paired;
}

$("pair").onclick = async () => { await chrome.runtime.sendMessage({ type: "pair" }); refresh(); };
$("get").onclick = () => { chrome.tabs.create({ url: SITE_URL }); };
$("forget").onclick = async () => { await chrome.runtime.sendMessage({ type: "forget" }); refresh(); };
refresh();
setInterval(refresh, 1500);
