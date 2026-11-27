# Burrow

A tabbed file manager for Windows, written in Python with Qt (PySide6).

## Features

- **Browsing**: address bar with path completion (`%USERPROFILE%` and `This PC` work), back/forward/up (also on mouse buttons), natural sort ("file2" comes before "file10"), folders first, and show/hide hidden files.
- **Mixed or folders-first sorting**: the *Sort* switch at the bottom right puts folders on top, or sorts files, folders and archives together purely by the column you sorted on (FilePilot-style). It applies to every folder.
- **Columns view** (optional, like macOS Finder or One Commander): the columns icon at the bottom (next to list, thumbnails and river), View ▸ Columns view, or Ctrl+Shift+L. Each folder you pick opens in a new column to the right. This PC, Network and tag pages stay a list. Columns have no scroll bars, for a compact look (the wheel, Shift+wheel and arrow keys scroll).
- **Icon size slider** at the bottom right: 16 to 64 px icons in the list and columns, for every tab and window, remembered.
- **Themes**: Burrow, Primary, Greyscale, Sepia, OLED black and Calm (low contrast, little blue light), each in light and dark.
- **Window material** (Windows 11): Mica, Mica Alt or Acrylic behind the window, with a light/medium/strong tint. Windows draws it, so it costs almost nothing.
- **Keyboard first**: Windows Explorer's shortcuts, ← from the list into the sidebar (↑/↓ browse as you go), F6 between areas.
- **Delete without the question**: tick *Don't ask again* in the delete dialog; turn it back on in Settings.
- **Type to filter**: start typing in any folder to filter it by name. Backspace removes a character, Esc clears the filter.
- **File operations**: cut, copy and paste (the clipboard works with Explorer), rename in place with F2 or a slow click, delete to the Recycle Bin, permanent delete, new folder or text file, Copy to / Move to, and drag and drop.
- **TeraCopy integration**: paste, drag and drop, Copy to and Move to all hand the transfer to TeraCopy when it's installed. It is found automatically, or you can set its path in Settings. **Paste with built-in copier** skips TeraCopy for one paste. Duplicating an item in its own folder always uses the built-in copier.
- **Column layouts**: right-click a column header (or View ▸ Columns) ▸ *Save layout for all folders* makes the current columns, order, widths and sort the default everywhere; *Save layout for this folder* gives one folder its own layout, kept across restarts (*Use the shared layout here* undoes it). *Fit columns to content* sizes each column to its widest item; turn it off and your own widths come back.
- **View presets for every folder**: one column layout (columns, order, widths, sort, folders first) applies to every folder and tab. The built-in **Details + Dates** preset shows Date created and Date modified. Save your own layout with *View ▸ View presets ▸ Save current view as preset…*, or right-click a column header.
- **Tabs**: Ctrl+T, Ctrl+W, Ctrl+Shift+T reopens a closed tab, middle-click a folder to open it in a new tab, and tabs are restored when you start Burrow again.
- **Split panes** (FilePilot-style), up to 6 per window: drag a tab out of its tab bar and drop it on any pane's right, left, top or bottom edge to snap it there, or on a pane's middle to move it into that pane. Side by side and stacked mix freely, so you can build two columns with the right one stacked, a 2×2 grid, and so on. Also View ▸ Split side by side (Ctrl+\), Split stacked (Ctrl+Shift+\), Rotate split (Ctrl+Alt+\, turns side by side into stacked and back), Move tab to next pane (Ctrl+Alt+M), Single pane, or right-click a tab. The address bar follows the active pane and the whole layout is restored next time.
- **Eject**: USB sticks, memory cards, external disks and DVDs get a small ⏏ button in the sidebar (and Eject in the right-click menu, also on This PC). Burrow moves its own tabs off the drive first, then safely removes it like *Safely Remove Hardware*. On network drives the same button disconnects.
- **Storage at a glance**: a skinny pill at the bottom right shows how full the current drive is, and This PC shows the same pill for every drive (it turns red past 90%).
- **Compare & sync** (Tools ▸ Compare & sync folders…, FreeFileSync-style, for backups): compare two folders by time and size or by content, then **Mirror →** (make the right an exact copy), **Update →** (copy new and changed files, delete nothing) or **Two-way ↔**. Every row shows what will happen and can be changed (right-click, or double-click to cycle). Skip patterns, recent folder pairs, and deleted files go to the Recycle Bin. With two panes open it starts with their folders.
- **Find duplicates** (Tools ▸ Find duplicates…): groups identical files by size, then a quick hash, then a full hash, so large folders are fast. Ticks every copy but the oldest (or newest, or shortest path) and moves them to the Recycle Bin, always keeping one.
- **Bulk rename** (select files, then F2 or Ctrl+Shift+F2): find & replace (plain or regular expression), a name pattern with `{name}`, `{n}` (counter with start, step and digits), `{date}` and `{parent}`, and upper/lower/title case, with a live preview that shows clashes in red before anything changes.
- **Clipboard at a glance**: copied items get a faint cyan tint, cut items yellow, and items being pasted green (with Burrow's copier). It follows the Windows clipboard, so files copied in Explorer show too.
- **Folders for loose files** (toolbar, Tools menu or Ctrl+Alt+F): puts each file in a folder named after it, and files that share a name go together (extra tags such as `.en` or `-poster` after the name still count as the same name). Works on the selected files, or every file in the folder; shows the folders first.
- **Updates from inside Burrow**: with Auto update on (Settings ▸ General) Burrow asks GitHub for the latest release every time it starts, and when you bring its window up if it hasn't looked for 12 hours. When there's a new one an *Update* button appears at the bottom right: one click downloads it, restarts Burrow on the new version and keeps your settings. Right-click it to hide it (Settings ▸ Sidebar & list brings it back). The update icon at the bottom right (or *Tools ▸ Check for updates…*) checks right away: a new version brings up the prompt to update now or later.
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
- **Extract and compress** from the right-click menu: *Extract here*, *Extract to "name\"* (each archive into its own folder) and *Add to archive…*. Burrow uses NanaZip or 7-Zip (their own progress, password and Add dialogs), else WinRAR, else Windows' built-in tar.
- **Archive contents**: select a .zip, .rar, .7z, .tar.*, .iso or similar file to see what's inside (folder tree, sizes, compression) without extracting. Burrow uses 7-Zip/NanaZip when installed, otherwise Windows' built-in tar.
- **Windows integration** (Settings ▸ Integrations, or Tools): *Open folders in Burrow* (desktop, Explorer, Run, shortcuts open as tabs) and *Open Burrow with Win+E* are separate switches, both off until you turn them on. Win+E works while Burrow runs; *Keep Burrow running in the background* adds a tray icon and starts it with Windows so Win+E always works. *Mirror bookmarks to Explorer Quick Access* pins/unpins bookmarked folders in Explorer. Folder colors are applied with Windows' own folder-settings call, so Explorer shows them immediately.
- **Every feature has a switch** in Settings (General · Preview · Sidebar & list · Integrations): each preview type, syntax colors, arrow-key seeking, tags, Explorer folder icons, each sidebar section, single-window mode and more.
- **Folder sizes from Everything (voidtools)**: when Everything is running, folder sizes fill the Size column a few milliseconds after a folder opens. You can sort by them, and they show in the inspector and status bar. Burrow talks to Everything's built-in IPC directly, so no SDK DLL is needed. Turn on *Index folder size* in Everything (*Tools ▸ Options ▸ Indexes*). You can switch it off in Settings.
- **Sizes in columns view**: a small size after each name: MB, or GB / TB for big items. Folder sizes come from Everything (switch in Settings ▸ Sidebar & list).
- **Safe like Explorer**: opening files goes through Windows' own checks (SmartScreen and the "downloaded from the internet" warning), extracted files keep that mark, names Windows reserves (CON, NUL, COM1…) are refused, deleting goes to the Recycle Bin, and updates are installed only from this repository's releases after their SHA-256 matches the one GitHub publishes.
- **Toolbar**: New, Cut, Copy, Paste, Rename, Extract, Delete, Sort, View and the tools, in one row above the address bar, like Explorer. The chevron at the end of the address bar hides it. Extract has its own key: Ctrl+Shift+X.
- **Hover highlight**: a soft rounded band under the mouse, like FilePilot; Settings ▸ General ▸ Hover highlight makes it lighter, stronger or off.
- **Type to search, Everything-fast**: start typing in any folder (list or columns view) and Burrow searches it and all its subfolders through Everything, with Everything's syntax (several words, `*`, `ext:`). Esc goes back. Without Everything, typing filters the folder as before.
- **Columns view works like the list**: Space plays video, F2 renames in place, drag and drop, Enter goes into folders, Ctrl+wheel icon size, mouse back/forward, tag dots.
- **Thumbnails and River views**: a picture grid, or photos at one height in flowing rows, each as wide as its picture with a small name along the bottom (River, for photo folders). Windows' own thumbnails, made in the background; the icon-size slider scales them (View ▸ Thumbnails / River, Ctrl+Shift+7 / 8).
- **Columns that keep their place**: the column you're in stays third from the left, older ones slide away, and searching shows its results beside the columns instead of replacing them.
- **Progress at a glance**: a skinny pill next to the item count while Burrow copies, moves, extracts or zips (hover it for the details); it fills as the copy goes, and glides while an archiver works in its own window.
- **Folder contents in the preview**: select a folder and the preview pane lists what's inside (folders first, with sizes); double-click to open. Settings ▸ Preview ▸ Folder contents.
- **Crash log**: if Burrow ever crashes or hits an error, the reason is kept in `%APPDATA%\Burrow\crash.log`, so it can be fixed.
- **Quick Look**: Space shows a big preview of the selected file, like macOS: photos and PDFs zoomable, video and music playing, text, and Office files: with Office installed, Windows' own Word / Excel / PowerPoint / Outlook previewer (the one in Explorer's preview pane); without it, Burrow reads .docx (text, headings, tables), .xlsx (a table per sheet) and .pptx (each slide's text) itself. Arrows move to the next file, Enter opens it, Space closes. On video and music Space instead plays and pauses them right in the preview pane.
- **Cloud sync status**: OneDrive, iCloud, Dropbox… items show Explorer's markers (cloud = online-only, green check = on this device, solid = always kept), read from the files without downloading anything.
- **Save view**: the toolbar's Save view remembers a folder's view (list, columns, thumbnails or river, with its columns and sort), or makes it the default everywhere.
- **Folder sizes remembered**: sizes from Everything are kept between runs and show the moment a folder opens.
- **Preview column**: in columns view the selected file is previewed in the last column, like macOS Finder: its picture, kind, size and details, video and music playing right there, with Open and Quick Look. The side pane steps aside in columns view and comes back in the other views.
- **Studio colors**: paper-white surfaces, hairline borders and a soft blue selection, in light and dark (Settings ▸ General ▸ Colors).
- **Light on resources**: in the tray Burrow gives its memory back to Windows (about 1 MB resident, 56 MB committed) and uses no CPU. Drives are noticed through Windows' device notifications instead of polling, the photo/PDF viewer and EPUB reader load on first use, and the install leaves out Qt parts it never loads (86 MB).
- **Never freezes on the network**: network folders, share lists, free space and reconnecting a remembered drive all happen in the background, so a slow or offline server shows "Loading…" or an error instead of hanging the window. A folder that can't be reached is retried by itself (2, 4, 8… up to 30 s) and appears as soon as the server answers, and an open network folder is kept connected so Windows doesn't drop it after 10 idle minutes (switch in Settings).
- **Light and dark themes** in two color sets: Burrow (cool blue) and **Primary**, a warm palette inspired by the Obsidian theme of the same name. Pick them in *Settings ▸ Colors*. They follow Windows light/dark by default, browser-style tabs, a clickable breadcrumb path, and the menus behind the ☰ button.

## Works with

Burrow finds these on its own when they're installed, and works without any of them. Nothing is bundled; each one is used only for what's listed.

| App | What Burrow uses it for | License |
|---|---|---|
| [Everything](https://www.voidtools.com/) (voidtools) | Folder sizes in the list, columns view, inspector and status bar, read from its index in milliseconds | Freeware |
| [TeraCopy](https://www.codesector.com/teracopy) | Copy and move (paste, drag and drop, Copy to / Move to) | Freeware, Pro is paid |
| [NanaZip](https://github.com/M2Team/NanaZip) | Extract here / Extract to folder / Add to archive, and archive contents in the inspector | Open source (MIT) |
| [7-Zip](https://www.7-zip.org/) | The same as NanaZip, when 7-Zip is the one installed | Open source (LGPL) |
| [WinRAR](https://www.win-rar.com/) | Extract and add to archive, when neither NanaZip nor 7-Zip is installed | Shareware |
| tar ([libarchive](https://github.com/libarchive/libarchive)), built into Windows | Archive contents and extracting when no archiver is installed | Open source (BSD) |
| [Windows Terminal](https://github.com/microsoft/terminal) | Open terminal here | Open source (MIT) |
| [PowerShell](https://github.com/PowerShell/PowerShell) | Open terminal here, when Windows Terminal isn't installed | Open source (MIT) |
| OneDrive, iCloud, Dropbox, Google Drive, Box… | Shown in the sidebar and This PC, with their own icons (found through Windows' sync-root registry) | Their own |
| Windows image codecs (HEIF, AVIF, JPEG XL, RAW Image Extension) | Previews of photos Qt can't read by itself | Microsoft Store, free |
| [Blackmagic RAW Player](https://www.blackmagicdesign.com/products/blackmagicraw) | Opening .braw clips (Burrow shows their details) | Free |
| FolderPainter-style folder colors | Folder colors are written to `desktop.ini` the same way, so both tools and Explorer show them | — |
| Explorer Quick Access | Bookmarks can be mirrored there (pinned and unpinned with them) | Part of Windows |
| [GitHub Releases](https://github.com/zj-saber/burrow/releases) | Updates, checked against the SHA-256 that GitHub publishes for each download | — |

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
| Ctrl+\ / Ctrl+Shift+\ / Ctrl+F6 | Split side by side / stacked / next pane |
| Ctrl+Alt+\ / Ctrl+Alt+M | Rotate split / move tab to the next pane |
| F2 (several selected) / Ctrl+Shift+F2 | Bulk rename |
| Ctrl+, | Settings |
