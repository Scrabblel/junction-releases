# Junction

**All your PC games in one place.** Junction is a free game launcher for Windows. It puts the games from all your launchers in one library, starts them for you, and watches prices in the official stores.

**[⬇ Download the latest version](https://github.com/scrabblel/junction-releases/releases/latest)** (the `JunctionSetup-….exe` file under *Assets*)

Junction is free and open source, under the GPL-3.0. The code is in this repository; see [Build it yourself](#build-it-yourself).

---

<img width="2559" height="1391" alt="image" src="https://github.com/user-attachments/assets/34512048-52e1-4a29-9327-0a5ffeb00594" />


---

## What it does

- **One library for every launcher.** Steam, Epic Games, GOG Galaxy, Ubisoft Connect, EA app, Battle.net, Rockstar Games and Xbox, plus any game you add yourself. Junction finds them by itself.
- **Play, install and uninstall** from one place. Junction does this through each game's own launcher, so your saves, updates and cloud sync keep working as before.
- **Several Steam accounts?** Junction knows which account owns each game and switches Steam over for you when you press Play.
- **Play time and achievements together:** play time from every launcher, and achievements from Steam, GOG, Epic, EA and Ubisoft.
- **A store that compares prices** across the official stores for your country, in your currency. It shows a year of price history, the lowest price ever, the free games the launchers are giving away, and a wishlist that tells you when a game goes on sale.
- **Stats** about what you play, **favourites** and your own **collections**.
- **Three layouts:** Console (big covers, made for a controller), Classic (your games in a list on the left) and Big screen (full screen, for your TV). Plus a few styles, in light or dark.
- **Full controller support**, as well as mouse and keyboard.

## Install

1. Download `JunctionSetup-<version>.exe` from the [latest release](https://github.com/scrabblel/junction-releases/releases/latest).
2. Run it. Windows may say *"Windows protected your PC"*, because Junction isn't signed yet (signing costs money). Click **More info → Run anyway**.
3. Junction installs for your Windows user only and doesn't need administrator rights.

You need Windows 10 or 11 (64-bit). Junction checks for a new version each time it starts and updates itself.

## Getting started

When Junction opens for the first time, a short setup shows which launchers and games it found, and asks two things:

- **Where you buy your games.** The Store shows prices for that country, in its currency. Junction picks the region set in Windows; type to choose another.
- **A Steam API key** (optional, only when you use Steam). It's free and you make it on Steam's site; Junction shows you how. With it you see your full Steam library, achievements and friends. Without it, you see your installed Steam games and the ones Steam remembers.

That's it: your games are ready to play.

## Using Junction

- **Home** shows the games you played last, with their news and achievements (in Classic, also the best deals for you). **Library** has all your games, which you can filter, sort and search. **Store**, **Wishlist** and **Stats** are in the bar at the top.
- **Open a game** for its page: Play or Install, play time, achievements, news, screenshots and details. When you own a game on more than one launcher, you choose which copy to start.
- **Your profile picture (top right)** holds everything else:
  - **Accounts and keys:** your Steam accounts and keys, and *Play time from launchers*. Sign in to Epic, EA or Ubisoft there to see the play time (and the achievements) those launchers keep.
  - **Preferences:** the look (layout, style and theme) and the country for prices.
  - **Rescan launchers**, after you install a new launcher or game.
  - **Help:** check for updates, and the problem report.
- **Add a game by hand** (in the Library) for anything Junction doesn't find, like a game you installed without a launcher: click **Browse…** and pick the game's program (.exe). Windows asks once, before it first starts, whether that program may run.
- **Closing the window** can keep Junction in the tray (you choose in the setup). That way it can count play time for launchers that don't keep it themselves.

### Shortcuts

| | Keyboard | Controller |
|---|---|---|
| Move around | Arrow keys | D-pad or left stick |
| Open | Enter | A |
| Back | Esc | B |
| Search and commands | Ctrl+K | Y |
| Settings | | ☰ (Menu) |
| Switch tabs | | LB / RB |
| Full screen | F11 | |
| Menu for a game | Right-click, or the menu key | X |

## Your privacy

Junction sends nothing to its maker: there's no account, no tracking and no statistics. Your API keys and launcher sign-ins stay on your PC, encrypted by Windows. Junction only talks to the services it needs: the launchers, Steam, IsThereAnyDeal (for prices), GitHub (to check for updates) and, if you add a key, SteamGridDB (for covers).

The Epic, EA and Ubisoft sign-ins work the same unofficial way as Playnite and Heroic do. If those companies change something, they can stop working until Junction is updated.

## Good to know

- **EA app, Rockstar Games and Battle.net:** Junction only sees the games you have installed, not the ones you own but haven't downloaded.
- **Achievements** from Ubisoft, Battle.net, Rockstar and Xbox aren't shown. EA doesn't share pictures or rarity, so EA achievements show a star.
- **Switching Steam accounts restarts Steam,** which takes about 15 seconds.
- **Junction is still young (version 0.9).** Expect the odd bug, and please report it.

## Found a problem?

[Open an issue](https://github.com/scrabblel/junction-releases/issues). Say what you did and what happened, and add a problem report: click your profile picture → **Help → Problem report → Copy**, then paste it in. Junction removes your keys and sign-ins from the report by itself. Screenshots help a lot.

## Uninstall

Windows **Settings → Apps → Installed apps → Junction → Uninstall**. Your games stay where they are: Junction never moves or removes them when you uninstall Junction.

## Build it yourself

Junction is written in Python, with the interface as one web page. You only need this to change Junction or build it yourself; to use it, [download the installer](https://github.com/scrabblel/junction-releases/releases/latest).

### Run it from the source

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

### Build the installer

```
pip install pyinstaller pillow
python tools/build.py
```

This checks the code, builds a new person's Store as a test, makes `dist/Junction/Junction.exe` with PyInstaller, and then `dist/JunctionSetup-<version>.exe` with [Inno Setup 6](https://jrsoftware.org/isinfo.php). The version comes from `VERSION` in `junction/config.py`. Use `--exe-only` to skip the installer and `--skip-store-check` to build offline.

If `JUNCTION_ITAD_KEY` is set (or `itadApiKey` is in `local_settings.json`), the build puts that IsThereAnyDeal key into the app. Without it, the built app asks each person for their own key.

### How it fits together

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

## License

Junction is free software: you can redistribute it and/or modify it under the terms of the **GNU General Public License, version 3**, as published by the Free Software Foundation. It comes with no warranty. See [LICENSE](LICENSE) for the full text.

Junction isn't affiliated with Valve, Epic Games, GOG, Ubisoft, Electronic Arts, Blizzard, Rockstar Games, Microsoft or IsThereAnyDeal. Their names and logos belong to them.
