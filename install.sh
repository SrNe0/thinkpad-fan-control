#!/bin/bash
set -e

APP_NAME="ThinkPad Fan Control"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[ OK ]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERR ]${NC}  $*"; exit 1; }
ask()     { echo -e "${BOLD}${YELLOW}[ ? ]${NC}  $*"; }

echo -e "\n${BOLD}${CYAN}ThinkPad Fan Control — Installer${NC}\n"

# ── root check ────────────────────────────────────────────────────────────────
if [[ $EUID -eq 0 ]]; then
    error "Do not run as root. The installer will ask for sudo when needed."
fi

REAL_USER="$USER"
REAL_HOME="$HOME"

# ── detect distro / package manager ───────────────────────────────────────────
detect_distro() {
    if command -v apt &>/dev/null; then
        echo "apt"
    elif command -v dnf &>/dev/null; then
        echo "dnf"
    elif command -v pacman &>/dev/null; then
        echo "pacman"
    elif command -v zypper &>/dev/null; then
        echo "zypper"
    else
        echo "unknown"
    fi
}

install_deps() {
    local pm="$1"
    info "Installing dependencies via $pm..."
    case "$pm" in
        apt)
            sudo apt update -q
            sudo apt install -y thinkfan python3-tk python3-matplotlib \
                                python3-pil python3-pystray python3-gi
            ;;
        dnf)
            sudo dnf install -y thinkfan python3-tkinter python3-matplotlib \
                                python3-pillow python3-pystray python3-gobject
            ;;
        pacman)
            # thinkfan is AUR-only — need an AUR helper
            AUR_HELPER=""
            if command -v yay &>/dev/null; then AUR_HELPER="yay"
            elif command -v paru &>/dev/null; then AUR_HELPER="paru"
            fi

            # --needed skips packages already installed
            sudo pacman -Sy --needed --noconfirm python-matplotlib python-pillow \
                                                 python-pystray python-gobject tk

            if [[ -n "$AUR_HELPER" ]]; then
                if pacman -Q thinkfan &>/dev/null; then
                    ok "thinkfan already installed — skipping."
                else
                    info "Installing thinkfan from AUR via $AUR_HELPER..."
                    "$AUR_HELPER" -S --needed --noconfirm thinkfan
                fi
            else
                if pacman -Q thinkfan &>/dev/null; then
                    ok "thinkfan already installed."
                else
                    warn "thinkfan is an AUR package and requires an AUR helper."
                    warn "Install yay or paru first:"
                    warn "  https://github.com/Jguer/yay"
                    warn "Then re-run this installer."
                    error "No AUR helper found (yay/paru). Install one and re-run."
                fi
            fi
            ;;
        zypper)
            sudo zypper install -y thinkfan python3-tk python3-matplotlib \
                                   python3-Pillow python3-pystray python3-gobject
            ;;
        *)
            warn "Unknown package manager. Install manually:"
            warn "  thinkfan, python3-tk, python3-matplotlib, python3-pil, python3-pystray"
            ;;
    esac
}

# ── detect wayland / tray ─────────────────────────────────────────────────────
check_tray() {
    if [[ -n "$WAYLAND_DISPLAY" ]]; then
        info "Wayland session detected."
        if pgrep -x "waybar" &>/dev/null; then
            if grep -rq '"tray"' "$REAL_HOME/.config/waybar/" 2>/dev/null; then
                ok "waybar with tray module found — systray will work."
            else
                warn "waybar found but no 'tray' module in its config."
                ask "Add tray module to waybar automatically? [y/N]"
                read -r answer
                if [[ "$answer" =~ ^[Yy]$ ]]; then
                    add_waybar_tray
                else
                    warn "Systray icon will not show. You can add it manually later."
                fi
            fi
        else
            warn "No compatible tray provider found (waybar/swaybar)."
            warn "The app will launch as a normal window without a tray icon."
        fi
    else
        ok "X11 session — systray fully supported."
    fi
}

