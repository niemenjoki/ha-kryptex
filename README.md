# Kryptex for Home Assistant

> [!NOTE]
> Tested with Kryptex 5.10.7 and Home Assistant 2026.10. The bridge reads Kryptex's internal database, which a
> Kryptex update can change. Please report problems in [Issues](https://github.com/niemenjoki/ha-kryptex/issues).

Monitor and control [Kryptex](https://www.kryptex.com/) crypto mining from Home Assistant. See your Kryptex
balance and income, follow every mining GPU and CPU, pause mining when electricity is expensive, and compare
mining income with your electricity price. Installs through HACS; a small bridge runs on the Windows mining PC.

## Features

- Balance, withdrawable balance, and daily and monthly income, in bitcoin and optionally in your own currency.
- Every mining GPU and CPU as its own device: hashrate, coin and algorithm, temperature, power, fan and clocks.
- Pause and resume mining with Kryptex's own pause, and start or close the Kryptex app.
- Profitability: break-even electricity price, electricity cost and net profit per day, also while mining is
  paused, so automations can decide when to mine.
- Mine only when it pays: an example automation pauses mining when electricity costs more than mining earns,
  for example with spot prices from Nord Pool.
- Ready-made dashboard cards that use only built-in cards.
- Local: no Kryptex account details or cloud service needed. The bridge talks to the Kryptex app on the PC.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/niemenjoki/ha-kryptex/main/docs/dashboard-dark.png">
  <img alt="Kryptex dashboard in Home Assistant: balance, income, break-even electricity price, net profit, pause
  switch and a GPU with temperature, power, fan and hashrate" src="https://raw.githubusercontent.com/niemenjoki/ha-kryptex/main/docs/dashboard-light.png">
</picture>

The example cards from [`examples/`](examples/), with built-in Home Assistant cards only.

Two parts:

1. **Kryptex bridge** (`bridge/`): a small Python HTTP API on the mining PC. It reads Kryptex's local database
   (read-only) and the Kryptex service's local API, and passes on only what is listed below.
2. **Integration** (`custom_components/kryptex/`): installed through HACS. It polls the bridge every 60 seconds.

Not affiliated with Kryptex. The bridge reads an internal database and an undocumented local API of the app; both
can change in any Kryptex update. If they do, the affected entities become unavailable instead of showing wrong
numbers.

## Money is in bitcoin

Kryptex keeps your balance in bitcoin; amounts in dollars or euros are only a conversion at the current rate. So
the integration reports balance and income in **BTC**. In the options you can add a display currency: then every
amount also gets a sensor in that currency, converted with Kryptex's own bitcoin rate (the same rate the app
shows), and a bitcoin price sensor.

## Entities

**Kryptex** (one device per bridge)

| Entity | Notes |
|---|---|
| Balance, Withdrawable balance (BTC) | As last synced by the app. |
| Daily income, Monthly income (BTC) | Current hashrate × Kryptex's profitability estimate, like the app shows. Unavailable when not mining. |
| Same in your currency, Bitcoin price | Only with a display currency (options). |
| Break-even electricity price | Only with a power sensor (options). The electricity price at which mining income equals the electricity cost. Also while paused or closed, see [Profitability while not mining](#profitability-while-not-mining). |
| Electricity cost per day, Net profit per day | Only with a power sensor and an electricity price sensor (options). |
| Mining (binary sensor) | On when Kryptex mines: the app runs, it is not paused, and it reports a hashrate. |
| App running (binary sensor) | Whether `Kryptex.exe` is running. Stays on while Kryptex is paused. |
| Kryptex app (switch) | Starts or closes the Kryptex app. |
| Paused (switch) | Kryptex's own pause: mining stops, the app and the PC stay on. |
| Kryptex (update) | Installed and offered version. Shows that an update exists; it does not install it (see below). |

**Mining devices**: every CPU and GPU that Kryptex lists becomes its own device, added automatically when it
first appears. Entities: state, algorithm (coin in attributes), miner and version, hashrate, daily income (BTC and
your currency), temperature, memory temperature, power, fan, and core and memory clock (disabled by default).
An entity is created only when Kryptex reports the value: Kryptex reports no temperature or power for CPUs. A
device that Kryptex no longer lists becomes unavailable and can be deleted from its device page.

The hashrate unit follows the value, like in the Kryptex app (kH/s on a CPU, up to TH/s on some GPU algorithms),
because Kryptex can switch algorithms. The value in H/s is in the attribute `hashrate_h_s`.

When the PC is off, all entities are unavailable. The integration does not block Home Assistant startup.

### Why updates are installed at the PC

The Kryptex installer asks for Windows administrator approval (the "Do you want to allow this app to make changes"
prompt). That prompt can only be answered by someone at the PC, so an update started from Home Assistant would
stop there. The update entity tells you when a new version is offered; install it at the PC.

## 1. Install the bridge on the mining PC

Needs Windows, Kryptex and Python 3.10 or newer. Get Python from [python.org](https://www.python.org/downloads/)
and tick "Add python.exe to PATH" in the installer.

Do these on the mining PC, signed in as the Windows user who runs Kryptex:

1. Open the [latest release](https://github.com/niemenjoki/ha-kryptex/releases/latest) and under **Assets** click
   **Source code (zip)**. This bridge matches the integration version that HACS installs.
2. In your Downloads folder, right-click the file, for example `ha-kryptex-1.0.0.zip` → **Extract All** →
   **Extract**.
3. Open the extracted folder and inside it the `bridge` folder. Windows may put a second `ha-kryptex-1.0.0` folder
   inside the first one: open it too.
4. Double-click **`install.cmd`**. Windows asks for administrator rights (needed for the firewall rule and for
   starting and closing Kryptex, which runs as administrator): click Yes.
   If Windows warns that the file comes from the internet, click **More info → Run anyway** (or **Run**).
5. The window ends with the address, port and API key to enter in Home Assistant. Copy them, then press a key to
   close the window. The key is also in `%APPDATA%\kryptex-bridge\config.json`.

Lost the address, port or key? Run `install.cmd` again: it shows them again and changes nothing (the key is
kept).

If you use git: `git clone https://github.com/niemenjoki/ha-kryptex`, check out the release you installed in HACS
(for example `git checkout v1.0.0`) and run `bridge\install.cmd`. To update, `git fetch --tags` and check out the
new release tag.

To update the bridge later, download the new release's **Source code (zip)** and run `install.cmd` again. The API
key is kept.

### What the installer does

`install.cmd` only starts [`install.ps1`](bridge/install.ps1) as administrator. `install.ps1` explains each step
in comments:

1. Finds Python on your PC.
2. Chooses a free port: 5500, or the first free one of 5501-5510 if another program uses 5500. The
   window shows the port to enter in Home Assistant.
3. Copies one file, [`kryptex_bridge.py`](bridge/kryptex_bridge.py), to `%LOCALAPPDATA%\kryptex-bridge`.
4. Creates `%APPDATA%\kryptex-bridge\config.json` with a random API key. When you run the installer again, the
   file, its key and your own settings are kept; only the port changes if another program has taken it.
5. Registers a scheduled task "Kryptex bridge" that starts the bridge hidden at logon and restarts it if it stops.
   It is a scheduled task and not a Windows service on purpose: Kryptex runs in your user session, and a service
   could not start it there. Two more tasks, "Kryptex bridge start" and "Kryptex bridge stop", start and close the
   Kryptex app with administrator rights, because Kryptex runs as administrator. They run only when the bridge
   asks, and the bridge itself runs without administrator rights.
6. Adds a firewall rule "Kryptex bridge" that opens only the bridge's port, only to your local network, and only
   on private networks.
7. Checks that the bridge answers.

Nothing is downloaded and nothing leaves your PC except the answers to Home Assistant. The bridge uses only
Python's standard library, talks only to Kryptex on the same PC (127.0.0.1), and reads Kryptex's database
read-only. Every request to it needs the API key. [`uninstall.ps1`](bridge/uninstall.ps1) removes everything.

### Installer options

To give options, open the `bridge` folder in File Explorer, right-click an empty spot → **Open in Terminal**
(Windows 10: **File → Open Windows PowerShell**), and run for example:

```powershell
.\install.cmd -Port 5501 -FirewallRemoteAddress 192.168.1.10
```

| Option | Default | Effect |
|---|---|---|
| `-Port` | first free of 5500-5510 | Use this port and save it in `config.json`. If it is in use, the installer stops before changing anything. |
| `-FirewallRemoteAddress` | `LocalSubnet` | Who may connect: your local network, or only your Home Assistant's IP address. |
| `-NoFirewall` | | No firewall rule. Add one yourself if Home Assistant cannot connect. |
| `-Python` | `pythonw.exe` on PATH | Full path to `pythonw.exe`. |

### If something goes wrong

| Message | What to do |
|---|---|
| `Cannot find path` or `install.cmd is not recognized` | The terminal is not in the `bridge` folder. Use **Open in Terminal** in that folder as above, or type `cd `, drag the `bridge` folder into the window and press Enter. |
| `pythonw.exe was not found` | Install Python from python.org with "Add python.exe to PATH" ticked, then run the installer again. |
| `Your network ... is set to Public` | Settings → Network & internet → your connection → Network profile type → **Private network**. The firewall rule is not used on public networks. |
| `This window runs as ..., but ... is signed in` | Windows asked for another account's password. Install by hand (below) or ask the PC's administrator to add the firewall rule. |
| `Port ... is in use by ...` | The port given with `-Port` is taken by the program shown. Leave `-Port` out to let the installer pick a free port. |
| `The port changed from ... to ...` | Another program took the bridge's port, so the installer chose a new one. Delete the Kryptex integration in Home Assistant and add it again with the new port. |
| `The bridge did not answer yet` | See `%APPDATA%\kryptex-bridge\bridge.log`. It tells why the bridge did not start. |

### Install by hand

If you would rather not run a script, these are the same steps done by hand:

1. Create the folder `%LOCALAPPDATA%\kryptex-bridge` (paste the name into File Explorer's address bar) and copy
   `bridge\kryptex_bridge.py` into it.
2. Create the folder `%APPDATA%\kryptex-bridge` and in it a file `config.json`, with a long random key of your own
   (for example from a password manager, at least 32 letters and digits):

   ```json
   { "port": 5500, "api_key": "your-long-random-key" }
   ```

3. Task Scheduler → **Create Task**. General: name `Kryptex bridge`, "Run only when user is logged on". Triggers:
   **At log on** of your user. Actions: **Start a program**, program `pythonw.exe` (full path, e.g.
   `C:\Users\you\AppData\Local\Programs\Python\Python313\pythonw.exe`), arguments the full path of
   `kryptex_bridge.py` in quotes. Settings: "If the task fails, restart every 1 minute", and untick "Stop the task
   if it runs longer than". Then right-click the task → **Run**.
4. Two more tasks, both with "Run with highest privileges" ticked and no trigger: `Kryptex bridge start`
   (program `cmd.exe`, arguments `/c start "" "C:\Program Files\Kryptex\Kryptex.exe"`) and `Kryptex bridge stop`
   (program `taskkill.exe`, arguments `/IM Kryptex.exe /F`).
5. Windows Defender Firewall with Advanced Security → Inbound Rules → **New Rule** → Port, TCP `5500` → Allow →
   Domain and Private → name `Kryptex bridge`. Then open the rule → Scope → Remote IP address → **Local subnet**.
6. Check in PowerShell: `Invoke-RestMethod http://127.0.0.1:5500/health -Headers @{ Authorization = "Bearer your-long-random-key" }`
   should answer `ok` and the version.

The uninstaller also removes a bridge installed by hand, as long as the names above are used.

### Configuration

`%APPDATA%\kryptex-bridge\config.json`. Restart the task after changes (Task Scheduler, or log off and on).

| Key | Default |
|---|---|
| `api_key` | created by the installer |
| `host`, `port` | `0.0.0.0`, `5500` |
| `database` | `%APPDATA%\Kryptex\kryptex.db` |
| `service_url` | `http://127.0.0.1:8107` (the Kryptex service; must be on this PC) |
| `process_name` | `Kryptex.exe` |
| `start_command` | `["schtasks", "/run", "/tn", "Kryptex bridge start"]` |
| `stop_command` | `["schtasks", "/run", "/tn", "Kryptex bridge stop"]` |

Commands are lists of arguments and are never built from request data. The log is
`%APPDATA%\kryptex-bridge\bridge.log`.

The Kryptex service API has no password and listens only on the PC itself. The bridge never exposes it: it asks
only for the version, update, pause state and devices, and changes only the pause. Hardware ids (GPU UUIDs, PCI
addresses) and account data are not passed on.

### API

All requests need `Authorization: Bearer <api_key>`.

| Request | Answer |
|---|---|
| `GET /health` | `{ ok, version }` |
| `GET /status` | `{ ok, version, running, profitability, estimate, balance, rates, service }` |
| `GET /profitability`, `/estimate`, `/balance`, `/rates`, `/service` | One part. HTTP 503 with `{ ok: false, error }` when it fails. |
| `POST /start`, `POST /stop` | Start or close the app: `{ ok, changed }` |
| `POST /pause`, `POST /resume` | Kryptex's pause, read back after the change: `{ ok, changed, paused }` |

Amounts have `_btc` fields (and `_usd` for reference: Kryptex stores USD internally). `rates.btc_price` is the
price of one bitcoin in each currency Kryptex knows. `profitability` is the income now; `estimate` is the income
if mining started now (the median of the last 120 mining readings, about 10 minutes, up to a week old, with
Kryptex's newest coefficients). A failed part never contains numbers, so nothing stores a wrong value. Error codes include
`database_missing`, `paused`, `stale_measurement` (no hashrate in the last 120 s, e.g. not mining),
`stale_coefficient`, `no_btc_rate`, `service_unavailable` and `pause_not_applied`.

### Uninstall

Open PowerShell as administrator (right-click Start → **Terminal (Admin)**), type `cd `, drag the `bridge` folder
into the window, press Enter, and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 -RemoveConfig
```

Without `-RemoveConfig` the settings and the API key are kept for a later install.

## 2. Install the integration

Needs [HACS](https://hacs.xyz/). If you do not have it yet, install it first with
[its own guide](https://hacs.xyz/docs/use/download/download/).

The buttons open the right page in your own Home Assistant
([My Home Assistant](https://my.home-assistant.io/) asks for its address the first time):

1. [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=niemenjoki&repository=ha-kryptex&category=integration)
   Or by hand: HACS → menu → Custom repositories → add `https://github.com/niemenjoki/ha-kryptex` as an
   Integration.
2. Download "Kryptex" and restart Home Assistant.
3. [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=kryptex)
   Or by hand: Settings → Devices & services → Add integration → Kryptex.

   Enter the address, port and API key that the bridge installer showed.

### Keep the address working

Your router gives the mining PC its IP address, and it may give it a different one later, for example after the
router restarts. Home Assistant then no longer finds the bridge. Two ways to avoid this:

- **Use the PC's name instead of its IP address**: enter the name with `.local` at the end, e.g. `MYPC.local`.
  Windows shows the name in Settings → System → About ("Device name"). Home Assistant finds the PC by its name
  on your local network.
- **Reserve the IP address for the PC in your router.** The setting is usually called "DHCP reservation" or
  "static lease", and its place differs between routers. If you are unsure, tell your favourite AI assistant
  your router's model and that you want to reserve an IP address for one computer, and it will guide you.

If the address or port changes later: Settings → Devices & services → Kryptex → ⋮ → **Reconfigure**. Your
entities and settings stay.

### Options

Settings → Devices & services → Kryptex → Configure.

| Option | Effect |
|---|---|
| Display currency | Default: your Home Assistant currency, if Kryptex has a rate for it. "BTC only" adds no converted sensors. |
| Power of the mining PC | A power sensor (W or kW) that measures the whole PC, e.g. a smart plug. Adds the break-even price. Kryptex reports only GPU power, so its own numbers are not enough. |
| Total electricity price | Your price including transfer, taxes and other fees. Adds electricity cost and net profit per day. |
| Unit of the electricity price | Currency per kWh or cents per kWh. The break-even price uses the same unit as your price sensor, so the two can be compared directly. |

### Profitability while not mining

The profitability sensors tell what mining would earn, also while Kryptex is paused or closed, so an automation
can decide when to start or resume:

- **Income**: while mining, Kryptex's estimate now. Otherwise the bridge's estimate: the hashrates of the last
  10 minutes Kryptex mined (up to a week ago) with Kryptex's newest profitability coefficients. It takes the median
  reading, so a reading from the moment mining starts or stops, when only some devices mine, does not count.
  Kryptex updates its coefficients also while paused, so the estimate follows the bitcoin price and difficulty.
- **Power**: the median of the PC's power while mining (one sample a minute, the last 60), not the idle power
  of a paused PC. The samples are kept when Home Assistant restarts. Before the first samples, the power now is
  used while mining, and the sensors are unavailable while not mining.

The attributes `income_basis` (`mining_now` or `estimate`), `hashrate_measured_at` and `power_basis` show what
each value is based on.

## Examples

`examples/` has ready-made dashboard cards and an automation. Replace the entity ids as the comments say.

- `dashboard-overview.yaml`: balance, income, profitability, pause and version.
- `dashboard-device.yaml`: one mining device with temperature and power gauges.
- `automation-pause-when-unprofitable.yaml`: pauses Kryptex when electricity costs more than the break-even price
  for 10 minutes and resumes when it is cheaper again. It only uses Kryptex's pause; the PC stays on.

### How the pause automation works

The automation compares two numbers: your total electricity price and the break-even electricity price, the price
at which mining earns exactly what its electricity costs.

- **Pause:** when the electricity price has been above the break-even price for 10 minutes, it turns on the
  Kryptex "Paused" switch. Mining stops; the Kryptex app and the PC stay on.
- **Resume:** when the electricity price has been below the break-even price for 10 minutes, it turns the switch
  off and mining continues.
- **While paused** the break-even price is still shown (see
  [Profitability while not mining](#profitability-while-not-mining)), so the automation knows when to resume.
- **The 10 minutes** keep a short price spike, or a price that changes back and forth around the break-even price,
  from pausing and resuming mining again and again. With hourly or 15-minute spot prices this means at most one
  change per price period.

To adjust it:

- **Pause only when clearly unprofitable:** add a margin, for example
  `> states('sensor.kryptex_break_even_electricity_price') | float + 1` (1 cent with prices in c/kWh).
- **Count the heat:** if mining heats a room you would heat anyway, the heat has value. Add your heating cost per
  kWh to the break-even price in both triggers, and mining continues at higher electricity prices.
- **Other conditions:** add conditions under `conditions:`, for example to leave the switch alone while you use the
  PC.

The price sensor and the break-even price must use the same unit (c/kWh or currency/kWh). Choose the unit in the
integration options. The automation does nothing while the PC is off or the Kryptex app is closed.

## Development

```sh
pip install pytest-homeassistant-custom-component
pytest                                       # integration and examples, with saved bridge answers in tests/fixtures
python -m unittest discover -s bridge/tests  # bridge, against a synthetic database and a fake Kryptex service
```

The tests run with the Home Assistant version of pytest-homeassistant-custom-component; the integration is used with Kryptex 5.10.7 and Home Assistant 2026.10.

## License

MIT
