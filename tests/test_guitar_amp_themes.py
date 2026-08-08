"""The v0.2 guitar-amp theme family is registered and switchable."""

from tui.app import GIGBUDDY_THEME, GUITAR_AMP_THEME_NAMES, GigBuddyApp


def test_gigbuddy_is_the_default_and_restored_themes_are_registered():
    app = GigBuddyApp(spawn_engine=False)

    assert GIGBUDDY_THEME.name == "gigbuddy"
    assert app.theme == "gigbuddy"
    assert GUITAR_AMP_THEME_NAMES == (
        "gigbuddy",
        "orange-tolex",
        "tweed-brass",
        "diamond-noir",
        "blackface-silver",
        "british-green-oxblood",
        "surf-cream-coral",
    )
    assert all(name in app.available_themes for name in GUITAR_AMP_THEME_NAMES)


def test_next_theme_cycles_only_the_guitar_amp_family():
    app = GigBuddyApp(spawn_engine=False)
    for expected in GUITAR_AMP_THEME_NAMES[1:] + GUITAR_AMP_THEME_NAMES[:1]:
        app.action_next_theme()
        assert app.theme == expected