add_waybar_tray() {
    local config
    config=$(find "$REAL_HOME/.config/waybar" -name "config" -o -name "config.jsonc" 2>/dev/null | head -1)
    if [[ -z "$config" ]]; then
        warn "Could not find waybar config file. Add \"tray\" manually."
        return
    fi
    cp "$config" "${config}.bak"
    # Insert "tray" into the modules-right array
    sed -i 's/"modules-right": \[/"modules-right": ["tray", /' "$config"
    ok "Added 'tray' to waybar modules-right. Backup at ${config}.bak"
    warn "Restart waybar to apply: killall waybar && waybar &"
}

# ── thinkpad_acpi fan_control ─────────────────────────────────────────────────
setup_module() {
    local conf="/etc/modprobe.d/thinkpad.conf"
    if [[ -f "$conf" ]] && grep -q "fan_control=1" "$conf"; then
        ok "thinkpad_acpi fan_control already enabled."
    else
        info "Enabling thinkpad_acpi fan_control..."
        echo "options thinkpad_acpi fan_control=1" | sudo tee "$conf" > /dev/null
        ok "Written: $conf"
    fi
    # Apply without reboot: try sysfs first, fall back to module reload
    if echo 1 | sudo tee /sys/module/thinkpad_acpi/parameters/fan_control > /dev/null 2>&1; then
        ok "fan_control=1 applied (no reboot needed)."
    else
        info "Reloading thinkpad_acpi module to apply fan_control=1..."
        sudo modprobe -r thinkpad_acpi 2>/dev/null || true
        if sudo modprobe thinkpad_acpi fan_control=1; then
            ok "Module reloaded with fan_control=1."
        else
            warn "Could not reload thinkpad_acpi. A reboot may be required."
        fi
    fi
}

# ── install helper script + sudoers ──────────────────────────────────────────
setup_helper() {
    info "Installing helper script..."
    sudo install -m 755 "$SCRIPT_DIR/scripts/thinkfan-set-level.sh" \
                        /usr/local/bin/thinkfan-set-level
    ok "Installed: /usr/local/bin/thinkfan-set-level"

    info "Installing sudoers rule..."
    sudo install -m 440 "$SCRIPT_DIR/scripts/thinkfan-sudoers" \
                        /etc/sudoers.d/thinkfan-gui
    # Validate
    if sudo visudo -c &>/dev/null; then
        ok "Sudoers rule valid."
    else
        sudo rm /etc/sudoers.d/thinkfan-gui
        error "Sudoers validation failed — rule removed for safety."
    fi
}

# ── copy app ──────────────────────────────────────────────────────────────────
setup_app() {
    local dest="$REAL_HOME/.local/share/thinkpad-fan-control"
    mkdir -p "$dest"
    cp "$SCRIPT_DIR/src/thinkfan-control.py" "$dest/"
    ok "App installed at: $dest"

    # .desktop for app menu
    mkdir -p "$REAL_HOME/.local/share/applications"
    cat > "$REAL_HOME/.local/share/applications/thinkfan-control.desktop" <<EOF
[Desktop Entry]
Name=ThinkPad Fan Control
Comment=Fan speed monitor and control for ThinkPad laptops
Exec=python3 $dest/thinkfan-control.py
Icon=preferences-system
Terminal=false
Type=Application
Categories=System;Settings;
Keywords=fan;temperature;thinkpad;cooling;
EOF
    ok "App menu entry created."

    # autostart
    mkdir -p "$REAL_HOME/.config/autostart"
    cat > "$REAL_HOME/.config/autostart/thinkfan-control.desktop" <<EOF
[Desktop Entry]
Name=ThinkPad Fan Control
Exec=python3 $dest/thinkfan-control.py
Icon=preferences-system
Terminal=false
Type=Application
Hidden=false
X-GNOME-Autostart-enabled=true
EOF
    ok "Autostart entry created."
}

