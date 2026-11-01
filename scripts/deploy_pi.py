"""
Raspberry Pi Deployment & Remote Management Helper
Allows 1-command deployment, cross-OS Wolt session synchronization (decrypted cookies + localStorage),
log checking, and service restarting over SSH.
"""

import os
import sys
import json
import time
import subprocess
import argparse

DEFAULT_HOST = os.getenv("RPI_HOST", "192.168.1.116")
DEFAULT_USER = os.getenv("RPI_USER", "andres")
REMOTE_DIR = os.getenv("RPI_DIR", "/home/andres/wolt-smart-pantry")
SERVICE_NAME = "wolt-bot"

def _get_ssh_key_args():
    for key_name in ["hetzner_automation_key", "id_ed25519", "id_rsa"]:
        kp = os.path.expanduser(f"~/.ssh/{key_name}")
        if os.path.exists(kp):
            return ["-i", kp]
    return []

def run_ssh(cmd, host=DEFAULT_HOST, user=DEFAULT_USER, allocate_tty=False):
    target = f"{user}@{host}"
    ssh_cmd = ["ssh", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=no"]
    ssh_cmd.extend(_get_ssh_key_args())
    if allocate_tty:
        ssh_cmd.append("-tt")
    ssh_cmd.extend([target, cmd])
    print(f"[*] Executing on {target}: {cmd}")
    res = subprocess.run(ssh_cmd)
    return res.returncode

def copy_ssh_key(host=DEFAULT_HOST, user=DEFAULT_USER):
    pubkey_path = os.path.expanduser("~/.ssh/hetzner_automation_key.pub")
    if not os.path.exists(pubkey_path):
        pubkey_path = os.path.expanduser("~/.ssh/id_ed25519.pub")
    if not os.path.exists(pubkey_path):
        pubkey_path = os.path.expanduser("~/.ssh/id_rsa.pub")
    if not os.path.exists(pubkey_path):
        print("[!] No public SSH key found in ~/.ssh/")
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
    return run_ssh(cmd, host=host, user=user, allocate_tty=True)

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
    """
    Exports decrypted Wolt cookies & localStorage from local PC Chromium profile via Playwright
    and imports them directly into the Raspberry Pi's Chromium profile (solving Windows DPAPI vs Linux cookie encryption).
    """
    from playwright.sync_api import sync_playwright

    if user_id:
        local_profile = os.path.abspath(os.path.join("data", "users", str(user_id), ".wolt_profile"))
        remote_profile = f"{remote_dir}/data/users/{user_id}/.wolt_profile"
    else:
        local_profile = os.path.abspath(".wolt_profile")
        remote_profile = f"{remote_dir}/.wolt_profile"

    if not os.path.exists(local_profile):
        print(f"[!] Local profile directory not found at: {local_profile}")
        print("    Run 'python wolt_manager.py login" + (f" --user {user_id}'" if user_id else "'") + " first on your PC.")
        return 1

    print(f"[*] Extracting decrypted Wolt session (cookies + localStorage) from local profile: {local_profile}...")
    os.makedirs("scratch", exist_ok=True)
    state_file = os.path.join("scratch", "wolt_auth_state.json")

    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        context = p.chromium.launch_persistent_context(
            user_data_dir=local_profile,
            headless=True,
            args=args,
            viewport={"width": 1280, "height": 850}
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://wolt.com/en/est/tallinn/venue/wolt-market-maakri", wait_until="domcontentloaded")
        time.sleep(3.5)
        cookies = context.cookies()
        ls = page.evaluate("() => Object.assign({}, window.localStorage)")
        context.close()

    with open(state_file, "w", encoding="utf-8") as f:
        json.dump({"cookies": cookies, "localStorage": ls}, f)
    print(f"[+] Exported {len(cookies)} decrypted cookies and {len(ls)} localStorage keys.")

    importer_code = f'''import json, time, os
from playwright.sync_api import sync_playwright

profile_dir = {json.dumps(remote_profile)}
os.makedirs(profile_dir, exist_ok=True)
for root_dir, _, files in os.walk(profile_dir):
    for fn in files:
        if fn in ["LOCK", "SingletonLock", "SingletonSocket", "SingletonCookie"]:
            try:
                os.remove(os.path.join(root_dir, fn))
            except Exception:
                pass

with open("wolt_auth_state.json", "r", encoding="utf-8") as f:
    state = json.load(f)

with sync_playwright() as p:
    args = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    context = p.chromium.launch_persistent_context(
        user_data_dir=profile_dir,
        headless=True,
        args=args,
        viewport={{"width": 1280, "height": 850}}
    )
    page = context.pages[0] if context.pages else context.new_page()
    context.clear_cookies()
    valid_cookies = []
    for c in state.get("cookies", []):
        c_copy = dict(c)
        if c_copy.get("sameSite") not in ("Strict", "Lax", "None"):
            c_copy.pop("sameSite", None)
        valid_cookies.append(c_copy)
    context.add_cookies(valid_cookies)
    page.goto("https://wolt.com/en/est/tallinn/venue/wolt-market-maakri", wait_until="commit")
    time.sleep(2.5)
    page.evaluate(
        """(items) => {{
            for (const [k, v] of Object.entries(items)) {{
                try {{ window.localStorage.setItem(k, v); }} catch (e) {{}}
            }}
        }}""",
        state.get("localStorage", {{}})
    )
    page.reload(wait_until="commit")
    time.sleep(3.0)
    context.close()
print("[+] Successfully imported decrypted Wolt session into Raspberry Pi Chromium profile!")
'''
    importer_file = os.path.join("scratch", "import_pi_wolt.py")
    with open(importer_file, "w", encoding="utf-8") as f:
        f.write(importer_code)

    scp_cmd = ["scp", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=no"]
    scp_cmd.extend(_get_ssh_key_args())
    scp_cmd.extend([state_file, importer_file, f"{user}@{host}:{remote_dir}/"])
    res = subprocess.run(scp_cmd)
    if res.returncode != 0:
        return res.returncode

    rc = run_ssh(
        f"pkill -f chromium || true; cd {remote_dir} && ./venv/bin/python -u import_pi_wolt.py && rm -f wolt_auth_state.json import_pi_wolt.py && sudo systemctl restart {SERVICE_NAME}",
        host=host,
        user=user
    )
    try:
        os.remove(state_file)
        os.remove(importer_file)
    except Exception:
        pass
    return rc

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
