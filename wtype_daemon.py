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
                # Dynamic pasting speed
                if len(text) < 50:
                    # Small text: type it medium fast (wtype default)
                    subprocess.run(["wtype", text], check=True)
                else:
                    # Large text: paste via clipboard for instant speed
                    # Backup old clipboard safely
                    old_clip = b""
                    try:
                        old_clip = subprocess.check_output(["wl-paste", "--no-newline"], stderr=subprocess.DEVNULL)
                    except:
                        pass
                    
                    # Set new clipboard
                    subprocess.run(["wl-copy"], input=text.encode('utf-8'), check=True)
                    # Small delay to let compositor register clipboard
                    time.sleep(0.05)
                    # Send Ctrl+V
                    subprocess.run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], check=True)
                    
                    # Restore old clipboard (optional, but polite)
                    time.sleep(0.1)
                    if old_clip:
                        subprocess.run(["wl-copy"], input=old_clip)
                    else:
                        subprocess.run(["wl-copy", "-c"]) # clear
            except (OSError, subprocess.CalledProcessError) as error:
                print(f"Protocol7: wtype failed: {error}", flush=True)
