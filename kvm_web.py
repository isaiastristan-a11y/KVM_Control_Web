import os
import threading
import configparser
import subprocess
import re

from flask import Flask, jsonify, request, render_template_string

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "kvm_config.ini")

app = Flask(__name__)

# Shared state (single-process dev server)
state_lock = threading.Lock()
serial_conn = None
active_pc = None  # 1..10

# Default commands for 10 PCs (will be overridden by INI on load)
pc_commands = {
    1: "X1,1$",
    2: "X2,2$",
    3: "X3,3$",
    4: "X4,4$",
    5: "X5,5$",
    6: "X6,6$",
    7: "X7,7$",
    8: "X8,8$",
    9: "X9,9$",
    10: "X10,10$",
}


def get_network_info():
    """Return dict with IP, mask, gateway for the default interface (Linux, best-effort)."""
    ip = ""
    mask = ""
    gateway = ""
    try:
        # Get default route to know gateway and interface
        route_out = subprocess.check_output(
            ["ip", "route", "show", "default"], text=True
        )
        m = re.search(r"default via (\S+) dev (\S+)", route_out)
        if not m:
            return {"ip": ip, "mask": mask, "gateway": gateway}
        gateway = m.group(1)
        iface = m.group(2)

        # Get IP/mask on that interface
        addr_out = subprocess.check_output(
            ["ip", "addr", "show", "dev", iface], text=True
        )
        m2 = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", addr_out)
        if m2:
            ip = m2.group(1)
            prefix = int(m2.group(2))
            # Convert CIDR prefix to dotted mask
            mask_int = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
            mask_parts = [
                str((mask_int >> shift) & 0xFF) for shift in (24, 16, 8, 0)
            ]
            mask = ".".join(mask_parts)
    except Exception:
        # Best-effort; leave empty strings on failure
        pass
    return {"ip": ip, "mask": mask, "gateway": gateway}


def load_config():
    """Load serial settings and per-PC commands from INI."""
    global pc_commands
    config = configparser.ConfigParser()
    if os.path.exists(CONFIG_FILE):
        try:
            config.read(CONFIG_FILE)
            serial_section = config["Serial"]
            serial_cfg = {
                "port": serial_section.get("port", ""),
                "baudrate": serial_section.get("baudrate", "9600"),
                "bytesize": serial_section.get("bytesize", "8"),
                "parity": serial_section.get("parity", "N"),
                "stopbits": serial_section.get("stopbits", "1"),
            }
        except Exception:
            serial_cfg = {
                "port": "",
                "baudrate": "9600",
                "bytesize": "8",
                "parity": "N",
                "stopbits": "1",
            }
        if config.has_section("KVM"):
            for i in range(1, 11):
                key = f"command_{i}"
                pc_commands[i] = config["KVM"].get(key, pc_commands[i])
    else:
        serial_cfg = {
            "port": "",
            "baudrate": "9600",
            "bytesize": "8",
            "parity": "N",
            "stopbits": "1",
        }
        save_config(serial_cfg)

    return serial_cfg


def save_config(serial_cfg):
    """Persist serial settings and per-PC commands to INI."""
    config = configparser.ConfigParser()
    config["Serial"] = {
        "port": serial_cfg.get("port", ""),
        "baudrate": serial_cfg.get("baudrate", "9600"),
        "bytesize": serial_cfg.get("bytesize", "8"),
        "parity": serial_cfg.get("parity", "N"),
        "stopbits": serial_cfg.get("stopbits", "1"),
    }
    config["KVM"] = {f"command_{i}": pc_commands[i] for i in range(1, 11)}

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        config.write(f)


def list_serial_ports():
    if serial is None:
        return []
    return [p.device for p in serial.tools.list_ports.comports()]


