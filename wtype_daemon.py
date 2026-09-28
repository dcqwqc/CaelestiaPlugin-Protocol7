import os
import subprocess
import time

FIFO = "/tmp/protocol7_wtype.fifo"
if not os.path.exists(FIFO):
    os.mkfifo(FIFO)

fd = os.open(FIFO, os.O_RDWR)
with os.fdopen(fd, 'r') as f:
    for line in f:
        if line.strip() == "QUIT_DAEMON":
            break
        # Strip the delimiter: it marks the end of an utterance
        text = line.rstrip("\r\n")
        if text:
            try:
                # User prefers the typing animation for all text lengths
                subprocess.run(["wtype", text], check=True)
            except (OSError, subprocess.CalledProcessError) as error:
                print(f"Protocol7: wtype failed: {error}", flush=True)
