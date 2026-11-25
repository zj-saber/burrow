# Burrow

A tabbed file manager for Windows, written in Python with Qt (PySide6).

## Features

- **Browsing**: address bar with path completion (`%USERPROFILE%` and `This PC` work), back/forward/up (also on mouse buttons), natural sort ("file2" comes before "file10"), folders first, and show/hide hidden files.
- **Mixed or folders-first sorting**: the *Sort* switch at the bottom right puts folders on top, or sorts files, folders and archives together purely by the column you sorted on (FilePilot-style). It applies to every folder.
- **Columns view** (optional, like macOS Finder or One Commander): *View: list / columns* at the bottom right, View ▸ Columns view, or Ctrl+Shift+L. Each folder you pick opens in a new column to the right. This PC, Network and tag pages stay a list. Columns have no scroll bars, for a compact look (the wheel, Shift+wheel and arrow keys scroll).
- **Icon size slider** at the bottom right: 16 to 64 px icons in the list and columns, for every tab and window, remembered.
- **Themes**: Burrow, Primary, Greyscale, Sepia, OLED black and Calm (low contrast, little blue light), each in light and dark.
- **Window material** (Windows 11): Mica, Mica Alt or Acrylic behind the window, with a light/medium/strong tint. Windows draws it, so it costs almost nothing.
- **Keyboard first**: Windows Explorer's shortcuts, ← from the list into the sidebar (↑/↓ browse as you go), F6 between areas.
- **Delete without the question**: tick *Don't ask again* in the delete dialog; turn it back on in Settings.
- **Type to filter**: start typing in any folder to filter it by name. Backspace removes a character, Esc clears the filter.
- **File operations**: cut, copy and paste (the clipboard works with Explorer), rename in place with F2 or a slow click, delete to the Recycle Bin, permanent delete, new folder or text file, Copy to / Move to, and drag and drop.
- **TeraCopy integration**: paste, drag and drop, Copy to and Move to all hand the transfer to TeraCopy when it's installed. It is found automatically, or you can set its path in Settings. **Paste with built-in copier** skips TeraCopy for one paste. Duplicating an item in its own folder always uses the built-in copier.
- **View presets for every folder**: one column layout (columns, order, widths, sort, folders first) applies to every folder and tab. The built-in **Details + Dates** preset shows Date created and Date modified. Save your own layout with *View ▸ View presets ▸ Save current view as preset…*, or right-click a column header.
- **Tabs**: Ctrl+T, Ctrl+W, Ctrl+Shift+T reopens a closed tab, middle-click a folder to open it in a new tab, and tabs are restored when you start Burrow again.
- **Split panes** (FilePilot-style): drag a tab down out of its tab bar and drop it on the right, left, top or bottom edge to snap it into a split; drop it on the other pane to move it. Also View ▸ Split side by side (Ctrl+\), Split stacked (Ctrl+Shift+\), Move tab to other pane (Ctrl+Alt+M), Switch pane (F6), Single pane, or right-click a tab. The address bar follows the active pane and the split is restored next time.
- **Multiple windows**: Ctrl+N, a tab's right-click ▸ Open in new window, and Win+E (Settings: Win+E opens a new window or a new tab).
- **EPUB books** in the inspector: cover, title/author/publisher, and a chapter reader (‹ › chapters, Ctrl+wheel text size, ⤢ large reader).
- **Collapsible details**: click *Details* in the inspector to fold the file information away and give the preview the space. Drag the line under a photo, PDF or video to make the preview taller or shorter; Burrow remembers the height.
- **Bookmarks sidebar** (Ctrl+B), plus common places and drives. Each section collapses: click a heading, or use the arrow beside This PC and Network. Burrow remembers which sections you closed. Drag a heading up or down to reorder the sections (right-click a heading to reset the order). Drop files on a sidebar entry to copy or move them there, or drop folders on "Bookmarks" to bookmark them.
- **Inspector** (Alt+P): plays video and audio (Space plays or pauses, ← / → seek 5 s and Shift+← / → seek 30 s; video opens on a poster frame, audio shows cover art and tags), previews images and any text file (YAML, JSON, INI, TOML, XML, code, logs, subtitles... with syntax colors) and shows file details: created, modified and accessed dates, attributes, and folder contents.
- **Cloud drives**: OneDrive, iCloud Drive/Photos, Dropbox, Google Drive, Box and others are found through Windows' sync-root registry and shown in the sidebar and in This PC, with each provider's own icon. Files that are only in the cloud are never downloaded just to preview them.
- **Network drives**: mapped drives show up (including remembered ones that are disconnected; opening one reconnects it), along with a **Network** page. Type `\\computer` to see its shared folders. Computers you open are remembered in the sidebar. *Tools ▸ Map network drive…* and *Disconnect…* use the standard Windows dialogs.
- **Color tags**: right-click ▸ Tags to put macOS-style color tags (Red, Orange, Yellow, Green, Blue, Purple, Gray) on any file or folder. They show as dots next to the name, and the sidebar's Tags section lists everything with a given color. Tags follow renames, moves and copies done in Burrow.
- **Folder colors**: right-click a folder ▸ Folder color. The color is written to `desktop.ini` the way FolderPainter does it, so Windows Explorer shows it too. Choosing Default restores the folder's original icon.
- **Zoomable preview**: photos and PDFs open in the inspector with zoom. Use the scroll wheel, two-finger touchpad scrolling or pinch, drag to pan, and double-click to toggle fit / 100%. PDFs have page buttons and PgUp/PgDn, and ⤢ (or Enter) opens a large viewer.
- **RAW photos**: CR2, CR3, NEF, ARW, DNG, RAF, ORF, RW2 and more preview at full size from the camera's embedded JPEG (or Windows' RAW codec), zoomable, with camera, lens, exposure and date. HEIC/AVIF/JPEG XL preview through Windows' codecs.
- **Blackmagic RAW (.braw)**: clip details (resolution, frame rate, duration, codec). Frames need Blackmagic's decoder, so a double-click opens the clip in Blackmagic RAW Player.
- **Archive contents**: select a .zip, .rar, .7z, .tar.*, .iso or similar file to see what's inside (folder tree, sizes, compression) without extracting. Burrow uses 7-Zip/NanaZip when installed, otherwise Windows' built-in tar.
- **Windows integration** (Settings ▸ Integrations, or Tools): *Open folders in Burrow* (desktop, Explorer, Run, shortcuts open as tabs) and *Open Burrow with Win+E* are separate switches, both off until you turn them on. Win+E works while Burrow runs; *Keep Burrow running in the background* adds a tray icon and starts it with Windows so Win+E always works. *Mirror bookmarks to Explorer Quick Access* pins/unpins bookmarked folders in Explorer. Folder colors are applied with Windows' own folder-settings call, so Explorer shows them immediately.
- **Every feature has a switch** in Settings (General · Preview · Sidebar & list · Integrations): each preview type, syntax colors, arrow-key seeking, tags, Explorer folder icons, each sidebar section, single-window mode and more.
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

