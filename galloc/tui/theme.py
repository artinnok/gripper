from textual.theme import Theme


MUTED_COLOR = "#7a7263"
SELECTION_BACKGROUND = "#33302a"
SELECTION_FOREGROUND = "#fbf1d7"
DONE = "[$success]✓[/]"
CURRENT = "[$primary]❯[/]"

AMBER_THEME = Theme(
    name="galloc-amber",
    primary="#f0b44c",
    accent="#f0b44c",
    foreground="#ddd3bd",
    background="#1c1b19",
    surface="#1c1b19",
    panel=SELECTION_BACKGROUND,
    success="#a9b665",
    warning="#e78a4e",
    error="#ea6962",
    dark=True,
    variables={
        "muted": MUTED_COLOR,
        "block-cursor-background": SELECTION_BACKGROUND,
        "block-cursor-foreground": SELECTION_FOREGROUND,
        "block-cursor-text-style": "none",
        "block-cursor-blurred-background": SELECTION_BACKGROUND,
        "block-cursor-blurred-foreground": SELECTION_FOREGROUND,
    },
)

APP_CSS = """
Screen {
    background: $background;
    padding: 1 2;
}

#header {
    height: 2;
}

DataTable {
    width: 1fr;
    background: $background;
    scrollbar-size: 0 0;
}

DataTable:focus {
    background-tint: transparent;
}

DataTable > .datatable--header, DataTable:focus > .datatable--header {
    background: $background;
    background-tint: transparent;
    color: $muted;
    text-style: none;
}

#detail {
    width: 25%;
    padding: 0 0 0 2;
    border-left: solid $panel;
}

#key-source {
    width: 1fr;
    padding-right: 2;
}

#info, #preview, #progress, #note, #startup, #path-label, Message {
    height: auto;
}

#prompt, #api-key, #limit, #choice {
    margin: 1 0;
}

#columns {
    height: auto;
    margin-top: 1;
    color: $muted;
}

AddScreen #choice {
    height: 1fr;
    margin-top: 0;
}

#note {
    color: $muted;
}

#keys {
    dock: bottom;
    height: 1;
    color: $muted;
}

Input, Input:focus {
    background: $background;
    background-tint: transparent;
}

OptionList, OptionList:focus {
    background: $background;
    background-tint: transparent;
}
"""