def open_serial(serial_cfg):
    """Open serial port using given config; returns connection."""
    if serial is None:
        raise RuntimeError("El módulo 'pyserial' no está instalado.")

    port = (serial_cfg.get("port") or "").strip()
    if not port:
        raise ValueError("Seleccione o escriba un puerto COM.")

    baudrate = int(serial_cfg.get("baudrate") or "9600")
    bytesize = int(serial_cfg.get("bytesize") or "8")
    parity_str = (serial_cfg.get("parity") or "N").upper()
    stopbits = float(serial_cfg.get("stopbits") or "1")

    parity_map = {
        "N": serial.PARITY_NONE,
        "E": serial.PARITY_EVEN,
        "O": serial.PARITY_ODD,
        "M": serial.PARITY_MARK,
        "S": serial.PARITY_SPACE,
    }

    bytesize_map = {
        5: serial.FIVEBITS,
        6: serial.SIXBITS,
        7: serial.SEVENBITS,
        8: serial.EIGHTBITS,
    }

    stopbits_map = {
        1: serial.STOPBITS_ONE,
        1.5: serial.STOPBITS_ONE_POINT_FIVE,
        2: serial.STOPBITS_TWO,
    }

    conn = serial.Serial(
        port=port,
        baudrate=baudrate,
        bytesize=bytesize_map.get(bytesize, serial.EIGHTBITS),
        parity=parity_map.get(parity_str, serial.PARITY_NONE),
        stopbits=stopbits_map.get(stopbits, serial.STOPBITS_ONE),
        timeout=1,
    )
    return conn


def _encode_command_text(command: str) -> bytes:
    """Encode a command string, supporting simple hex escapes like '\\0x4' or '\\x04'."""
    out = bytearray()
    i = 0
    length = len(command)
    while i < length:
        ch = command[i]
        if ch == "\\" and i + 1 < length:
            nxt = command[i + 1]
            # \xHH hex escape
            if nxt == "x" and i + 3 < length:
                hex_part = command[i + 2 : i + 4]
                try:
                    out.append(int(hex_part, 16))
                    i += 4
                    continue
                except ValueError:
                    # fall back to literal
                    pass
            # \0xH or \0xHH (as requested: e.g. X4,\\0x4$)
            if nxt == "0" and i + 3 < length and command[i + 2] == "x":
                # consume 1–2 hex digits after \0x
                j = i + 3
                hex_digits = ""
                while j < length and len(hex_digits) < 2 and command[j] in "0123456789abcdefABCDEF":
                    hex_digits += command[j]
                    j += 1
                if hex_digits:
                    try:
                        out.append(int(hex_digits, 16))
                        i = j
                        continue
                    except ValueError:
                        pass
            # Common escapes \r and \n
            if nxt == "r":
                out.append(13)
                i += 2
                continue
            if nxt == "n":
                out.append(10)
                i += 2
                continue
            # Default: treat "\X" as literal X
            out.append(ord(nxt))
            i += 2
        else:
            out.append(ord(ch))
            i += 1
    return bytes(out)


def send_command(command: str) -> bool:
    """Send command over the global serial connection. Returns True on success."""
    global serial_conn
    if serial_conn is None or not serial_conn.is_open:
        return False
    try:
        data = _encode_command_text(command) + b"\r\n"
        # Debug: print exactly what we send to the serial port
        print(f"[KVM SEND] raw_command={command!r} bytes={data!r}")
        serial_conn.write(data)
        return True
    except Exception:
        return False


