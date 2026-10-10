"""
Raspberry Pi Deployment & Remote Management Helper
Allows 1-command deployment, synchronization, log checking, and service restarting over SSH.
"""

import os
import sys
import subprocess
import argparse

DEFAULT_HOST = os.getenv("RPI_HOST", "192.168.1.116")
DEFAULT_USER = os.getenv("RPI_USER", "andres")
REMOTE_DIR = os.getenv("RPI_DIR", "/home/andres/wolt-smart-pantry")
SERVICE_NAME = "wolt-bot"

def run_ssh(cmd, host=DEFAULT_HOST, user=DEFAULT_USER, allocate_tty=True):
    target = f"{user}@{host}"
    ssh_cmd = ["ssh", "-o", "ConnectTimeout=15"]
    if allocate_tty:
        ssh_cmd.append("-tt")
    ssh_cmd.extend([target, cmd])
    print(f"[*] Executing on {target}: {cmd}")
    res = subprocess.run(ssh_cmd)
    return res.returncode

def copy_ssh_key(host=DEFAULT_HOST, user=DEFAULT_USER):
    pubkey_path = os.path.expanduser("~/.ssh/id_ed25519.pub")
    if not os.path.exists(pubkey_path):
        pubkey_path = os.path.expanduser("~/.ssh/id_rsa.pub")
    if not os.path.exists(pubkey_path):
        print("[!] No public SSH key found in ~/.ssh/ (checked id_ed25519.pub and id_rsa.pub)")
        return 1

    with open(pubkey_path, "r", encoding="utf-8") as f:
        pubkey = f.read().strip()

    target = f"{user}@{host}"
    print(f"[*] Installing public key ({pubkey_path}) onto {target}...")
    remote_script = f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '{pubkey}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
    return run_ssh(remote_script, host=host, user=user)

def setup_sudo(host=DEFAULT_HOST, user=DEFAULT_USER):
    """Configures passwordless sudo for wolt-bot service management on the Raspberry Pi."""
    target = f"{user}@{host}"
    print(f"[*] Configuring passwordless sudo for '{SERVICE_NAME}' service on {target}...")
    rule = f"{user} ALL=(ALL) NOPASSWD: /bin/systemctl restart {SERVICE_NAME}, /bin/systemctl is-active {SERVICE_NAME}, /bin/systemctl status {SERVICE_NAME}, /bin/systemctl start {SERVICE_NAME}, /bin/systemctl stop {SERVICE_NAME}, /usr/bin/systemctl restart {SERVICE_NAME}, /usr/bin/systemctl is-active {SERVICE_NAME}, /usr/bin/systemctl status {SERVICE_NAME}"
    cmd = f"echo '{rule}' | sudo tee /etc/sudoers.d/{SERVICE_NAME} && sudo chmod 440 /etc/sudoers.d/{SERVICE_NAME}"
    return run_ssh(cmd, host=host, user=user)

def deploy(host=DEFAULT_HOST, user=DEFAULT_USER, remote_dir=REMOTE_DIR):
    target = f"{user}@{host}"
    print(f"[*] Deploying latest code to {target}:{remote_dir}...")
    deploy_cmd = (
        f"cd {remote_dir} && "
        f"git pull origin main && "
        f"if [ -d venv ]; then venv/bin/pip install -q -r requirements.txt; fi && "
        f"sudo systemctl restart {SERVICE_NAME} && "
        f"systemctl is-active {SERVICE_NAME}"
    )
    return run_ssh(deploy_cmd, host=host, user=user)

def status(host=DEFAULT_HOST, user=DEFAULT_USER):
    status_cmd = f"sudo systemctl status {SERVICE_NAME} --no-pager"
    return run_ssh(status_cmd, host=host, user=user)

def logs(host=DEFAULT_HOST, user=DEFAULT_USER, lines=30):
    logs_cmd = f"journalctl -u {SERVICE_NAME} -n {lines} --no-pager"
    return run_ssh(logs_cmd, host=host, user=user)

