"""
AURA Unified One-Click Launcher & Service Manager.
Orchestrates:
  1. Local Neural Brain (llama-server with RTX 4060 CUDA, 16k context) on port 11434
  2. AURA Core API Server (FastAPI / 24/7 Daemon) on port 8000
  3. Web Console & Test Center on port 5050
"""
import os
import sys
import time
import socket
import urllib.request
import urllib.error
import json
import subprocess
import webbrowser
from pathlib import Path

# Force UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Paths
BASE_DIR = Path(__file__).resolve().parent
PYTHON_EXE = BASE_DIR / ".venv" / "Scripts" / "python.exe"
LLAMA_SERVER_EXE = Path(r"C:\llama-cuda\llama-server.exe")
MODEL_GGUF = BASE_DIR / "brains" / "aura-brain-v1" / "model.gguf"
AUTH_TOKEN = "6Swko2P0xuYCn76KOsIGHQtwRlwqrGwzdfFuXeFt-t0"

# Ports
PORT_LLAMA = 11434
PORT_CORE = 8000
PORT_WEB = 5050

# URLs
URL_WEB = f"http://localhost:{PORT_WEB}/aura_web_ui.html"
URL_CORE_READY = f"http://127.0.0.1:{PORT_CORE}/api/ready"
URL_LLAMA_HEALTH = f"http://127.0.0.1:{PORT_LLAMA}/health"

# ANSI Colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BOLD = "\033[1m"
RESET = "\033[0m"


def is_port_in_use(port: int) -> bool:
    """Check if a TCP port is listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def get_process_on_port(port: int):
    """Find psutil Process listening on given port."""
    try:
        import psutil
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                return psutil.Process(conn.pid)
    except Exception:
        pass
    return None


def check_url(url: str, timeout: float = 2.0) -> bool:
    """Check if URL returns 200."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status == 200
    except Exception:
        return False


def start_llama_server() -> bool:
    """Launch llama-server with RTX 4060 CUDA acceleration."""
    if is_port_in_use(PORT_LLAMA):
        if check_url(URL_LLAMA_HEALTH):
            print(f"  {GREEN}●{RESET} Local Neural Brain (RTX 4060): Đang chạy trên cổng {PORT_LLAMA}")
            return True

    if not LLAMA_SERVER_EXE.exists():
        print(f"  {RED}✖{RESET} Không tìm thấy {LLAMA_SERVER_EXE}!")
        return False

    if not MODEL_GGUF.exists():
        print(f"  {RED}✖{RESET} Không tìm thấy file model: {MODEL_GGUF}!")
        return False

    print(f"  {YELLOW}⏳{RESET} Đang nạp Local Neural Brain vào VRAM (RTX 4060 CUDA, 16k context)...")
    cmd = [
        str(LLAMA_SERVER_EXE),
        "-m", str(MODEL_GGUF),
        "--port", str(PORT_LLAMA),
        "--host", "127.0.0.1",
        "-ngl", "99",
        "-c", "16384"
    ]

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(cmd, cwd=str(BASE_DIR), creationflags=creationflags)

    # Wait for ready
    for _ in range(30):
        time.sleep(0.5)
        if check_url(URL_LLAMA_HEALTH):
            print(f"  {GREEN}✔{RESET} Local Neural Brain đã sẵn sàng trên cổng {PORT_LLAMA} (VRAM: ~2.1 GB)")
            return True

    print(f"  {RED}✖{RESET} Quá thời gian chờ Local Brain khởi động.")
    return False


def start_core_server() -> bool:
    """Launch AURA FastAPI server on port 8000."""
    if is_port_in_use(PORT_CORE):
        if check_url(URL_CORE_READY):
            print(f"  {GREEN}●{RESET} AURA Core API: Đang chạy trên cổng {PORT_CORE}")
            return True

    print(f"  {YELLOW}⏳{RESET} Đang khởi động AURA Core API (FastAPI, Offline Daemon)...")
    cmd = [str(PYTHON_EXE), "-m", "server.main"]

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(cmd, cwd=str(BASE_DIR), creationflags=creationflags)

    # Wait for ready
    for _ in range(30):
        time.sleep(0.5)
        if check_url(URL_CORE_READY):
            print(f"  {GREEN}✔{RESET} AURA Core API đã sẵn sàng trên cổng {PORT_CORE}")
            return True

    print(f"  {RED}✖{RESET} Quá thời gian chờ AURA Core khởi động.")
    return False