@app.route("/")
def index():
    serial_cfg = load_config()
    ports = list_serial_ports()
    net_info = get_network_info()
    # Simple single-page app with fetch-based API calls
    return render_template_string(
        """
<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8" />
  <title>KVM RS-232 Controller (Web)</title>
  <style>
    body { font-family: sans-serif; margin: 20px; }
    fieldset { margin-bottom: 16px; }
    .matrix { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-top: 8px; }
    .pc-btn {
      padding: 10px;
      border: 1px solid #ccc;
      border-radius: 4px;
      background: #e0e0e0;
      cursor: pointer;
      text-align: center;
      font-weight: bold;
    }
    .pc-btn.active {
      background: #2e7d32;
      color: #fff;
    }
    .pc-btn:disabled {
      background: #ddd;
      cursor: not-allowed;
    }
    .status { margin-top: 8px; font-weight: bold; }
    .status.disconnected { color: #c62828; }
    .status.connected { color: #2e7d32; }
    label { display: inline-block; min-width: 110px; }
    input[type="text"], select { min-width: 120px; }
  </style>
</head>
<body>
  <h2>KVM RS-232 Controller (Web)</h2>

  <fieldset>
    <legend>Red local del PC (solo lectura)</legend>
    <div>
      <label>IP:</label>
      <span>{{ net_info.ip or "desconocida" }}</span>
    </div>
    <div>
      <label>Máscara:</label>
      <span>{{ net_info.mask or "desconocida" }}</span>
    </div>
    <div>
      <label>Gateway:</label>
      <span>{{ net_info.gateway or "desconocida" }}</span>
    </div>
  </fieldset>

  <fieldset>
    <legend>Configuración del puerto COM (9600, 8, N, 1)</legend>
    <div>
      <label for="port">Puerto:</label>
      <select id="port">
        <option value="">-- seleccionar --</option>
        {% for p in ports %}
        <option value="{{ p }}" {% if p == serial_cfg.port %}selected{% endif %}>{{ p }}</option>
        {% endfor %}
      </select>
      <input type="text" id="port_manual" placeholder="o escribir /dev/ttyUSB0" value="{{ serial_cfg.port }}" />
    </div>
    <div>
      <label for="baudrate">Baudios:</label>
      <input type="text" id="baudrate" value="{{ serial_cfg.baudrate }}" />
    </div>
    <div>
      <label for="bytesize">Bits de datos:</label>
      <input type="text" id="bytesize" value="{{ serial_cfg.bytesize }}" />
    </div>
    <div>
      <label for="parity">Paridad:</label>
      <select id="parity">
        {% for p in ["N", "E", "O", "M", "S"] %}
        <option value="{{ p }}" {% if p == serial_cfg.parity %}selected{% endif %}>{{ p }}</option>
        {% endfor %}
      </select>
    </div>
    <div>
      <label for="stopbits">Bits de parada:</label>
      <input type="text" id="stopbits" value="{{ serial_cfg.stopbits }}" />
    </div>
    <div style="margin-top:8px;">
      <button id="connectBtn">Conectar</button>
      <button id="disconnectBtn">Desconectar</button>
      <button id="saveCfgBtn">Guardar INI</button>
      <button id="reloadCmdBtn">Recargar comandos INI</button>
    </div>
    <div id="status" class="status disconnected">Desconectado</div>
  </fieldset>

  <fieldset>
    <legend>Comando a enviar para el PC seleccionado</legend>
    <div>
      <label for="commandInput">Comando:</label>
      <input type="text" id="commandInput" style="min-width:300px;" />
      <button id="saveCmdBtn">Guardar comando de este PC</button>
    </div>
    <div id="activePcLabel" style="margin-top:4px;">PC activo: ninguno</div>
  </fieldset>

  <fieldset>
    <legend>Selección de PC (2x5)</legend>
    <div class="matrix" id="pcMatrix"></div>
  </fieldset>

  <fieldset>
    <legend>Comandos actuales (solo lectura)</legend>
    <ul id="cmdList" style="padding-left: 20px; margin-top: 4px;"></ul>
  </fieldset>

  <script>
    const portsFromServer = {{ ports|tojson }};
    const initialSerialCfg = {{ serial_cfg|tojson }};
    let currentActivePc = null;
    let pcCommands = {};

    function showError(msg) {
      alert(msg);
    }

    function updateStatus(connected, text) {
      const statusEl = document.getElementById("status");
      statusEl.textContent = text || (connected ? "Conectado" : "Desconectado");
      statusEl.classList.toggle("connected", !!connected);
      statusEl.classList.toggle("disconnected", !connected);
    }

    function renderMatrix() {
      const matrix = document.getElementById("pcMatrix");
      matrix.innerHTML = "";
      for (let i = 1; i <= 10; i++) {
        const btn = document.createElement("button");
        btn.textContent = "PC " + i;
        btn.className = "pc-btn";
        btn.dataset.pcIndex = i;
        if (i === currentActivePc) {
          btn.classList.add("active");
        }
        btn.addEventListener("click", () => onPcClick(i));
        matrix.appendChild(btn);
      }
    }

    function refreshActivePcUI() {
      const label = document.getElementById("activePcLabel");
      if (!currentActivePc) {
        label.textContent = "PC activo: ninguno";
        document.getElementById("commandInput").value = "";
      } else {
        label.textContent = "PC activo: PC " + currentActivePc;
        document.getElementById("commandInput").value = pcCommands[currentActivePc] || "";
      }
      renderMatrix();
      // Update read-only list
      const list = document.getElementById("cmdList");
      list.innerHTML = "";
      for (let i = 1; i <= 10; i++) {
        const li = document.createElement("li");
        li.textContent = "PC " + i + ": " + (pcCommands[i] || "");
        list.appendChild(li);
      }
    }

    async function fetchState() {
      try {
        const res = await fetch("/api/state");
        if (!res.ok) throw new Error("Error al cargar el estado");
        const data = await res.json();
        currentActivePc = data.active_pc;
        pcCommands = data.pc_commands || {};
        updateStatus(data.serial_connected, data.status_text);
        refreshActivePcUI();
      } catch (e) {
        console.error(e);
      }
    }

    async function onPcClick(i) {
      try {
        const res = await fetch("/api/select_pc", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pc_index: i })
        });
        const data = await res.json();
        if (!data.success) {
          showError(data.error || "No se pudo enviar el comando.");
          return; // keep previous UI state
        }
        currentActivePc = data.active_pc;
        pcCommands = data.pc_commands || pcCommands;
        refreshActivePcUI();
      } catch (e) {
        console.error(e);
        showError("Error al comunicarse con el servidor.");
      }
    }

    async function onConnect() {
      const cfg = {
        port: document.getElementById("port_manual").value || document.getElementById("port").value,
        baudrate: document.getElementById("baudrate").value,
        bytesize: document.getElementById("bytesize").value,
        parity: document.getElementById("parity").value,
        stopbits: document.getElementById("stopbits").value
      };
      try {
        const res = await fetch("/api/connect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cfg)
        });
        const data = await res.json();
        if (!data.success) {
          showError(data.error || "No se pudo conectar.");
        }
        updateStatus(data.serial_connected, data.status_text);
      } catch (e) {
        console.error(e);
        showError("Error al comunicarse con el servidor.");
      }
    }

    async function onDisconnect() {
      try {
        const res = await fetch("/api/disconnect", { method: "POST" });
        const data = await res.json();
        updateStatus(false, data.status_text || "Desconectado");
      } catch (e) {
        console.error(e);
        showError("Error al comunicarse con el servidor.");
      }
    }

    async function onSaveCfg() {
      const cfg = {
        port: document.getElementById("port_manual").value || document.getElementById("port").value,
        baudrate: document.getElementById("baudrate").value,
        bytesize: document.getElementById("bytesize").value,
        parity: document.getElementById("parity").value,
        stopbits: document.getElementById("stopbits").value
      };
      try {
        const res = await fetch("/api/save_config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(cfg)
        });
        const data = await res.json();
        if (!data.success) {
          showError(data.error || "No se pudo guardar la configuración.");
        } else {
          alert("Configuración guardada en el INI.");
        }
      } catch (e) {
        console.error(e);
        showError("Error al comunicarse con el servidor.");
      }
    }

    async function onSaveCommand() {
      if (!currentActivePc) {
        showError("Seleccione primero un PC.");
        return;
      }
      const cmd = document.getElementById("commandInput").value;
      try {
        const res = await fetch("/api/save_command", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pc_index: currentActivePc, command: cmd })
        });
        const data = await res.json();
        if (!data.success) {
          showError(data.error || "No se pudo guardar el comando.");
          return;
        }
        pcCommands = data.pc_commands || pcCommands;
        alert("Comando guardado para PC " + currentActivePc + ".");
      } catch (e) {
        console.error(e);
        showError("Error al comunicarse con el servidor.");
      }
    }

    async function onReloadCommands() {
      try {
        const res = await fetch("/api/reload_commands", { method: "POST" });
        const data = await res.json();
        if (!data.success) {
          showError(data.error || "No se pudieron recargar los comandos desde el INI.");
          return;
        }
        pcCommands = data.pc_commands || pcCommands;
        // Keep currentActivePc as-is but refresh UI and list
        refreshActivePcUI();
        alert("Comandos recargados desde kvm_config.ini.");
      } catch (e) {
        console.error(e);
        showError("Error al comunicarse con el servidor.");
      }
    }

    document.getElementById("connectBtn").addEventListener("click", onConnect);
    document.getElementById("disconnectBtn").addEventListener("click", onDisconnect);
    document.getElementById("saveCfgBtn").addEventListener("click", onSaveCfg);
    document.getElementById("saveCmdBtn").addEventListener("click", onSaveCommand);
    document.getElementById("reloadCmdBtn").addEventListener("click", onReloadCommands);

    renderMatrix();
    fetchState();
    // Poll state periodically so that all open browser tabs stay in sync
    setInterval(fetchState, 1000);
  </script>
</body>
</html>
        """,
        serial_cfg=serial_cfg,
        ports=ports,
        net_info=net_info,
    )


