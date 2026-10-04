function labelFor(element) {
  return [
    element.getAttribute("aria-label") || "",
    element.getAttribute("title") || "",
    element.getAttribute("data-testid") || "",
    element.textContent || ""
  ].join(" ").trim().toLowerCase();
}

function findVoiceButton() {
  const buttons = [...document.querySelectorAll("button")];
  const candidates = buttons.map(button => ({button, label: labelFor(button)}));

  const active = candidates.find(({label}) =>
    /(end|stop|leave|exit).*(voice|sprach|stimme)|(voice|sprach|stimme).*(end|stop|leave|exit)/i.test(label)
  );
  if (active) return {alreadyActive: true, button: null};

  const preferred = candidates.find(({label}) =>
    /(start|open|enter|begin).*(voice|sprach|stimme)|(voice mode|sprachmodus|stimmenmodus)/i.test(label)
  );
  if (preferred) return {alreadyActive: false, button: preferred.button};

  const fallback = candidates.find(({label}) =>
    /(voice|sprachmodus|stimme)/i.test(label) &&
    !/(dictat|transcrib|microphone|mic|stop|end|leave|exit)/i.test(label)
  );
  return {alreadyActive: false, button: fallback ? fallback.button : null};
}

browser.runtime.onMessage.addListener(async message => {
  if (!message || message.action !== "hey-tabby-activate-voice") return undefined;

  for (let attempt = 0; attempt < 12; attempt++) {
    const match = findVoiceButton();
    if (match.alreadyActive) return {ok: true, result: "already-active"};
    if (match.button) {
      match.button.click();
      return {ok: true, result: "clicked"};
    }
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  return {ok: false, result: "voice-button-not-found"};
});
