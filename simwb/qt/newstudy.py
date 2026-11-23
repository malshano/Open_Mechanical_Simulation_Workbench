"""The first step of a new study: where to save it. A study is a folder that holds everything (the model, the setup, the mesh, the results), so the
person chooses a name and the folder to put it in, sees the folder it will be, and presses Create; the study then opens on the Geometry step, where
the 3D model is imported. Opened without blocking (QDialog.open), so the control API and Claude can fill and press it like a person."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

from .. import project as P
from . import theme
from .ids import wid


class NewStudyDialog(QDialog):
    def __init__(self, parent, study_label: str, name: str, folder: str, example: str = ""):
        super().__init__(parent)
        self.setObjectName("newstudy.dialog")
        self.setWindowTitle(f"New study: {study_label}")
        self.setModal(True)
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        v.setSpacing(10)
        intro = QLabel("A study is one folder with everything in it: the model, the setup, the mesh and the results. Choose where to keep it. "
                       + (f"The example model ({example.replace('.step', '').replace('_', ' ')}) is imported for you."
                          if example else "Once it is created you import your 3D model in the Geometry step."))
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        v.addWidget(intro)
        form = QFormLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        v.addLayout(form)
        self.name = wid(QLineEdit(name), "newstudy.name")
        self.name.setMaxLength(120)
        self.name.setToolTip("The name of the study (also the name of its folder)")
        self.name.textChanged.connect(self._update)
        form.addRow(QLabel("Name"), self.name)
        row = QHBoxLayout()
        self.folder = wid(QLineEdit(folder), "newstudy.folder")
        self.folder.setToolTip("The folder to save the study in (it is made if it does not exist)")
        self.folder.textChanged.connect(self._update)
        browse = wid(QPushButton("Browse…"), "newstudy.browse")
        browse.setObjectName("ghostBorder")
        browse.setToolTip("Choose the folder")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        form.addRow(QLabel("Save in"), row)
        self.path = wid(QLabel(""), "newstudy.path")
        self.path.setObjectName("muted")
        self.path.setWordWrap(True)
        self.path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.path)
        self.error = wid(QLabel(""), "newstudy.error")
        self.error.setWordWrap(True)
        self.error.setVisible(False)
        v.addWidget(self.error)
        br = QHBoxLayout()
        br.addStretch(1)
        cancel = wid(QPushButton("Cancel"), "newstudy.cancel")
        cancel.setToolTip("Do not create a study")
        cancel.clicked.connect(self.reject)
        self.ok = wid(QPushButton("Create study"), "newstudy.create")
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.setToolTip("Make the study in that folder and open it")
        self.ok.clicked.connect(self._create)
        br.addWidget(cancel)
        br.addWidget(self.ok)
        v.addLayout(br)
        self._update()
        self.name.selectAll()
        self.name.setFocus()

    def target(self) -> str:
        """The folder the study will be (a number is added by the engine when that name is taken)."""
        f = self.folder.text().strip()
        return str(Path(f) / P.slug(self.name.text())) if f else ""

    def _update(self, *_):
        t = self.target()
        taken = bool(t) and Path(t).exists()
        self.path.setText(("It will be saved as: " + t + ("  (that folder exists: a number is added)" if taken else "")) if t else "")
        self.error.setVisible(False)

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Save the study in", self.folder.text() or str(P.WORKSPACE))
        if d:
            self.folder.setText(d)

    def _fail(self, text: str):
        self.error.setText(f"<span style='color:{theme.tokens()['bad_text']}'><b>✖ {text}</b></span>")
        self.error.setVisible(True)

    def values(self) -> tuple:
        return self.name.text().strip(), self.folder.text().strip()

    def _create(self):
        name, folder = self.values()
        if not name:
            return self._fail("Give the study a name")
        if not folder:
            return self._fail("Choose the folder to save it in")
        try:
            P.check_folder(folder)                      # (made if it is missing; must be writable)
        except Exception as e:  # noqa: BLE001
            return self._fail(str(e))
        self.accept()
