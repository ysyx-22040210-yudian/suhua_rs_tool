# Shared X11/Xwayland session discovery for the Verdi launch scripts.
# This file is sourced; it does not set shell options, traps, or exit.

GUI_USER="${GUI_USER-}"
GUI_DISPLAY="${GUI_DISPLAY-}"
GUI_XAUTHORITY_IS_SET="${GUI_XAUTHORITY+x}"
if [ -z "$GUI_XAUTHORITY_IS_SET" ]; then
  GUI_XAUTHORITY="${XAUTHORITY:-}"
fi
GUI_DBUS_SESSION_BUS_ADDRESS="${GUI_DBUS_SESSION_BUS_ADDRESS:-${DBUS_SESSION_BUS_ADDRESS:-}}"
GUI_XDG_RUNTIME_DIR="${GUI_XDG_RUNTIME_DIR:-${XDG_RUNTIME_DIR:-}}"
GUI_SESSION_PID="${GUI_SESSION_PID-}"
GUI_SESSION_PATTERN="${GUI_SESSION_PATTERN:-gnome-session-binary|gnome-session|gnome-shell|startplasma|plasmashell|ksmserver|kwin_x11|kwin_wayland|xfce4-session|mate-session|cinnamon|lxsession|lxqt-session|Xwayland}"
GUI_PROC_ROOT="${GUI_PROC_ROOT:-/proc}"
GUI_PROBE_TIMEOUT="${GUI_PROBE_TIMEOUT:-5}"

gui_session_error() {
  echo "ERROR: $*" >&2
}

gui_session_read_process_env() {
  local process_id=$1
  local variable_name=$2
  local entry

  while IFS= read -r -d '' entry; do
    case "$entry" in
      "$variable_name="*)
        printf '%s\n' "${entry#*=}"
        return 0
        ;;
    esac
  done <"$GUI_PROC_ROOT/$process_id/environ" 2>/dev/null
  return 1
}

gui_session_set_optional_env() {
  local variable_name=$1
  local variable_value=$2
  if [ -n "$variable_value" ]; then
    export "$variable_name=$variable_value"
  else
    unset "$variable_name"
  fi
}

gui_session_probe_values() {
  local candidate_display=$1
  local candidate_xauthority=$2

  (
    export DISPLAY="$candidate_display"
    gui_session_set_optional_env XAUTHORITY "$candidate_xauthority"
    timeout "$GUI_PROBE_TIMEOUT" xdpyinfo >/dev/null 2>&1
  )
}

gui_session_probe_process() {
  local process_id=$1
  local process_owner
  local candidate_display
  local candidate_xauthority
  local candidate_dbus
  local candidate_runtime
  local candidate_home

  [ -r "$GUI_PROC_ROOT/$process_id/environ" ] || return 1
  candidate_display="$(gui_session_read_process_env "$process_id" DISPLAY || true)"
  [ -n "$candidate_display" ] || return 1
  if [ -n "$GUI_DISPLAY" ] && [ "$candidate_display" != "$GUI_DISPLAY" ]; then
    return 1
  fi
  if [ -z "$GUI_USER" ] && [ -z "$GUI_DISPLAY" ] && [ -z "$GUI_SESSION_PID" ]; then
    case "$candidate_display" in
      :[0-9]*|unix:[0-9]*|unix/:[0-9]*) ;;
      *) return 1 ;;
    esac
  fi

  process_owner="$(ps -o user:64= -p "$process_id" 2>/dev/null | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || true)"
  [ -n "$process_owner" ] || process_owner=unknown
  if [ -n "$GUI_USER" ] && [ "$process_owner" != "$GUI_USER" ]; then
    return 1
  fi
  if [ -z "$GUI_USER" ]; then
    case "$process_owner" in
      gdm|Debian-gdm|sddm|lightdm) return 1 ;;
    esac
  fi

  if [ -n "$GUI_XAUTHORITY_IS_SET" ]; then
    candidate_xauthority="$GUI_XAUTHORITY"
  else
    candidate_xauthority="$(gui_session_read_process_env "$process_id" XAUTHORITY || true)"
  fi
  candidate_dbus="$(gui_session_read_process_env "$process_id" DBUS_SESSION_BUS_ADDRESS || true)"
  candidate_runtime="$(gui_session_read_process_env "$process_id" XDG_RUNTIME_DIR || true)"
  candidate_home="$(gui_session_read_process_env "$process_id" HOME || true)"
  if [ -z "$GUI_XAUTHORITY_IS_SET" ] && [ -z "$candidate_xauthority" ] &&
     [ -n "$candidate_home" ] && [ -r "$candidate_home/.Xauthority" ]; then
    candidate_xauthority="$candidate_home/.Xauthority"
  fi

  gui_session_probe_values "$candidate_display" "$candidate_xauthority" || return 1

  GUI_SESSION_CANDIDATE_PID="$process_id"
  GUI_SESSION_CANDIDATE_USER="$process_owner"
  GUI_SESSION_CANDIDATE_DISPLAY="$candidate_display"
  GUI_SESSION_CANDIDATE_XAUTHORITY="$candidate_xauthority"
  GUI_SESSION_CANDIDATE_DBUS="$candidate_dbus"
  GUI_SESSION_CANDIDATE_RUNTIME="$candidate_runtime"
  return 0
}