def start_web_ui() -> bool:
    """Launch Web UI static server on port 5050."""
    if is_port_in_use(PORT_WEB):
        print(f"  {GREEN}●{RESET} AURA Web Console: Đang phục vụ tại cổng {PORT_WEB}")
        return True

    print(f"  {YELLOW}⏳{RESET} Đang khởi động Web Console server...")
    cmd = [str(PYTHON_EXE), str(BASE_DIR / "run_web_ui.py")]

    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(cmd, cwd=str(BASE_DIR), creationflags=creationflags)

    for _ in range(10):
        time.sleep(0.3)
        if is_port_in_use(PORT_WEB):
            print(f"  {GREEN}✔{RESET} Web Console sẵn sàng tại cổng {PORT_WEB}")
            return True

    return is_port_in_use(PORT_WEB)


def stop_all_services():
    """Gracefully terminate all AURA processes to free GPU and RAM."""
    print(f"\n{YELLOW}Đang dừng tất cả các tiến trình AURA để giải phóng GPU và RAM...{RESET}")
    import psutil

    # 1. Kill by port
    for port, name in [(PORT_WEB, "Web Console"), (PORT_CORE, "AURA Core"), (PORT_LLAMA, "Neural Brain (llama-server)")]:
        proc = get_process_on_port(port)
        if proc:
            try:
                print(f"  {YELLOW}●{RESET} Đang dừng {name} (PID: {proc.pid})...")
                proc.terminate()
                proc.wait(timeout=3)
                print(f"  {GREEN}✔{RESET} Đã dừng {name}.")
            except Exception:
                try:
                    proc.kill()
                    print(f"  {GREEN}✔{RESET} Đã buộc dừng {name}.")
                except Exception as e:
                    print(f"  {RED}✖{RESET} Lỗi dừng {name}: {e}")

    # 2. Ensure llama-server.exe is gone
    for p in psutil.process_iter(["name", "pid"]):
        try:
            if p.info["name"] and "llama-server" in p.info["name"].lower():
                print(f"  {YELLOW}●{RESET} Dừng llama-server còn sót lại (PID: {p.pid})...")
                p.kill()
        except Exception:
            pass

    print(f"{GREEN}✔ Toàn bộ dịch vụ AURA đã được tắt an toàn! VRAM đã được giải phóng.{RESET}\n")


def print_banner():
    banner = f"""{CYAN}
    ╔══════════════════════════════════════════════════════════╗
    ║                 ⚡  A U R A   C O R E  ⚡                ║
    ║             Autonomous Offline AI Companion              ║
    ║           NVIDIA GeForce RTX 4060 Laptop GPU             ║
    ╚══════════════════════════════════════════════════════════╝{RESET}
    """
    print(banner)


def show_status():
    print(f"{BOLD}--- TRẠNG THÁI HỆ THỐNG ---{RESET}")
    llama_ok = is_port_in_use(PORT_LLAMA) and check_url(URL_LLAMA_HEALTH)
    core_ok = is_port_in_use(PORT_CORE) and check_url(URL_CORE_READY)
    web_ok = is_port_in_use(PORT_WEB)

    print(f"  • Local Neural Brain (Port {PORT_LLAMA}):  " + (f"{GREEN}● ONLINE (RTX 4060 CUDA){RESET}" if llama_ok else f"{RED}○ OFFLINE{RESET}"))
    print(f"  • AURA Core API      (Port {PORT_CORE}):   " + (f"{GREEN}● ONLINE (Offline Mode){RESET}" if core_ok else f"{RED}○ OFFLINE{RESET}"))
    print(f"  • Web Console        (Port {PORT_WEB}):   " + (f"{GREEN}● ONLINE{RESET}" if web_ok else f"{RED}○ OFFLINE{RESET}"))
    print("----------------------------\n")


