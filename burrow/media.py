"""A small video/audio player for the inspector.

Qt Multimedia (FFmpeg backend) is imported only the first time a media file is previewed, so
it costs nothing at startup. Nothing autoplays: a video shows its first frame and waits.
Unloading releases the file right away so it can be renamed, moved or deleted.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QSizePolicy, QSlider, QToolButton,
                               QVBoxLayout, QWidget)

from . import theme

VIDEO_EXTS = set("mp4 m4v mkv webm mov avi wmv flv mpg mpeg m2ts mts ts 3gp ogv vob".split())
AUDIO_EXTS = set("mp3 m4a aac flac wav ogg oga opus wma aiff aif alac ape mka m4b".split())


def fmt_time(ms: int) -> str:
    s = max(0, int(ms // 1000))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


class _SeekSlider(QSlider):
    """Jumps to the clicked spot instead of paging."""

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self.maximum() > 0:
            x = ev.position().x()
            val = round(self.minimum() + (self.maximum() - self.minimum()) * x / max(1, self.width()))
            self.setValue(val)
            self.sliderMoved.emit(val)
        super().mousePressEvent(ev)


class MediaPreview(QWidget):
    """The inspector's video/audio player: poster frame, controls, cover art and tags."""
    metadataReady = Signal(dict)     # duration, title, artist, album, resolution, codecs...
    coverShown = Signal()
    failed = Signal(str)

    volume = 0.8                     # shared across files for the session

    def __init__(self, parent=None):
        super().__init__(parent)
        self._player = None
        self._audio = None
        self._video = None
        self._is_video = False
        self._poster_pending = False
        self._at_poster = False
        self._poster_ms = 0
        self._meta_sent = False
        self._path = None
        self._seek_target = None          # latest requested position while keys are held
        self._seek_timer = QTimer(self)
        self._seek_timer.setSingleShot(True)
        self._seek_timer.setInterval(40)
        self._seek_timer.timeout.connect(self._apply_seek)
        self._release_timer = QTimer(self)    # free the decoder soon after leaving a media file
        self._release_timer.setSingleShot(True)
        self._release_timer.setInterval(4000)
        self._release_timer.timeout.connect(self._release)

        self.cover = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.cover.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.cover.hide()

        self.play_btn = QToolButton()
        self.play_btn.setAutoRaise(True)
        self.play_btn.setToolTip("Play / pause")
        self.play_btn.clicked.connect(self.toggle)
        self.seek = _SeekSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self._seek_to)
        self.time = QLabel("0:00 / 0:00")
        self.time.setProperty("role", "key")
        self.mute_btn = QToolButton()
        self.mute_btn.setAutoRaise(True)
        self.mute_btn.setCheckable(True)
        self.mute_btn.setToolTip("Mute")
        self.mute_btn.toggled.connect(self._set_muted)
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(int(self.volume * 100))
        self.vol.setFixedWidth(64)
        self.vol.setToolTip("Volume")
        self.vol.valueChanged.connect(self._set_volume)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 2, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.seek, 1)
        controls.addWidget(self.time)
        controls.addWidget(self.mute_btn)
        controls.addWidget(self.vol)

        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.lay.addWidget(self.cover)
        self.lay.addLayout(controls)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.seek.setMinimumWidth(30)
        self.vol.setFixedWidth(48)
        self.apply_theme()
        self.hide()

    # ----- setup -------------------------------------------------------------------------
    def _ensure(self):
        """Create the player and video surface on first use (they're heavy)."""
        if self._player is not None:
            return
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(self.volume)
        self._player.setAudioOutput(self._audio)
        self._video = QVideoWidget(self)
        self._video.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self._video.setMinimumHeight(90)
        self._video.setStyleSheet("background: black;")
        self._video.hide()
        self.lay.insertWidget(0, self._video, 1)
        self._player.setVideoOutput(self._video)
        p = self._player
        p.positionChanged.connect(self._on_position)
        p.durationChanged.connect(self._on_duration)
        p.playbackStateChanged.connect(lambda *_: self._sync_play_icon())
        p.mediaStatusChanged.connect(self._on_status)
        p.metaDataChanged.connect(self._on_metadata)
        p.errorOccurred.connect(self._on_error)

    def apply_theme(self):
        self._sync_play_icon()
        self.mute_btn.setIcon(theme.icon("mute" if self.mute_btn.isChecked() else "volume"))

    # ----- public ------------------------------------------------------------------------
    def load(self, path: str, video: bool):
        """Open a video or song paused on its poster frame."""
        self._release_timer.stop()
        self._ensure()
        self.unload()
        self._path = path
        self._is_video = video
        self._meta_sent = False
        self._poster_pending = video
        self._at_poster = False
        self.seek.setRange(0, 0)
        self.time.setText("0:00 / 0:00")
        self._video.setVisible(video)
        if video:
            self._fit_video(QSize(16, 9))
        self.cover.hide()
        self.show()
        self._player.setSource(QUrl.fromLocalFile(path))

    def unload(self):
        if self._player is not None and self._path is not None:
            self._player.stop()
            self._player.setSource(QUrl())          # releases the file handle
        self._path = None
        self._seek_target = None
        self.hide()
        if self._player is not None:
            self._release_timer.start()

    def _release(self):
        """Drop the player, its decoder buffers and the video surface. Recreated in a few ms
        the next time a media file is previewed (the libraries stay loaded)."""
        if self._path is not None or self._player is None:
            return
        self._player.setVideoOutput(None)
        for obj in (self._player, self._audio, self._video):
            obj.deleteLater()
        self._player = self._audio = self._video = None

    def pause(self):
        if self._player is not None and self._path is not None:
            self._player.pause()

    def toggle(self):
        if self._player is None or self._path is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            if self._at_poster:                 # the poster frame is mid-video: start at 0
                self._at_poster = False
                self._player.setPosition(0)
            self._player.play()

    def seek_by(self, ms: int) -> bool:
        """Jump forward/back (arrow keys). Repeats while a key is held are coalesced: the UI
        moves at once, and the player seeks to the newest target every 40 ms at most."""
        if self._player is None or self._path is None:
            return False
        dur = self._player.duration()
        if dur <= 0:
            return True
        if self._at_poster:
            self._at_poster = False
            base = 0
        else:
            base = self._seek_target if self._seek_target is not None else self._player.position()
        target = max(0, min(dur - 250, base + ms))
        self._seek_target = target
        self.seek.setValue(target)
        self.time.setText(f"{fmt_time(target)} / {fmt_time(dur)}")
        if not self._seek_timer.isActive():
            self._seek_timer.start()
        return True

    def _apply_seek(self):
        if self._player is None or self._seek_target is None:
            return
        self._player.setPosition(self._seek_target)
        # keep the target until the player reports it, so the slider doesn't jump back
        QTimer.singleShot(120, self._seek_settled)

    def _seek_settled(self):
        if not self._seek_timer.isActive():
            self._seek_target = None

    def is_active(self) -> bool:
        return self._path is not None

    # ----- slots ---------------------------------------------------------------------------
    def _sync_play_icon(self):
        playing = False
        if self._player is not None:
            from PySide6.QtMultimedia import QMediaPlayer
            playing = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(theme.icon("pause" if playing else "play"))
        if self.play_btn.icon().isNull():
            self.play_btn.setText("❚❚" if playing else "▶")

    def _set_volume(self, v):
        MediaPreview.volume = v / 100
        if self._audio is not None:
            self._audio.setVolume(MediaPreview.volume)

    def _set_muted(self, on):
        if self._audio is not None:
            self._audio.setMuted(on)
        self.mute_btn.setIcon(theme.icon("mute" if on else "volume"))

    def _seek_to(self, ms):
        self._at_poster = False
        if self._player is not None:
            self._player.setPosition(ms)

    def _on_position(self, ms):
        if self._at_poster or self._poster_pending or self._seek_target is not None:
            return                              # parked on the poster, or a key seek is in flight
        if not self.seek.isSliderDown():
            self.seek.setValue(ms)
        self.time.setText(f"{fmt_time(ms)} / {fmt_time(self._player.duration())}")

    def _on_duration(self, ms):
        self.seek.setRange(0, max(0, ms))
        self.time.setText(f"{fmt_time(self._player.position())} / {fmt_time(ms)}")

    def _on_status(self, status):
        """Media loaded: take the poster frame (a frame a little way in) without playing."""
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._poster_pending:
            self._poster_pending = False
            # Poster frame without playing: like Explorer thumbnails, take a frame ~8% in
            # (videos often open on black), muted, and pause as soon as it is decoded.
            dur = self._player.duration()
            self._poster_ms = int(min(dur * 0.08, 60000)) if dur > 10000 else 0
            self._audio.setMuted(True)
            if self._poster_ms:
                self._player.setPosition(self._poster_ms)
            self._player.play()
            QTimer.singleShot(150, self._end_poster)

    def _end_poster(self):
        if self._player is None or self._path is None:
            return
        self._player.pause()
        self._at_poster = True
        self._audio.setMuted(self.mute_btn.isChecked())
        self.seek.setValue(0)
        self.time.setText(f"0:00 / {fmt_time(self._player.duration())}")

    def _fit_video(self, res: QSize):
        # the video now fills the inspector's preview area (its height is set by the
        # drag handle); QVideoWidget letterboxes to keep the aspect ratio
        self._video.setMinimumHeight(90)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self._video is not None and self._video.isVisible():
            self._fit_video(getattr(self, "_res", QSize(16, 9)))

    def _on_metadata(self):
        """Fill in resolution, length, codec, artist, album and cover art."""
        from PySide6.QtMultimedia import QMediaMetaData
        md = self._player.metaData()
        K = QMediaMetaData.Key
        res = md.value(K.Resolution)
        if self._is_video and isinstance(res, QSize) and res.isValid():
            self._res = res
            self._fit_video(res)
        cover = md.value(K.ThumbnailImage) or md.value(K.CoverArtImage)
        if not self._is_video and cover is not None and not cover.isNull():
            pm = QPixmap.fromImage(cover)
            side = max(96, min(self.width() or 260, 260))
            self.cover.setPixmap(pm.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation))
            self.cover.setFixedHeight(min(side, pm.height()) + 4)
            self.cover.show()
            self.coverShown.emit()
        if self._meta_sent:
            return
        info = {}

        def text(key):
            v = md.value(key)
            if isinstance(v, (list, tuple)):
                v = ", ".join(str(x) for x in v if x)
            return str(v) if v not in (None, "") else ""
        info["Title"] = text(K.Title)
        info["Artist"] = text(K.ContributingArtist) or text(K.AlbumArtist)
        info["Album"] = text(K.AlbumTitle)
        tn = md.value(K.TrackNumber)
        info["Track"] = str(tn) if tn else ""
        info["Genre"] = text(K.Genre)
        dur = md.value(K.Duration) or self._player.duration()
        info["Duration"] = fmt_time(dur) if dur else ""
        if self._is_video and isinstance(res, QSize) and res.isValid():
            fr = md.value(K.VideoFrameRate)
            info["Video"] = f"{res.width()} × {res.height()}" + (f" @ {float(fr):.3g} fps" if fr else "")
        # PySide can't convert the codec enums through value(); stringValue() works for any key.
        codecs = []
        for key in ((K.VideoCodec, K.AudioCodec) if self._is_video else (K.AudioCodec,)):
            try:
                name = md.stringValue(key)
            except Exception:                         # noqa: BLE001 - optional detail
                name = ""
            if name and name.lower() not in ("unspecified", "invalid"):
                codecs.append(name)
        info["Codecs"] = " / ".join(codecs)
        br = md.value(K.AudioBitRate) if not self._is_video else md.value(K.VideoBitRate)
        info["Bitrate"] = f"{int(br) // 1000} kbps" if br else ""
        info = {k: v for k, v in info.items() if v}
        if info.get("Duration"):
            self._meta_sent = True
            self.metadataReady.emit(info)

    def _on_error(self, _err, message):
        if self._path is not None:
            self.failed.emit(message or "This file can't be played.")
