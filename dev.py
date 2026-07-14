import os
import subprocess
import sys

# To automatically restart run.py on code changes, run this script with watchmedo:
# First, install watchdog: pip install watchdog
# Then, run: watchmedo auto-restart -d . -p "*.py" -- python dev.py

os.environ["TELEGRAM_BOT_TOKEN"] = "8996267827:AAFr90VroINCcdtYPWX3ZWJ2m2vq3LxY-ao"
os.environ["DEV_MODE"] = "true"

print("Starting run.py with development token...")
# Use sys.executable to ensure the same python interpreter is used
subprocess.run([sys.executable, "run.py"])