@app.route("/api/state")
def api_state():
    with state_lock:
        serial_connected = serial_conn is not None and serial_conn.is_open
        serial_cfg = load_config()
        net_info = get_network_info()
        status_text = (
            f"Conectado a {serial_cfg.get('port','')} @ "
            f"{serial_cfg.get('baudrate','9600')} "
            f"{serial_cfg.get('bytesize','8')}{serial_cfg.get('parity','N')}{serial_cfg.get('stopbits','1')}"
            if serial_connected
            else "Desconectado"
        )
        return jsonify(
            {
                "serial_connected": serial_connected,
                "status_text": status_text,
                "active_pc": active_pc,
                "pc_commands": pc_commands,
                "net_info": net_info,
            }
        )


@app.route("/api/connect", methods=["POST"])
def api_connect():
    global serial_conn
    data = request.get_json(force=True) or {}
    serial_cfg = {
        "port": data.get("port", ""),
        "baudrate": data.get("baudrate", "9600"),
        "bytesize": data.get("bytesize", "8"),
        "parity": data.get("parity", "N"),
        "stopbits": data.get("stopbits", "1"),
    }
    with state_lock:
        # Close existing connection
        if serial_conn is not None:
            try:
                if serial_conn.is_open:
                    serial_conn.close()
            except Exception:
                pass
            serial_conn = None
        try:
            serial_conn = open_serial(serial_cfg)
            save_config(serial_cfg)
            status_text = (
                f"Conectado a {serial_cfg['port']} @ "
                f"{serial_cfg['baudrate']} {serial_cfg['bytesize']}{serial_cfg['parity']}{serial_cfg['stopbits']}"
            )
            return jsonify(
                {
                    "success": True,
                    "serial_connected": True,
                    "status_text": status_text,
                }
            )
        except Exception as e:
            serial_conn = None
            return jsonify(
                {
                    "success": False,
                    "serial_connected": False,
                    "status_text": "Desconectado",
                    "error": str(e),
                }
            )


