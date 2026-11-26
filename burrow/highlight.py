"""A small, fast syntax highlighter for text previews (YAML, JSON, INI/TOML, XML, code...).

Deliberately simple: comments, keys, strings, numbers and booleans. Long files are only
colored near the top (HIGHLIGHT_LINES), so big logs and data files still open instantly.
"""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

HIGHLIGHT_LINES = 3000

KEY_STYLES = {
    # extension -> (comment markers, key regex)
    "yaml": ("#", r"^\s*(?:- )?([\w.\-\"' /]+?)(?=\s*:(?:\s|$))"),
    "yml": ("#", r"^\s*(?:- )?([\w.\-\"' /]+?)(?=\s*:(?:\s|$))"),
    "json": ("", r"(\"(?:[^\"\\]|\\.)*\")(?=\s*:)"),
    "jsonc": ("//", r"(\"(?:[^\"\\]|\\.)*\")(?=\s*:)"),
    "ini": (";#", r"^\s*([^=;#\[\]]+?)(?=\s*=)"),
    "cfg": (";#", r"^\s*([^=;#\[\]]+?)(?=\s*[=:])"),
    "conf": ("#;", r"^\s*([^=;#\[\]]+?)(?=\s*[=:])"),
    "toml": ("#", r"^\s*([\w.\-\"']+?)(?=\s*=)"),
    "properties": ("#!", r"^\s*([^=:#!\s]+)(?=\s*[=:])"),
    "env": ("#", r"^\s*(?:export\s+)?([A-Za-z_][\w]*)(?==)"),
    "reg": (";", r"^\s*(\"[^\"]*\"|@)(?==)"),
    "inf": (";", r"^\s*([^=;\[\]]+?)(?=\s*=)"),
}
CODE_COMMENTS = {"py": "#", "pyw": "#", "sh": "#", "bash": "#", "ps1": "#", "rb": "#", "r": "#",
                 "js": "//", "ts": "//", "tsx": "//", "jsx": "//", "c": "//", "h": "//", "cpp": "//",
                 "hpp": "//", "cs": "//", "java": "//", "go": "//", "rs": "//", "swift": "//",
                 "kt": "//", "dart": "//", "php": "//", "css": "", "scss": "//", "less": "//",
                 "lua": "--", "sql": "--", "bat": "rem ", "cmd": "rem "}


class PreviewHighlighter(QSyntaxHighlighter):
    """Light syntax colors for text previews (numbers, strings, comments, keys, tags)."""
    def __init__(self, doc, ext: str, colors: dict):
        super().__init__(doc)
        dim, accent = QColor(colors["dim"]), QColor(colors["accent"])
        dark = colors.get("name") == "dark"
        self.f_comment = QTextCharFormat()
        self.f_comment.setForeground(dim)
        self.f_comment.setFontItalic(True)
        self.f_key = QTextCharFormat()
        self.f_key.setForeground(QColor("#7cb7ff" if dark else "#1f5fbf"))
        self.f_section = QTextCharFormat()
        self.f_section.setForeground(accent)
        self.f_section.setFontWeight(QFont.Weight.DemiBold)
        self.f_string = QTextCharFormat()
        self.f_string.setForeground(QColor("#9ece6a" if dark else "#2e7d32"))
        self.f_number = QTextCharFormat()
        self.f_number.setForeground(QColor("#e0a46e" if dark else "#b5541c"))
        self.f_tag = QTextCharFormat()
        self.f_tag.setForeground(QColor("#c792ea" if dark else "#7b3fb5"))

        comments, key = KEY_STYLES.get(ext, (CODE_COMMENTS.get(ext, ""), ""))
        self.comment_markers = [comments] if len(comments) > 1 and not comments.isalpha() and comments in ("//", "--") \
            else ([comments] if comments.endswith(" ") else list(comments))
        self.key_re = QRegularExpression(key) if key else None
        self.section_re = QRegularExpression(r"^\s*\[[^\]]+\]\s*$") if ext in ("ini", "cfg", "toml", "inf", "reg", "conf") else None
        self.string_re = QRegularExpression(r"\"(?:[^\"\\]|\\.)*\"|'(?:[^'\\]|\\.)*'")
        self.number_re = QRegularExpression(r"(?<![\w.])-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?(?![\w.])"
                                            r"|\b(?:true|false|null|yes|no|on|off|True|False|None)\b")
        self.tag_re = QRegularExpression(r"</?[\w:.\-]+|/?>") if ext in ("xml", "html", "htm", "svg", "xaml", "plist", "vue", "svelte") else None

    def _apply(self, regex, text, fmt, group=0):
        it = regex.globalMatch(text)
        while it.hasNext():
            m = it.next()
            self.setFormat(m.capturedStart(group), m.capturedLength(group), fmt)

    def highlightBlock(self, text: str):
        """Color one line; very long files and lines are left plain to stay fast."""
        if self.currentBlock().blockNumber() > HIGHLIGHT_LINES or len(text) > 2000:
            return
        self._apply(self.number_re, text, self.f_number)
        self._apply(self.string_re, text, self.f_string)
        if self.tag_re is not None:
            self._apply(self.tag_re, text, self.f_tag)
        if self.key_re is not None:
            self._apply(self.key_re, text, self.f_key, 1)
        if self.section_re is not None and self.section_re.match(text).hasMatch():
            self.setFormat(0, len(text), self.f_section)
        # comments last: they win over everything on the rest of the line
        stripped = text.lstrip()
        for mark in self.comment_markers:
            if not mark:
                continue
            if mark.strip().isalpha():          # "rem " for batch files: only at line start
                if stripped.lower().startswith(mark):
                    self.setFormat(0, len(text), self.f_comment)
                return
            idx = text.find(mark)
            while idx >= 0:
                # skip markers inside strings (e.g. URLs "http://")
                if text.count('"', 0, idx) % 2 == 0 and text.count("'", 0, idx) % 2 == 0:
                    if mark != "#" or idx == 0 or text[idx - 1] in " \t":
                        self.setFormat(idx, len(text) - idx, self.f_comment)
                        return
                idx = text.find(mark, idx + 1)