gui_session_record_candidate() {
  local process_id=$1
  local session_key
  local quick_display

  case " $GUI_SESSION_SEEN_PIDS " in
    *" $process_id "*) return ;;
  esac
  GUI_SESSION_SEEN_PIDS="$GUI_SESSION_SEEN_PIDS $process_id"

  quick_display="$(gui_session_read_process_env "$process_id" DISPLAY || true)"
  if [ -n "$quick_display" ]; then
    session_key="|$quick_display|"
    case "$GUI_SESSION_FOUND_KEYS" in
      *"$session_key"*) return ;;
    esac
  fi

  gui_session_probe_process "$process_id" || return 0
  session_key="|$GUI_SESSION_CANDIDATE_DISPLAY|"
  case "$GUI_SESSION_FOUND_KEYS" in
    *"$session_key"*) return ;;
  esac

  GUI_SESSION_FOUND_KEYS="$GUI_SESSION_FOUND_KEYS$session_key"
  GUI_SESSION_AVAILABLE="${GUI_SESSION_AVAILABLE}${GUI_SESSION_CANDIDATE_USER} DISPLAY=${GUI_SESSION_CANDIDATE_DISPLAY} PID=${GUI_SESSION_CANDIDATE_PID}\n"
  if [ -z "$GUI_SESSION_FOUND_DISPLAY" ]; then
    GUI_SESSION_FOUND_PID="$GUI_SESSION_CANDIDATE_PID"
    GUI_SESSION_FOUND_USER="$GUI_SESSION_CANDIDATE_USER"
    GUI_SESSION_FOUND_DISPLAY="$GUI_SESSION_CANDIDATE_DISPLAY"
    GUI_SESSION_FOUND_XAUTHORITY="$GUI_SESSION_CANDIDATE_XAUTHORITY"
    GUI_SESSION_FOUND_DBUS="$GUI_SESSION_CANDIDATE_DBUS"
    GUI_SESSION_FOUND_RUNTIME="$GUI_SESSION_CANDIDATE_RUNTIME"
  else
    GUI_SESSION_AMBIGUOUS=1
  fi
}