def sync_profile(user_id=None, host=DEFAULT_HOST, user=DEFAULT_USER, remote_dir=REMOTE_DIR):
    """Syncs the authenticated Wolt browser profile from PC to Raspberry Pi over SCP."""
    if user_id:
        local_profile = os.path.join("data", "users", str(user_id), ".wolt_profile")
        remote_target_dir = f"{remote_dir}/data/users/{user_id}"
    else:
        local_profile = ".wolt_profile"
        remote_target_dir = remote_dir

    if not os.path.exists(local_profile):
        print(f"[!] Local profile directory not found at: {local_profile}")
        print("    Run 'python wolt_manager.py login" + (f" --user {user_id}'" if user_id else "'") + " first on your PC.")
        return 1

    print(f"[*] Preparing remote directory on {user}@{host}:{remote_target_dir}...")
    run_ssh(f"mkdir -p {remote_target_dir}", host=host, user=user)

    remote_dest = f"{user}@{host}:{remote_target_dir}/"
    print(f"[*] Copying {local_profile} -> {remote_dest}...")
    scp_cmd = ["scp", "-r", local_profile, remote_dest]
    res = subprocess.run(scp_cmd)
    if res.returncode == 0:
        print(f"[+] Wolt profile synced successfully to Raspberry Pi!")
        print(f"[*] Restarting '{SERVICE_NAME}' service on Pi to apply active session...")
        run_ssh(f"sudo systemctl restart {SERVICE_NAME}", host=host, user=user)
        print("[+] Done! Check with: python scripts/deploy_pi.py logs")
    return res.returncode

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Raspberry Pi Deployment & SSH Manager")
    parser.add_argument("action", choices=["deploy", "status", "logs", "setup-key", "setup-sudo", "exec", "sync-profile"], default="deploy", nargs="?", help="Action to perform")
    parser.add_argument("--user-id", "--user", dest="target_user_id", type=int, help="Telegram User ID (e.g. 7249779280) for profile synchronization")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Raspberry Pi IP / Hostname (default: {DEFAULT_HOST})")
    parser.add_argument("--rpi-user", default=DEFAULT_USER, help=f"Raspberry Pi SSH User (default: {DEFAULT_USER})")
    parser.add_argument("--dir", default=REMOTE_DIR, help=f"Remote repository path (default: {REMOTE_DIR})")
    parser.add_argument("--cmd", help="Custom command to run on Pi (for 'exec' action)")
    parser.add_argument("-n", "--lines", type=int, default=30, help="Number of journal log lines to display")

    args = parser.parse_args()

    if args.action == "setup-key":
        sys.exit(copy_ssh_key(host=args.host, user=args.rpi_user))
    elif args.action == "setup-sudo":
        sys.exit(setup_sudo(host=args.host, user=args.rpi_user))
    elif args.action == "deploy":
        sys.exit(deploy(host=args.host, user=args.rpi_user, remote_dir=args.dir))
    elif args.action == "status":
        sys.exit(status(host=args.host, user=args.rpi_user))
    elif args.action == "logs":
        sys.exit(logs(host=args.host, user=args.rpi_user, lines=args.lines))
    elif args.action == "sync-profile":
        uid = args.target_user_id
        if not uid:
            users_dir = os.path.join("data", "users")
            if os.path.exists(users_dir):
                active = [d for d in os.listdir(users_dir) if d.isdigit()]
                if len(active) == 1:
                    uid = int(active[0])
                    print(f"[*] Auto-detected active Telegram User ID: {uid}")
        sys.exit(sync_profile(user_id=uid, host=args.host, user=args.rpi_user, remote_dir=args.dir))
    elif args.action == "exec":
        if not args.cmd:
            print("[!] Please specify --cmd '<command>'")
            sys.exit(1)
        sys.exit(run_ssh(args.cmd, host=args.host, user=args.rpi_user))
