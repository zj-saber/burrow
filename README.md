# Burrow

A tabbed file manager for Windows, written in Python with Qt (PySide6).

## Features

- **Browsing**: address bar with path completion (`%USERPROFILE%` and `This PC` work), back/forward/up (also on mouse buttons), natural sort ("file2" comes before "file10"), folders first, and show/hide hidden files.
- **Type to filter**: start typing in any folder to filter it by name. Backspace removes a character, Esc clears the filter.
- **File operations**: cut, copy and paste (the clipboard works with Explorer), rename in place with F2 or a slow click, delete to the Recycle Bin, permanent delete, new folder or text file, Copy to / Move to, and drag and drop.
- **TeraCopy integration**: paste, drag and drop, Copy to and Move to all hand the transfer to TeraCopy when it's installed. It is found automatically, or you can set its path in Settings. **Paste with built-in copier** skips TeraCopy for one paste. Duplicating an item in its own folder always uses the built-in copier.
- **View presets for every folder**: one column layout (columns, order, widths, sort, folders first) applies to every folder and tab. The built-in **Details + Dates** preset shows Date created and Date modified. Save your own layout with *View ▸ View presets ▸ Save current view as preset…*, or right-click a column header.
- **Tabs**: Ctrl+T, Ctrl+W, Ctrl+Shift+T reopens a closed tab, middle-click a folder to open it in a new tab, and tabs are restored when you start Burrow again.
- **Bookmarks sidebar** (Ctrl+D), plus common places and drives. Each section collapses: click a heading, or use the arrow beside This PC and Network. Burrow remembers which sections you closed. Drop files on a sidebar entry to copy or move them there, or drop folders on "Bookmarks" to bookmark them.
- **Inspector** (Alt+P): plays video and audio (Space plays or pauses; video opens on a poster frame, audio shows cover art and tags), previews images and text files and shows file details: created, modified and accessed dates, attributes, and folder contents.
- **Cloud drives**: OneDrive, iCloud Drive/Photos, Dropbox, Google Drive, Box and others are found through Windows' sync-root registry and shown in the sidebar and in This PC, with each provider's own icon. Files that are only in the cloud are never downloaded just to preview them.
- **Network drives**: mapped drives show up (including remembered ones that are disconnected; opening one reconnects it), along with a **Network** page. Type `\\computer` to see its shared folders. Computers you open are remembered in the sidebar. *Tools ▸ Map network drive…* and *Disconnect…* use the standard Windows dialogs.
- **Color tags**: right-click ▸ Tags to put macOS-style color tags (Red, Orange, Yellow, Green, Blue, Purple, Gray) on any file or folder. They show as dots next to the name, and the sidebar's Tags section lists everything with a given color. Tags follow renames, moves and copies done in Burrow.
- **Folder colors**: right-click a folder ▸ Folder color. The color is written to `desktop.ini` the way FolderPainter does it, so Windows Explorer shows it too. Choosing Default restores the folder's original icon.
- **Zoomable preview**: photos and PDFs open in the inspector with zoom. Use the scroll wheel, two-finger touchpad scrolling or pinch, drag to pan, and double-click to toggle fit / 100%. PDFs have page buttons and PgUp/PgDn, and ⤢ (or Enter) opens a large viewer.
- **Archive contents**: select a .zip, .rar, .7z, .tar.*, .iso or similar file to see what's inside (folder tree, sizes, compression) without extracting. Burrow uses 7-Zip/NanaZip when installed, otherwise Windows' built-in tar.
- **Default file manager** (off unless you turn it on: Tools ▸ Use Burrow as default file manager, or Settings): folders and drives opened anywhere in Windows, plus Win+E, open in Burrow as tabs in the running window. The Open/Save dialogs inside other apps can't be replaced, because Windows draws those inside each app.
- **Folder sizes from Everything (voidtools)**: when Everything is running, folder sizes fill the Size column a few milliseconds after a folder opens. You can sort by them, and they show in the inspector and status bar. Burrow talks to Everything's built-in IPC directly, so no SDK DLL is needed. Turn on *Index folder size* in Everything (*Tools ▸ Options ▸ Indexes*). You can switch it off in Settings.
- **Never freezes on the network**: network folders, share lists and free space load in the background, so a slow or offline server shows "Loading…" or an error instead of hanging the window.
- **Light and dark themes** in two color sets: Burrow (cool blue) and **Primary**, a warm palette inspired by the Obsidian theme of the same name. Pick them in *Settings ▸ Colors*. They follow Windows light/dark by default, browser-style tabs, a clickable breadcrumb path, and the menus behind the ☰ button.

## Install (Windows .exe)

```
.\build.ps1
.\install.ps1            # add -Desktop for a desktop shortcut
```

`build.ps1` makes `dist\Burrow\Burrow.exe` with PyInstaller. `install.ps1` copies it to `%LOCALAPPDATA%\Programs\Burrow`, adds a Start menu shortcut, and registers Burrow in *Settings ▸ Apps*, where it can be uninstalled. It installs for your user only, so no admin rights are needed.

## Run from source

```
run.bat [folder]
```

The first run creates a `.venv` and installs PySide6. Settings are saved in `%APPDATA%\Burrow\settings.json`.

## Shortcuts

| Keys | Action |
|---|---|
| Ctrl+L / Alt+D / F4 | Edit address |
| Alt+Left / Alt+Right / Alt+Up | Back / Forward / Up |
| Backspace | Back (or delete a filter character) |
| F2 | Rename |
| Del / Shift+Del | Recycle / Delete permanently |
| Ctrl+Shift+N | New folder |
| Ctrl+Shift+V | Paste with TeraCopy |
| Ctrl+Shift+C | Copy path |
| Ctrl+H | Toggle hidden files |
| Ctrl+D | Bookmark folder |
| Alt+Enter | Properties |
| Ctrl+, | Settings |
