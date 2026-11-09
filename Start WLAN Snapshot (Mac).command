#!/bin/bash
# Double-click to start Cisco Meraki WLAN Snapshot. Close this window to stop it.
cd "$(dirname "$0")" || exit 1

# macOS always has a python3 placeholder, so check it actually runs (and is 3.8 or newer).
works() { "$1" -c 'import sys; sys.exit(sys.version_info < (3, 8))' >/dev/null 2>&1; }
PY=""
for p in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
  if works "$p"; then PY="$p"; break; fi
done

if [ -z "$PY" ]; then
  echo "Python 3 is needed to run the app, and it isn't installed on this Mac."
  echo "It's free. Apple's Command Line Tools include it."
  echo
  read -r -p "Install it now? [Y/n] " ans
  if [[ "$ans" =~ ^[Nn] ]]; then
    echo
    echo "No problem. Install Python 3 from https://www.python.org/downloads/ when you're ready,"
    echo "then double-click this launcher again."
  elif xcode-select --install >/dev/null 2>&1; then
    echo
    echo "An Apple installer window has opened. Click Install and wait for it to finish,"
    echo "then double-click this launcher again."
  else
    echo
    echo "Opening the Python download page in your browser..."
    open "https://www.python.org/downloads/macos/"
    echo "Install it, then double-click this launcher again."
  fi
  echo
  read -r -p "Press Return to close."
  exit 1
fi

echo "Starting Cisco Meraki WLAN Snapshot..."
echo "Your browser should open in a moment. Keep this window open while you use the app."
echo
"$PY" server.py --open
echo
read -r -p "The app has stopped. Any error is shown above. Press Return to close."