Burrow uses Windows Explorer's keys by default (*Settings ▸ General ▸ Keyboard shortcuts* switches back to Burrow classic).

| Keys | Action |
|---|---|
| Ctrl+N / Ctrl+T | New window / new tab |
| Ctrl+W / Ctrl+F4 | Close tab |
| Ctrl+Tab / Ctrl+Shift+Tab | Next / previous tab |
| Ctrl+L / Alt+D / F4 | Edit address |
| Ctrl+F / Ctrl+E / F3 | Filter (search this folder) |
| Alt+Left / Alt+Right / Alt+Up / Backspace | Back / Forward / Up / Back |
| Alt+Home | Home |
| F5 / Ctrl+R | Refresh |
| F6 / Shift+F6 | Move between address bar, sidebar and panes |
| Ctrl+Shift+E | Go to the sidebar |
| ← (in the list) | Go to the sidebar; ↑/↓ browse there, → or Esc come back |
| Enter / Ctrl+Enter | Open / open folder in a new window |
| Ctrl+Shift+Enter | Open folder in a new tab |
| F2 | Rename |
| Del / Ctrl+D | Move to the Recycle Bin |
| Shift+Del | Delete permanently |
| Ctrl+C / Ctrl+X / Ctrl+V / Ctrl+A | Copy / cut / paste / select all |
| Ctrl+Shift+C | Copy path |
| Ctrl+Shift+N | New folder |
| Ctrl+Shift+1 … 4 | Icons 64 / 48 / 32 / 16 px |
| Ctrl+Shift+6 / Ctrl+Shift+L | List view / toggle columns view |
| Ctrl+mouse wheel | Icon size |
| Alt+P / Alt+Shift+P | Inspector / its details |
| Alt+Enter | Properties |
| F11 | Full screen |
| Ctrl+H | Show hidden files |
| Ctrl+B | Bookmark folder |
| Ctrl+Shift+V | Paste with TeraCopy |
| Ctrl+\ / Ctrl+Shift+\ / Ctrl+F6 | Split side by side / stacked / switch pane |
| Ctrl+, | Settings |
