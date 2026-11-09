#!/usr/bin/env python3
"""Build dist/Cisco-Meraki-WLAN-Snapshot.zip: the app files only, never snapshots."""
import os
import stat
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "Cisco-Meraki-WLAN-Snapshot"
FILES = ["server.py", "wlan_snapshot.py", "app.html", "report_template.html", "README.md",
         "Start WLAN Snapshot (Mac).command", "Start WLAN Snapshot (Windows).bat"]


def main():
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    out = os.path.join(HERE, "dist", NAME + ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            info = zipfile.ZipInfo.from_file(os.path.join(HERE, f), arcname="%s/%s" % (NAME, f))
            if f.endswith(".command"):   # keep the Mac launcher double-clickable
                info.external_attr = (stat.S_IFREG | 0o755) << 16
            with open(os.path.join(HERE, f), "rb") as fh:
                z.writestr(info, fh.read(), zipfile.ZIP_DEFLATED)
    print(out)


if __name__ == "__main__":
    main()
