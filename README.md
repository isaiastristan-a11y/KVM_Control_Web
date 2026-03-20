# KVM RS-232 Web Controller

Web application to control a KVM switch over RS-232 using a 2x5 matrix (10 PCs), configurable COM settings, and per-PC commands stored in an INI file.

## Features

- RS-232 serial communication using `pyserial`.
- COM settings configuration (baudrate, data bits, parity, stop bits).
- 10 PC selector buttons (2 rows x 5 columns).
- Per-PC command strings in `kvm_config.ini` (`command_1` ... `command_10`).
- Active PC indicator and synchronized state across open browser tabs.
- Manual command reload from INI (`Recargar comandos INI` button).

## Required Software

Install these packages on Linux:

- `python3` (recommended 3.10+)
- `python3-venv`
- `python3-pip` (usually available with venv/pip)

Example (Debian/Ubuntu/Mint):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

## Project Files

- `kvm_web.py` - Flask web app.
- `kvm_config.ini` - Serial and command configuration.
- `requirements.txt` - Python dependencies.
- `run_kvm_web.sh` - Setup + launch script.

## Setup and Run

From the project directory:

```bash
cd "/home/isaias/Documents/kvm control"
chmod +x run_kvm_web.sh
./run_kvm_web.sh
```

What this script does:

1. Creates `.venv` (if it does not exist).
2. Installs dependencies from `requirements.txt`.
3. Starts Flask on port `5000`.

Open in browser:

- Local machine: `http://localhost:5000`
- Another PC on LAN: `http://<HOST_IP>:5000`

## Serial Port Permissions (Important)

If you get permission errors opening `/dev/ttyUSB0` (or similar), add your user to `dialout`:

```bash
sudo usermod -aG dialout "$USER"
```

Then log out and log back in (or reboot).

## Configuration (`kvm_config.ini`)

Example:

```ini
[Serial]
port = /dev/ttyUSB0
baudrate = 115200
bytesize = 8
parity = N
stopbits = 1

[KVM]
command_1 = X1,1$
command_2 = X2,2$
command_3 = X3,3$
command_4 = X4,4$
command_5 = X5,5$
command_6 = X6,6$
command_7 = X7,7$
command_8 = X8,8$
command_9 = X9,9$
command_10 = X10,10$
```

Notes:

- `port` must match the target machine (`/dev/ttyUSB0`, `/dev/ttyS0`, etc.).
- The web app can reload commands from disk using `Recargar comandos INI`.
- Hex escape support in commands:
  - `\x04`
  - `\0x4`
  - `\0x04`

## Troubleshooting

### 1) `ModuleNotFoundError: No module named 'flask'`

Run using the launcher script:

```bash
./run_kvm_web.sh
```

### 2) Browser cannot open `localhost:5000`

- Ensure the script is still running in terminal.
- Check server logs in that terminal for startup errors.

### 3) KVM not switching

- Confirm serial settings are correct for your KVM.
- Confirm command format in `[KVM]`.
- Confirm correct serial port device.
- Confirm cable/adapter is correctly connected.

### 4) Commands shown in browser are old

- Update `kvm_config.ini`.
- Click `Recargar comandos INI`.
- If needed, restart the app and refresh browser.

## Move to Another PC

Copy this project folder (except `.venv` is optional to copy). On the new PC, run:

```bash
chmod +x run_kvm_web.sh
./run_kvm_web.sh
```

Then adjust `kvm_config.ini` for the new serial device path and command needs.

