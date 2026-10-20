# shared/uix/widgets/table_cells.py
# -*- coding: utf-8 -*-
"""
Painted table cells (a coloured chip, an avatar + name) for big lists.

A QWidget per cell (QLabel + stylesheet + layout, four or five per row) made the Customers list take seconds to
open once a few hundred customers existed. A delegate paints the same look straight onto the item, so a row is a
few plain items and the list opens at once. Columns that show a delegate-painted chip report their real text
width, so ResizeToContents never clips a digit.
"""
from __future__ import annotations

from PySide2.QtCore import QRectF, QSize, Qt
from PySide2.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide2.QtWidgets import QStyle, QStyledItemDelegate

from .cards import TONES, _AVATAR_COLORS, initials

ROLE_TONE = Qt.UserRole + 1          # chip tone name
ROLE_SUB = Qt.UserRole + 2           # second line under the name
ROLE_BADGE = Qt.UserRole + 3         # small chip on the right of the name (e.g. "Supplier")


def _chip_font(base: QFont) -> QFont:
    f = QFont(base)
    f.setPointSizeF(max(base.pointSizeF() - 1, 7.5))
    f.setBold(True)
    return f


class ChipDelegate(QStyledItemDelegate):
    """Item text drawn as a rounded coloured chip (tone in ROLE_TONE)."""
    PAD_X, PAD_Y = 12, 3

    def sizeHint(self, option, index):
        text = str(index.data(Qt.DisplayRole) or "")
        fm = QFontMetrics(_chip_font(option.font))
        return QSize(fm.horizontalAdvance(text) + 2 * self.PAD_X + 14, 54)

    def paint(self, painter: QPainter, option, index):
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight().color().lighter(185))
        text = str(index.data(Qt.DisplayRole) or "")
        bg, fg, border = TONES.get(str(index.data(ROLE_TONE) or "grey"), TONES["grey"])
        font = _chip_font(option.font)
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(text) + 2 * self.PAD_X
        h = fm.height() + 2 * self.PAD_Y
        r = QRectF(option.rect.left() + 7, option.rect.center().y() - h / 2.0, w, h)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(r, h / 2.0, h / 2.0)
        painter.setFont(font)
        painter.setPen(QColor(fg))
        painter.drawText(r, Qt.AlignCenter, text)
        painter.restore()


class NameDelegate(QStyledItemDelegate):
    """Round initials avatar, bold name, grey second line, optional badge chip."""
    SIZE = 34

    def sizeHint(self, option, index):
        fm = QFontMetrics(option.font)
        return QSize(self.SIZE + 24 + fm.horizontalAdvance(str(index.data(Qt.DisplayRole) or "")), 54)

    def paint(self, painter: QPainter, option, index):
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight().color().lighter(185))
        name = str(index.data(Qt.DisplayRole) or "")
        sub = str(index.data(ROLE_SUB) or "")
        badge = str(index.data(ROLE_BADGE) or "")
        rect = option.rect
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        d = self.SIZE
        circle = QRectF(rect.left() + 8, rect.center().y() - d / 2.0, d, d)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_AVATAR_COLORS[sum(map(ord, name or "?")) % len(_AVATAR_COLORS)]))
        painter.drawEllipse(circle)
        af = QFont(option.font)
        af.setBold(True)
        af.setPointSizeF(max(option.font.pointSizeF() - 1, 7.5))
        painter.setFont(af)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(circle, Qt.AlignCenter, initials(name))

        right = rect.right() - 8
        if badge:
            bg, fg, border = TONES.get("blue", TONES["grey"])
            bf = _chip_font(option.font)
            bfm = QFontMetrics(bf)
            bw = bfm.horizontalAdvance(badge) + 20
            bh = bfm.height() + 4
            br = QRectF(right - bw, rect.center().y() - bh / 2.0, bw, bh)
            painter.setPen(QPen(QColor(border), 1))
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(br, bh / 2.0, bh / 2.0)
            painter.setFont(bf)
            painter.setPen(QColor(fg))
            painter.drawText(br, Qt.AlignCenter, badge)
            right = int(br.left()) - 6

        x = int(circle.right()) + 10
        text_w = max(right - x, 10)
        nf = QFont(option.font)
        nf.setBold(True)
        nfm = QFontMetrics(nf)
        painter.setFont(nf)
        painter.setPen(QColor("#111827"))
        if sub:
            top = QRectF(x, rect.top() + 7, text_w, nfm.height())
            painter.drawText(top, Qt.AlignLeft | Qt.AlignVCenter, nfm.elidedText(name, Qt.ElideRight, text_w))
            sf = QFont(option.font)
            sf.setPointSizeF(max(option.font.pointSizeF() - 1.5, 7))
            sfm = QFontMetrics(sf)
            painter.setFont(sf)
            painter.setPen(QColor("#9ca3af"))
            low = QRectF(x, top.bottom() + 1, text_w, sfm.height())
            painter.drawText(low, Qt.AlignLeft | Qt.AlignVCenter, sfm.elidedText(sub, Qt.ElideRight, text_w))
        else:
            painter.drawText(QRectF(x, rect.top(), text_w, rect.height()), Qt.AlignLeft | Qt.AlignVCenter,
                             nfm.elidedText(name, Qt.ElideRight, text_w))
        painter.restore()
