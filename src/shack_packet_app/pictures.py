"""Pictures for sending: shrink and re-encode before they go, so a photo
from a phone (4 MB, 4000 pixels across) is not twenty minutes of 1200
baud, and say what each choice costs on the air before it is chosen.

Qt's own image code (QtGui, already in every bundle) does the work: no
new library. Re-encoding also leaves the original's metadata behind -
EXIF, including where a phone says the photo was taken - which is a
kindness on a mode anyone can read; "Original" sends the file untouched,
metadata and all, and the dialog says so.

What each choice costs on the air is airtime.py's (0.3.1: the transfer
followed frame by frame and over by over, at every mode, calibrated on
the air); 0.3.0's flat 66-68 % of the bit rate is gone.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PyQt6.QtGui import QImage, QImageReader, QPainter


@dataclass(frozen=True)
class Preset:
    key: str
    longest: int | None        # pixels on the longest side; None: as it is
    quality: int | None        # JPEG quality; None: the original file


#: Longest side and JPEG quality. "medium" is the default: a picture a
#: person can see properly, about 60-120 KB, a minute or two at 9600.
PRESETS = (
    Preset("original", None, None),
    Preset("large", 1600, 85),
    Preset("medium", 1024, 75),
    Preset("small", 640, 65),
    Preset("tiny", 320, 50),
)
PRESET = {p.key: p for p in PRESETS}

def is_picture(path: Path | str) -> bool:
    reader = QImageReader(str(path))
    return reader.canRead() and bytes(reader.format().data()).lower() in (b"jpeg", b"jpg", b"png", b"bmp",
                                                                         b"gif", b"webp", b"tiff", b"ppm")


def load(path: Path | str) -> QImage:
    """The picture the right way up (a phone's EXIF orientation applied)."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        raise ValueError(reader.errorString() or "not a picture")
    return image


def reencode(image: QImage, preset: Preset) -> bytes:
    """`image` scaled so its longest side is at most the preset's, as a
    JPEG at its quality. Never enlarged."""
    if preset.longest is None or preset.quality is None:
        raise ValueError("the original preset sends the file as it is")
    if max(image.width(), image.height()) > preset.longest:
        image = image.scaled(preset.longest, preset.longest, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    if image.hasAlphaChannel():
        flat = QImage(image.size(), QImage.Format.Format_RGB32)
        flat.fill(Qt.GlobalColor.white)
        p = QPainter(flat)
        p.drawImage(0, 0, image)
        p.end()
        image = flat
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buf, "JPEG", preset.quality):
        raise ValueError("the picture could not be encoded")
    buf.close()
    return bytes(data.data())


def jpeg_name(name: str) -> str:
    return Path(name).stem + ".jpg"


def options(path: Path | str) -> list[tuple[Preset, int, tuple[int, int]]]:
    """Each preset with the bytes it would send and the picture's size in
    pixels: [(preset, bytes, (w, h))]. The original's bytes are the file's."""
    image = load(path)
    out = [(PRESETS[0], Path(path).stat().st_size, (image.width(), image.height()))]
    for preset in PRESETS[1:]:
        data = reencode(image, preset)
        scaled = image
        if max(image.width(), image.height()) > preset.longest:
            scaled = image.scaled(preset.longest, preset.longest, Qt.AspectRatioMode.KeepAspectRatio)
        out.append((preset, len(data), (scaled.width(), scaled.height())))
    return out


def thumbnail(path: Path | str, size: int = 160) -> QImage | None:
    """A small copy for the Files page, or None if it is not a picture
    (only decoded, never run or opened in another program)."""
    try:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        if not reader.canRead():
            return None
        full = reader.size()
        if full.isValid() and max(full.width(), full.height()) > size:
            full.scale(size, size, Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(full)
        image = reader.read()
        return None if image.isNull() else image
    except Exception:                               # noqa: BLE001 - a damaged file is just not shown
        return None