# ── initial thinkfan config ───────────────────────────────────────────────────
setup_thinkfan_config() {
    if [[ -f /etc/thinkfan.conf ]]; then
        warn "/etc/thinkfan.conf already exists — skipping (your config is preserved)."
        return
    fi
    info "Writing default thinkfan config (Rendimiento profile)..."
    sudo tee /etc/thinkfan.conf > /dev/null <<'EOF'
# ThinkPad Fan Control — default profile: Rendimiento
sensors:
  - hwmon: /sys/class/hwmon
    name: thinkpad
    indices: [1, 2]

fans:
  - tpacpi: /proc/acpi/ibm/fan

#                          CPU  GPU
levels:
  - speed: 0
    upper_limit:          [43,  40]

  - speed: 1
    lower_limit:          [41,  38]
    upper_limit:          [48,  45]

  - speed: 2
    lower_limit:          [46,  43]
    upper_limit:          [53,  50]

  - speed: 3
    lower_limit:          [51,  48]
    upper_limit:          [58,  55]

  - speed: 4
    lower_limit:          [56,  53]
    upper_limit:          [63,  60]

  - speed: 5
    lower_limit:          [61,  58]
    upper_limit:          [68,  65]

  - speed: 6
    lower_limit:          [66,  63]
    upper_limit:          [73,  70]

  - speed: 7
    lower_limit:          [71,  68]
EOF
    ok "Written: /etc/thinkfan.conf"
}

# ── enable + start thinkfan service ──────────────────────────────────────────
setup_service() {
    info "Enabling thinkfan service..."
    sudo systemctl enable --now thinkfan
    if systemctl is-active --quiet thinkfan; then
        ok "thinkfan service is running."
    else
        warn "thinkfan failed to start. Check: journalctl -xeu thinkfan"
    fi
}

# ── uninstall ────────────────────────────────────────────────────────────────
do_uninstall() {
    echo -e "\n${BOLD}${YELLOW}Uninstalling ThinkPad Fan Control...${NC}\n"

    # Kill running instance
    pkill -f "thinkfan-control.py" 2>/dev/null && ok "App process stopped." || true

    # Stop and disable thinkfan service
    if systemctl is-enabled --quiet thinkfan 2>/dev/null; then
        sudo systemctl disable --now thinkfan && ok "thinkfan service disabled."
    fi

    # Remove privileged files
    [[ -f /usr/local/bin/thinkfan-set-level ]] && \
        sudo rm /usr/local/bin/thinkfan-set-level && ok "Removed helper script."
    [[ -f /etc/sudoers.d/thinkfan-gui ]] && \
        sudo rm /etc/sudoers.d/thinkfan-gui && ok "Removed sudoers rule."
    [[ -f /etc/modprobe.d/thinkpad.conf ]] && \
        sudo rm /etc/modprobe.d/thinkpad.conf && ok "Removed modprobe config."

    # Remove app files
    rm -rf "$REAL_HOME/.local/share/thinkpad-fan-control"
    rm -f  "$REAL_HOME/.local/share/applications/thinkfan-control.desktop"
    rm -f  "$REAL_HOME/.config/autostart/thinkfan-control.desktop"
    rm -rf "$REAL_HOME/.config/thinkfan-gui"

    echo ""
    echo -e "${BOLD}${GREEN}Uninstall complete.${NC}"
    echo -e "thinkfan package and /etc/thinkfan.conf were left in place."
    echo -e "Remove manually if needed: ${CYAN}sudo pacman -R thinkfan${NC} / ${CYAN}sudo apt remove thinkfan${NC}"
    exit 0
}

# ── main ─────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
    do_uninstall
fi

PM=$(detect_distro)
info "Detected package manager: $PM"

install_deps "$PM"
check_tray
setup_module
setup_helper
setup_thinkfan_config
setup_app
setup_service

echo ""
echo -e "${BOLD}${GREEN}Installation complete!${NC}"
echo -e "Launch the app: ${CYAN}python3 ~/.local/share/thinkpad-fan-control/thinkfan-control.py${NC}"
echo -e "Or find it in your application menu as ${BOLD}ThinkPad Fan Control${NC}."
echo ""