@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    global serial_conn
    with state_lock:
        if serial_conn is not None:
            try:
                if serial_conn.is_open:
                    serial_conn.close()
            except Exception:
                pass
            serial_conn = None
    return jsonify({"success": True, "status_text": "Desconectado"})


@app.route("/api/select_pc", methods=["POST"])
def api_select_pc():
    global active_pc
    data = request.get_json(force=True) or {}
    pc_index = int(data.get("pc_index", 0))
    if pc_index < 1 or pc_index > 10:
        return jsonify({"success": False, "error": "Índice de PC inválido."})

    with state_lock:
        cmd = (pc_commands.get(pc_index) or f"sw i{pc_index} o1").strip()
        if not cmd:
            return jsonify({"success": False, "error": "Comando vacío para este PC."})
        ok = send_command(cmd)
        if not ok:
            return jsonify(
                {
                    "success": False,
                    "error": "No hay conexión al puerto serie. Conecte primero el puerto COM.",
                }
            )
        active_pc = pc_index
        return jsonify(
            {
                "success": True,
                "active_pc": active_pc,
                "pc_commands": pc_commands,
            }
        )


@app.route("/api/save_command", methods=["POST"])
def api_save_command():
    data = request.get_json(force=True) or {}
    pc_index = int(data.get("pc_index", 0))
    command = (data.get("command") or "").strip()
    if pc_index < 1 or pc_index > 10:
        return jsonify({"success": False, "error": "Índice de PC inválido."})
    if not command:
        return jsonify({"success": False, "error": "El comando no puede estar vacío."})

    with state_lock:
        pc_commands[pc_index] = command
        serial_cfg = load_config()
        save_config(serial_cfg)
        return jsonify({"success": True, "pc_commands": pc_commands})


@app.route("/api/save_config", methods=["POST"])
def api_save_config():
    data = request.get_json(force=True) or {}
    serial_cfg = {
        "port": data.get("port", ""),
        "baudrate": data.get("baudrate", "9600"),
        "bytesize": data.get("bytesize", "8"),
        "parity": data.get("parity", "N"),
        "stopbits": data.get("stopbits", "1"),
    }
    with state_lock:
        save_config(serial_cfg)
    return jsonify({"success": True})


@app.route("/api/reload_commands", methods=["POST"])
def api_reload_commands():
    global pc_commands
    with state_lock:
        # Re-read only the KVM section from INI to update pc_commands
        cfg = configparser.ConfigParser()
        try:
            if not os.path.exists(CONFIG_FILE):
                return jsonify({"success": False, "error": "kvm_config.ini no existe."})
            cfg.read(CONFIG_FILE)
            if not cfg.has_section("KVM"):
                return jsonify({"success": False, "error": "La sección [KVM] no existe en el INI."})
            for i in range(1, 11):
                key = f"command_{i}"
                if cfg.has_option("KVM", key):
                    pc_commands[i] = cfg.get("KVM", key)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})
    return jsonify({"success": True, "pc_commands": pc_commands})


def main():
    # Dev mode: debug=True, auto-reload
    app.run(debug=True, host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()

