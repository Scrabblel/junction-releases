# Junction

**All your PC games in one place.** Junction is a free game launcher for Windows. It puts the games from Steam, Epic Games, GOG Galaxy, Ubisoft Connect, EA app, Battle.net, Rockstar Games and Xbox in one library. It starts, installs and uninstalls them through each game's own launcher, and it compares prices across the official stores.

This is Junction's source code. **To just use Junction, download it from [junction-releases](https://github.com/scrabblel/junction-releases/releases/latest).** That page also explains what Junction does and how to use it.

## Run it from the source

You need Windows 10 or 11 (64-bit) and [Python](https://www.python.org/downloads/). Junction is made with Python 3.14.

```
pip install pywebview pythonnet
python Junction.pyw
```

It uses the same cache (`%LOCALAPPDATA%\Junction`) and settings (`%APPDATA%\Junction`) as an installed Junction, and only one of them can run at a time. To keep a separate copy, with its own settings, set `JUNCTION_HOME` to an empty folder first.

A few things need keys. Junction asks for them in the app, under your profile picture → **Accounts and keys**:

- **Prices:** an [IsThereAnyDeal](https://isthereanydeal.com/apps/my/) API key. Released builds come with one, but it isn't in this repository. Without a key, the Store has no prices.
- **Your full Steam library, achievements and friends:** a [Steam Web API key](https://steamcommunity.com/dev/apikey) per account.
- **Missing covers:** a [SteamGridDB](https://www.steamgriddb.com/profile/preferences/api) API key (optional).

When you run from the source, a `local_settings.json` next to `Junction.pyw` is used instead of the normal settings, if it exists. That keeps a developer's own keys apart from the installed app. Git ignores that file; see `junction/config.py`.

## Build the installer

```
pip install pyinstaller pillow
python tools/build.py
```

This checks the code, builds a new person's Store as a test, makes `dist/Junction/Junction.exe` with PyInstaller, and then `dist/JunctionSetup-<version>.exe` with [Inno Setup 6](https://jrsoftware.org/isinfo.php). The version comes from `VERSION` in `junction/config.py`. Use `--exe-only` to skip the installer and `--skip-store-check` to build offline.

If `JUNCTION_ITAD_KEY` is set (or `itadApiKey` is in `local_settings.json`), the build puts that IsThereAnyDeal key into the app. Without it, the built app asks each person for their own key.

## How it fits together

Junction is a [pywebview](https://pywebview.flowrl.com/) window. The whole interface is one page, `web/index.html`, and it talks to Python through `window.pywebview.api` (the `Api` class in `junction/app.py`).

| Where | What |
|---|---|
| `junction/app.py` | The window and the API the page calls |
| `junction/launchers.py` | Finds installed games: the launchers' registry entries, manifests and databases |
| `junction/library.py` | Builds the library: installed and owned games, play time, achievements |
| `junction/owned_local.py`, `steam_owned.py` | Games you own but haven't installed |
| `junction/steam_switch.py` | Switches Steam to the account that owns a game |
| `junction/epic_account.py`, `ea_account.py`, `ubisoft_account.py`, `signins.py` | Optional launcher sign-ins, for play time and achievements |
| `junction/live_store.py`, `stores.py` | Store prices, price history, free games and wishlists |
| `junction/running.py`, `playtime.py` | Which game is running, and play time for games no launcher counts |
| `junction/pad.py`, `titlebar.py`, `tray.py`, `startup.py`, `updates.py` | Controller input, the window's title bar, the tray icon, starting with Windows, updates |
| `web/index.html` | The interface: every page, layout and style |
| `tools/` | The build script, and the exports that feed the page when it runs outside the app |
| `installer/`, `Junction.iss`, `Junction.spec` | The installer's art and settings, and the PyInstaller settings |
| `design/` | Earlier style experiments |

The Epic, EA and Ubisoft sign-ins use the launchers' own unofficial web services, the way [Playnite](https://github.com/JosefNemec/Playnite), [Heroic](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher) and the GOG Galaxy plugins do. API keys and sign-ins are stored encrypted with Windows' per-user protection (DPAPI). Junction never sends them anywhere except to the service they belong to.

## Problems and ideas

Please open an issue on [junction-releases](https://github.com/scrabblel/junction-releases/issues). In the app, your profile picture → **Help → Problem report** gives a report you can paste in, with keys and sign-ins already removed.

## License

Junction is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License, version 3**, as published by the Free Software Foundation. It comes with no warranty. See [LICENSE](LICENSE) for the full text.

Junction isn't affiliated with Valve, Epic Games, GOG, Ubisoft, Electronic Arts, Blizzard, Rockstar Games, Microsoft or IsThereAnyDeal. Their names and logos belong to them.