gui_session_resolve() {
  local initial_display="${DISPLAY:-}"
  local current_user="${USER:-${LOGNAME:-current-user}}"
  local candidate_pids=""
  local process_dir
  local process_id

  case "$GUI_PROBE_TIMEOUT" in
    ''|*[!0-9]*) gui_session_error "GUI_PROBE_TIMEOUT must be a positive integer"; return 1 ;;
  esac
  if [ "$GUI_PROBE_TIMEOUT" -le 0 ]; then
    gui_session_error "GUI_PROBE_TIMEOUT must be greater than zero"
    return 1
  fi
  for command_name in sed xdpyinfo timeout; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
      gui_session_error "required GUI probe command not found: $command_name"
      return 1
    fi
  done

  if [ -z "$GUI_SESSION_PID" ] &&
     { [ -z "$GUI_USER" ] || [ "$GUI_USER" = "$current_user" ]; } &&
     { [ -z "$GUI_DISPLAY" ] || [ "$GUI_DISPLAY" = "$initial_display" ]; } &&
     [ -n "$initial_display" ] && gui_session_probe_values "$initial_display" "$GUI_XAUTHORITY"; then
    gui_session_set_optional_env XAUTHORITY "$GUI_XAUTHORITY"
    gui_session_set_optional_env DBUS_SESSION_BUS_ADDRESS "$GUI_DBUS_SESSION_BUS_ADDRESS"
    gui_session_set_optional_env XDG_RUNTIME_DIR "$GUI_XDG_RUNTIME_DIR"
    GUI_SESSION_SOURCE="current environment"
    GUI_SESSION_SELECTED_USER="$current_user"
    return 0
  fi

  if [ -z "$GUI_SESSION_PID" ] && [ -n "$GUI_DISPLAY" ] &&
     gui_session_probe_values "$GUI_DISPLAY" "$GUI_XAUTHORITY"; then
    export DISPLAY="$GUI_DISPLAY"
    gui_session_set_optional_env XAUTHORITY "$GUI_XAUTHORITY"
    gui_session_set_optional_env DBUS_SESSION_BUS_ADDRESS "$GUI_DBUS_SESSION_BUS_ADDRESS"
    gui_session_set_optional_env XDG_RUNTIME_DIR "$GUI_XDG_RUNTIME_DIR"
    GUI_SESSION_SOURCE="explicit GUI_DISPLAY"
    GUI_SESSION_SELECTED_USER="${GUI_USER:-$current_user}"
    return 0
  fi

  if ! command -v ps >/dev/null 2>&1; then
    gui_session_error "ps is required when the current DISPLAY is unusable"
    return 1
  fi
  if [ -z "$GUI_SESSION_PID" ] && ! command -v pgrep >/dev/null 2>&1; then
    gui_session_error "pgrep is required for automatic GUI discovery"
    return 1
  fi

  GUI_SESSION_SEEN_PIDS=""
  GUI_SESSION_FOUND_KEYS=""
  GUI_SESSION_AVAILABLE=""
  GUI_SESSION_FOUND_PID=""
  GUI_SESSION_FOUND_USER=""
  GUI_SESSION_FOUND_DISPLAY=""
  GUI_SESSION_FOUND_XAUTHORITY=""
  GUI_SESSION_FOUND_DBUS=""
  GUI_SESSION_FOUND_RUNTIME=""
  GUI_SESSION_AMBIGUOUS=0

  if [ -n "$GUI_SESSION_PID" ]; then
    case "$GUI_SESSION_PID" in
      *[!0-9]*|'') gui_session_error "GUI_SESSION_PID must be a numeric process ID"; return 1 ;;
    esac
    gui_session_record_candidate "$GUI_SESSION_PID"
  else
    if [ -n "$GUI_USER" ]; then
      candidate_pids="$(pgrep -u "$GUI_USER" -f "$GUI_SESSION_PATTERN" 2>/dev/null || true)"
    else
      candidate_pids="$(pgrep -f "$GUI_SESSION_PATTERN" 2>/dev/null || true)"
    fi
    for process_id in $candidate_pids; do
      gui_session_record_candidate "$process_id"
    done

    for process_dir in "$GUI_PROC_ROOT"/[0-9]*; do
      [ -d "$process_dir" ] || continue
      process_id="${process_dir##*/}"
      gui_session_record_candidate "$process_id"
    done
  fi

  if [ "$GUI_SESSION_AMBIGUOUS" -ne 0 ] && [ -z "$GUI_DISPLAY" ]; then
    echo "ERROR: multiple usable X11 displays were found:" >&2
    printf '%b' "$GUI_SESSION_AVAILABLE" >&2
    echo "Set GUI_DISPLAY to the intended DISPLAY, or run from that graphical/SSH-X shell." >&2
    return 1
  fi

  if [ -z "$GUI_SESSION_FOUND_DISPLAY" ]; then
    echo "ERROR: no usable X11 display was found." >&2
    echo "Current DISPLAY=${initial_display:-<unset>} did not pass xdpyinfo." >&2
    echo "Run the launcher with --probe-only from a graphical terminal," >&2
    echo "connect with 'ssh -Y', or set GUI_USER / GUI_DISPLAY / GUI_SESSION_PID explicitly." >&2
    echo "Wayland sessions require a working Xwayland DISPLAY for this Verdi release." >&2
    [ -z "$GUI_USER" ] || echo "GUI_USER filter: $GUI_USER" >&2
    return 1
  fi

  export DISPLAY="$GUI_SESSION_FOUND_DISPLAY"
  gui_session_set_optional_env XAUTHORITY "$GUI_SESSION_FOUND_XAUTHORITY"
  gui_session_set_optional_env DBUS_SESSION_BUS_ADDRESS "$GUI_SESSION_FOUND_DBUS"
  gui_session_set_optional_env XDG_RUNTIME_DIR "$GUI_SESSION_FOUND_RUNTIME"
  GUI_SESSION_SOURCE="process $GUI_SESSION_FOUND_PID"
  GUI_SESSION_SELECTED_USER="$GUI_SESSION_FOUND_USER"
  return 0
}
