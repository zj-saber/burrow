# Burrow

A tabbed file manager for Windows, written in Python with Qt (PySide6).

## Features

- **Browsing**: address bar with path completion (`%USERPROFILE%` and `This PC` work), back/forward/up (also on mouse buttons), natural sort ("file2" comes before "file10"), folders first, and show/hide hidden files.
- **Type to filter**: start typing in any folder to filter it by name. Backspace removes a character, Esc clears the filter.
- **File operations**: cut, copy and paste (the clipboard works with Explorer), rename in place with F2 or a slow click, delete to the Recycle Bin, permanent delete, new folder or text file, Copy to / Move to, and drag and drop.
- **TeraCopy integration**: paste, drag and drop, Copy to and Move to all hand the transfer to TeraCopy when it's installed. It is found automatically, or you can set its path in Settings. **Paste with built-in copier** skips TeraCopy for one paste. Duplicating an item in its own folder always uses the built-in copier.
- **View presets for every folder**: one column layout (columns, order, widths, sort, folders first) applies to every folder and tab. The built-in **Details + Dates** preset shows Date created and Date modified. Save your own layout with *View ▸ View presets ▸ Save current view as preset…*, or right-click a column header.
- **Tabs**: Ctrl+T, Ctrl+W, Ctrl+Shift+T reopens a closed tab, middle-click a folder to open it in a new tab, and tabs are restored when you start Burrow again.
- **Bookmarks sidebar** (Ctrl+D), plus common places and drives. Drop files on a sidebar entry to copy or move them there, or drop folders on "Bookmarks" to bookmark them.
- **Inspector** (Alt+P): previews images and text files and shows file details: created, modified and accessed dates, attributes, and folder contents.

## Run

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
