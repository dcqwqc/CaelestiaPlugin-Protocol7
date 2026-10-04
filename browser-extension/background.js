async function companionTab() {
  const tabs = await browser.tabs.query({url: ["https://chatgpt.com/*"]});
  if (!tabs.length) {
    return browser.tabs.create({url: "https://chatgpt.com/", active: true});
  }
  const pinned = tabs.filter(tab => tab.pinned);
  const pool = pinned.length ? pinned : tabs;
  pool.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
  return pool[0];
}

async function sendActivate(tabId) {
  for (let attempt = 0; attempt < 8; attempt++) {
    try {
      const result = await browser.tabs.sendMessage(tabId, {action: "hey-tabby-activate-voice"});
      if (result && result.ok) return result;
    } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  return {ok: false, result: "content-script-unavailable"};
}

browser.commands.onCommand.addListener(async command => {
  if (command !== "activate-voice") return;
  const tab = await companionTab();
  if (!tab || tab.id == null) return;
  await browser.tabs.update(tab.id, {active: true});
  if (tab.windowId != null) await browser.windows.update(tab.windowId, {focused: true});
  await sendActivate(tab.id);
});
