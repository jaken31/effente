"""Setup: a few facts about this machine, and its starting model (platform/CONTRACT.md
section 10, docs/scan-and-log-plan.md section 16).

Steps on one page, top to bottom: the local profile, then 1 questions, 2 the
machine profile, 3 the starting model and "Use this model", 4 the device's answer.

Everything comes from and goes to `state.setup` (INTERFACES.md addition 24). The
agent only fills the form. Code picks the model, and only "Use this model" stores
the profile and sends anything toward the device.
"""

from __future__ import annotations

from app.data import model_server
from app.theme import tokens as t
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from . import view
from .steps import DeviceAnswer, LocalProfile, ProfileForm, Questions, StartingModel

COLUMN_W = 980  # the steps' width: a form reads badly across a 1300px page


class SetupPage(QWidget):
    key = "setup"
    title = "Setup"
    subtitle = "a few facts about this machine, and its starting model"
    navigate = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = None
        self._saving_local = False

        self.local = LocalProfile()
        self.local.saved.connect(self.save_local)
        self.questions = Questions()
        self.questions.fill.connect(self.fill)
        self.profile = ProfileForm()
        self.profile.edited.connect(self.edit)
        self.model = StartingModel()
        self.model.use.connect(self.use)
        self.device = DeviceAnswer()

        column = QWidget()
        column.setMaximumWidth(COLUMN_W)
        col = QVBoxLayout(column)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(t.PAGE_GAP)
        for w in (self.local, self.questions, self.profile, self.model, self.device):
            col.addWidget(w)
        col.addStretch(1)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(column, 100)  # as wide as it may be; what is left is shared by the two sides
        row.addStretch(1)

    # ---- state ------------------------------------------------------------

    def bind(self, state) -> None:
        self.state = state
        s = state.setup
        s.profile_changed.connect(self.refresh)
        s.run_changed.connect(self.refresh)
        s.entry_changed.connect(self.refresh)
        s.local_profile_changed.connect(self._local_changed)
        state.snapshot_changed.connect(self.refresh)
        state.action_failed.connect(self._action_failed)
        if hasattr(state, "model_changed"):
            state.model_changed.connect(self.refresh)
        self.refresh()

    def _model_ready(self) -> tuple[bool, str]:
        status = getattr(self.state, "model_status", lambda: None)()
        ready = bool(status) and status.state == model_server.READY
        return ready, getattr(status, "model", "") if ready else ""

    # ---- actions ----------------------------------------------------------

    def fill(self, answers: list) -> None:
        if self.state is None or not self.state.current_machine() or self.state.setup.run_running():
            return
        ready, _ = self._model_ready()
        self.state.setup.start_run(answers, mode="local" if ready else "standin")

    def edit(self, fields: dict) -> None:
        if self.state is not None and self.state.current_machine():
            self.state.setup.set_draft(**fields)

    def use(self) -> None:
        if self.state is not None and not self._use_problem():
            self.state.setup.confirm()

    def save_local(self, name: str, role: str) -> None:
        if self.state is not None:
            self._saving_local = True
            self.state.setup.set_local_profile(name, role)

    def _local_changed(self) -> None:
        if self._saving_local:
            self._saving_local = False
            self.local.saved_ok()
        self.refresh()

    def _action_failed(self, _message: str) -> None:
        """The window shows the reason. What was typed stays."""
        self._saving_local = False

    # ---- drawing ----------------------------------------------------------

    def _use_problem(self) -> str:
        s = self.state.setup
        return view.use_problem(machine=self.state.current_machine(), choice=s.choice(), running=s.run_running(),
                                speed_problem=self.profile.problem, profile=s.profile(), draft=s.draft())

    def refresh(self, *_args) -> None:
        if self.state is None:
            return
        s = self.state.setup
        machine = self.state.current_machine()
        running = s.run_running()
        profile, draft = s.profile(), s.draft()

        self.local.set_local(s.local_profile(), "" if s.local_profile() else self.state.answerer())

        ready, model = self._model_ready()
        self.questions.set_mode(view.mode_note(ready, model) if machine else view.NO_MACHINE)
        self.questions.set_can_fill(bool(machine) and not running, running)
        self.questions.set_run(view.run(s.run(), running))

        self.profile.set_draft(draft, view.profile_hint(profile, draft), enabled=bool(machine) and not running)

        self.model.set_choice(s.choice())
        self.model.set_confirm(view.confirm_line(profile, draft), self._use_problem())
        self.model.set_confirmed(view.profile_hint(profile, draft) == view.CONFIRMED)

        self.device.set_device(view.device(s.wanted_entry(), s.device_entry(), bool(self.state.device_id())))
