from dataclasses import dataclass
from enum import StrEnum

from textual.markup import escape

from gripper.tui.theme import CURRENT, DONE


ANSWER_LABEL_WIDTH = 8


@dataclass(frozen=True)
class WizardStep[Step: StrEnum]:
    """Describe the prompt, the next step or branches, and where Back returns."""

    title: str
    keys: str = ""
    next_step: Step | None = None
    previous_step: Step | None = None
    choices: tuple[Step, ...] = ()


def render_progress[Step: StrEnum](
    steps: dict[Step, WizardStep[Step]], current: Step, answers: dict[Step, str], *, title: str | None = None
) -> str:
    """Show earlier answers in the wizard's display order, followed by the active prompt."""

    line_list = []
    for step, definition in steps.items():
        if step == current:
            break
        if step in answers:
            line_list.append(f"{DONE} {definition.title:<{ANSWER_LABEL_WIDTH}} {escape(answers[step])}")
    line_list.append(f"{CURRENT} {title or steps[current].title}")
    return "\n".join(line_list)
