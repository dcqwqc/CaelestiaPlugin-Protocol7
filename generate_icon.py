import sys
import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(APP_DIR)

from tray import create_image
img = create_image()
img.save(os.path.join(APP_DIR, "app.ico"), format="ICO", sizes=[(64, 64)])
