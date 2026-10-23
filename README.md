# Junction

**All your PC games in one place.** Junction is a free game launcher for Windows. It puts the games from all your launchers in one library, starts them for you, and watches prices in the official stores.

**[⬇ Download the latest version](https://github.com/scrabblel/junction-releases/releases/latest)** (the `JunctionSetup-….exe` file under *Assets*)

---

## What it does

- **One library for every launcher.** Steam, Epic Games, GOG Galaxy, Ubisoft Connect, EA app, Battle.net, Rockstar Games and Xbox, plus any game you add yourself. Junction finds them by itself.
- **Play, install and uninstall** from one place. Junction does this through each game's own launcher, so your saves, updates and cloud sync keep working as before.
- **Several Steam accounts?** Junction knows which account owns each game and switches Steam over for you when you press Play.
- **Play time and achievements together:** play time from every launcher, and achievements from Steam, GOG, Epic and EA.
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
  - **Accounts and keys:** your Steam accounts and keys, and *Play time from launchers*. Sign in to Epic, EA or Ubisoft there to see the play time (and the Epic and EA achievements) those launchers keep.
  - **Preferences:** the look (layout, style and theme) and the country for prices.
  - **Rescan launchers**, after you install a new launcher or game.
  - **Help:** check for updates, and the problem report.
- **Add a game by hand** (in the Library) for anything Junction doesn't find, like a game you installed without a launcher. Windows asks once, before it first starts, whether that program may run.
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