def cli_chat():
    """Quick interactive CLI chat turn with AURA."""
    print(f"\n{MAGENTA}=== TRÒ CHUYỆN TRỰC TIẾP VỚI AURA (Gõ 'exit' để quay lại menu) ==={RESET}\n")
    session_id = f"cli_session_{int(time.time())}"

    while True:
        try:
            user_input = input(f"{CYAN}Anh > {RESET}").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q", "quay lai"):
                break

            t0 = time.time()
            payload = json.dumps({"message": user_input, "session_id": session_id}).encode("utf-8")
            req = urllib.request.Request(
                f"http://127.0.0.1:{PORT_CORE}/api/chat",
                data=payload,
                headers={
                    "Authorization": f"Bearer {AUTH_TOKEN}",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=30) as res:
                body = json.loads(res.read().decode("utf-8"))
                elapsed = time.time() - t0
                reply = body.get("reply", "...")
                provider = body.get("metadata", {}).get("provider", "local_aura")
                print(f"\n{GREEN}AURA [{provider} · {elapsed:.2f}s]:{RESET}\n{reply}\n")
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"{RED}Lỗi truy vấn: {e}{RESET}\n")


def launch():
    """Main start procedure."""
    print_banner()
    print(f"{BOLD}Đang kiểm tra và khởi động các thành phần AURA...{RESET}")

    # 1. Neural Brain
    start_llama_server()

    # 2. Core Server
    start_core_server()

    # 3. Web UI
    start_web_ui()

    print(f"\n{GREEN}{BOLD}✔ TẤT CẢ THÀNH PHẦN ĐÃ SẴN SÀNG!{RESET}")
    print(f"  👉 Web Console: {CYAN}{URL_WEB}{RESET}")
    print(f"  👉 Core Server: {CYAN}http://localhost:{PORT_CORE}{RESET}")

    # Open browser automatically
    try:
        webbrowser.open(URL_WEB)
    except Exception:
        pass

    # Interactive loop
    while True:
        print(f"\n{BOLD}CÁC TÙY CHỌN:{RESET}")
        print(f"  [{CYAN}1{RESET}] Mở lại Web Console trên Trình duyệt")
        print(f"  [{CYAN}2{RESET}] Trò chuyện trực tiếp với AURA ngay tại Console này")
        print(f"  [{CYAN}3{RESET}] Xem trạng thái & Health Check")
        print(f"  [{CYAN}4{RESET}] Khởi động lại toàn bộ dịch vụ (Restart)")
        print(f"  [{CYAN}5{RESET}] Tắt toàn bộ dịch vụ và giải phóng GPU (Stop/Shutdown)")
        print(f"  [{CYAN}0{RESET}] Thoát cửa sổ này (AURA vẫn tiếp tục chạy ngầm)")

        choice = input(f"\n{BOLD}Chọn số [0-5]: {RESET}").strip()

        if choice == "1":
            webbrowser.open(URL_WEB)
            print(f"{GREEN}Đã mở {URL_WEB} trên trình duyệt.{RESET}")
        elif choice == "2":
            cli_chat()
        elif choice == "3":
            show_status()
        elif choice == "4":
            stop_all_services()
            time.sleep(1)
            start_llama_server()
            start_core_server()
            start_web_ui()
            show_status()
        elif choice == "5":
            stop_all_services()
            break
        elif choice == "0":
            print(f"{GREEN}AURA vẫn đang chạy ngầm phục vụ anh nhé! Chúc anh một ngày tuyệt vời! 💕{RESET}")
            break
        else:
            print(f"{YELLOW}Lựa chọn không hợp lệ, vui lòng chọn từ 0 đến 5.{RESET}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg in ("--stop", "-s", "stop"):
            stop_all_services()
            sys.exit(0)
        elif arg in ("--status", "status"):
            show_status()
            sys.exit(0)
        elif arg in ("--restart", "restart"):
            stop_all_services()
            time.sleep(1)
            launch()
            sys.exit(0)

    launch()
