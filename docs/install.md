# Installing and upgrading

*[&larr; back to the documentation](README.md)*

hamdash keeps **your settings, your log and your board in their own
folder**, separate from the program. Installing a new version replaces the
program and leaves that folder alone, so upgrading never loses a contact, a
radio or a tile. This was tested by starting from versions 0.5, 0.8 and 1.0
and upgrading to the current one: everything came through unchanged.

Where your settings live:

| | |
|---|---|
| Windows | `%APPDATA%\hamdash` (paste that into the File Explorer address bar) |
| macOS | `~/Library/Application Support/hamdash` |
| Linux | `~/.config/hamdash` |

**To back up**, copy that folder somewhere safe. To restore, copy it back
while hamdash is closed.

Every version is on the
[releases page](https://github.com/wizzardsl/hamdash/releases/latest).
Once hamdash is installed, **Settings → Help** says when a newer one is
out, with a button that opens that page.

## Windows

### Installing

1. On the [releases page](https://github.com/wizzardsl/hamdash/releases/latest),
   under **Assets**, download **`hamdash-setup-…exe`**.
2. Open it. Windows will probably say **"Windows protected your PC"**,
   because hamdash is not signed with a paid certificate. Click
   **More info**, then **Run anyway**.
3. Click through the installer. It installs for you only, so it does not
   need an administrator. Tick **Create a desktop shortcut** if you want
   one.
4. hamdash is now in the Start menu.

### Upgrading

Download the newer **`hamdash-setup-…exe`** and run it, exactly as above.
It finds the copy you already have and replaces it: same place, same
shortcuts, and your settings untouched. If hamdash is running, the installer
closes it first.

### Uninstalling

**Settings → Apps → Installed apps → hamdash → Uninstall**. This removes the
program and keeps your settings folder, so reinstalling later picks up where
you left off. To remove everything, delete `%APPDATA%\hamdash` as well.

### Using the zip instead

Versions up to 1.3.1 have no installer, only
`hamdash-windows-x64-….zip`. To use a zip:

1. Close hamdash.
2. Delete the old hamdash folder - only the program folder, not
   `%APPDATA%\hamdash`.
3. Right-click the new zip → **Extract All…**, and extract it to the same
   place the old folder was.
4. Open `hamdash.exe` inside it.

**Do not extract a new version over the top of an old one.** Files the new
version no longer uses would be left behind. Watch for one more thing:
**Extract All** names the folder after the zip, version number included, so a
desktop shortcut made for the old folder goes on opening the old version.
Make the shortcut again after upgrading, or switch to the installer.

## macOS

1. Download **`hamdash-macos-arm64-….zip`** (Apple-silicon Macs) and
   double-click it to unzip.
2. Move the `hamdash` folder somewhere permanent, for example your
   **Applications** folder.
3. Inside it, **right-click `hamdash` → Open**. The first time, macOS warns
   that it is from an unidentified developer; choose **Open**. After that, a
   double-click is enough.

**To upgrade**, close hamdash, delete the old `hamdash` folder, and unzip
the new one into the same place. Your settings are elsewhere and stay.

**Version 1.3.1 only:** that download will not start, because the file lost
its "may run" permission on the way through the build. Open **Terminal**,
type `chmod +x ` (with a space after it), drag the `hamdash` file from the
folder onto the Terminal window, and press Return. Later versions are fixed.

## Linux

1. Download **`hamdash-linux-x64-….zip`** and unzip it somewhere
   permanent, for example `~/hamdash`.
2. The window needs WebKitGTK, which a desktop Linux with GNOME or a GTK
   browser already has. If it is missing (`sudo apt install
   gir1.2-webkit2-4.1` on Ubuntu or Debian), hamdash still runs and opens
   in your web browser instead.
3. Run `~/hamdash/hamdash`.

**To upgrade**, close hamdash, delete the old folder, and unzip the new one
in its place.

## Going back to an older version

Install the older one the same way. Your settings folder stays. Older
versions have not been tested against a newer version's settings, so **back
up the folder first**: if anything looks wrong, close hamdash and copy the
backup back.

## Running from source

For anyone who would rather use `git` and Python (3.11 or later):

```bash
git clone https://github.com/wizzardsl/hamdash
cd hamdash
pip install -r requirements.txt
python run.py
```

To upgrade, `git pull` and `pip install -r requirements.txt` again. Your
settings are in the same folder as for the downloads, so you can switch
between the two.
