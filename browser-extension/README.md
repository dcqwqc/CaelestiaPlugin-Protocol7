# Hey Tabby Zen Voice Bridge

This tiny Firefox/Zen WebExtension is the disposable browser-specific part of
the companion. Protocol7 focuses Zen and sends Ctrl+Alt+Shift+V. The extension
then:

1. prefers a pinned chatgpt.com tab (the dedicated Companion conversation),
2. focuses that tab/window,
3. asks its ChatGPT content script to find the Voice control by accessible
   labels/test IDs,
4. clicks that control, or leaves an already-active Voice session alone.

It does not read conversation messages and does not use screen coordinates.

For development, load this directory as a temporary add-on from Zen's
about:debugging page. A signed/permanent package can replace it later without
changing Protocol7.
