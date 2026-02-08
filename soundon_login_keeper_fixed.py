import asyncio
import tkinter as tk
from tkinter import messagebox
import threading
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError


# ================= CONFIG =================
LOGIN_URL = "https://www.soundon.global/login?lang=en&region=VN"
HEADLESS = False
SLOW_MO = 80
NAV_TIMEOUT = 120_000
ACTION_TIMEOUT = 60_000

SEL_EMAIL = 'input[name="username"], input[type="email"]'
SEL_PASS = 'input[type="password"]'
SEL_LOGIN_BTN = 'button:has-text("Log in")'


# ================= PROXY PARSER =================
def parse_proxy(proxy_str: str) -> dict:
    """
    Parse proxy string in format: ip:port or ip:port:user:pass
    Returns dict with server, username, password
    """
    if not proxy_str or not proxy_str.strip():
        return None
    
    parts = proxy_str.strip().split(':')
    if len(parts) < 2:
        return None
    
    ip = parts[0].strip()
    try:
        port = int(parts[1].strip())
    except ValueError:
        return None
    
    if not ip or port <= 0:
        return None
    
    # Check if has username and password
    username = parts[2].strip() if len(parts) >= 3 else None
    password = ':'.join(parts[3:]) if len(parts) >= 4 else None
    
    return {
        'server': f'http://{ip}:{port}',  # HTTP proxy format
        'username': username,
        'password': password
    }


# ================= PLAYWRIGHT =================
async def type_human(locator, text: str, delay=80):
    """Type text like a human"""
    await locator.click()
    try:
        await locator.fill("")
    except:
        pass
    await locator.type(text, delay=delay)


async def login_and_click(email: str, password: str, delay: float, stop_event, tab_number: int, proxy_config: dict = None):
    """
    Open browser, login, and maintain login state
    Auto-detect logout and re-login immediately
    Reload page every 1 second to check login status
    Support HTTP/HTTPS proxy
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            slow_mo=SLOW_MO,
            args=["--disable-blink-features=AutomationControlled"]
        )

        # Create context with proxy if provided
        context_options = {
            "viewport": {"width": 1280, "height": 720}
        }
        
        if proxy_config:
            context_options["proxy"] = proxy_config
            print(f"[Tab {tab_number}] Using proxy: {proxy_config.get('server', 'N/A')}")

        context = await browser.new_context(**context_options)
        page = await context.new_page()
        page.set_default_timeout(ACTION_TIMEOUT)

        async def do_login():
            """Perform login action"""
            try:
                print(f"[Tab {tab_number}] Logging in...")
                await page.goto(LOGIN_URL, timeout=NAV_TIMEOUT)
                await page.wait_for_selector(SEL_EMAIL, timeout=10000)
                await page.wait_for_selector(SEL_PASS, timeout=10000)

                # Type email and password
                await type_human(page.locator(SEL_EMAIL).first, email)
                await type_human(page.locator(SEL_PASS).first, password)
                
                # Click login button
                await page.locator(SEL_LOGIN_BTN).first.click()
                
                # Wait for login to complete
                await page.wait_for_timeout(5000)
                
                print(f"[Tab {tab_number}] Login completed. Current URL: {page.url}")
                return True
            except Exception as e:
                print(f"[Tab {tab_number}] Error during login: {e}")
                return False

        async def is_logged_in():
            """Check if still logged in by checking current URL and page content"""
            try:
                current_url = page.url.lower()
                
                # If on login page, user is definitely logged out
                if "/login" in current_url:
                    return False
                
                # Check if on library, profile, or other authenticated pages
                authenticated_paths = ['/library', '/profile', '/analytics', '/releases', '/promotion', '/accounts-management']
                if any(path in current_url for path in authenticated_paths):
                    return True
                
                # If on soundon.global but uncertain, check for login button
                if "soundon.global" in current_url:
                    try:
                        login_btn_count = await page.locator(SEL_LOGIN_BTN).count()
                        # If login button exists, user is logged out
                        if login_btn_count > 0:
                            return False
                        else:
                            return True
                    except:
                        # If can't check, assume logged in
                        return True
                
                # Unknown state - assume logged out to be safe
                return False
                
            except Exception as e:
                print(f"[Tab {tab_number}] Error checking login status: {e}")
                return False

        try:
            # Initial login
            success = await do_login()
            if not success:
                print(f"[Tab {tab_number}] Initial login failed!")
                return
            
            print(f"[Tab {tab_number}] Maintaining login state. Checking every {delay}s...")
            
            check_count = 0
            login_count = 1
            
            # Keep checking login status and maintain it
            while not stop_event.is_set():
                try:
                    check_count += 1
                    
                    # Reload page to check if still logged in
                    try:
                        await page.reload(timeout=30000)  # 30 second timeout for reload
                        await page.wait_for_timeout(2000)  # Wait 2 seconds after reload
                    except PlaywrightTimeoutError:
                        print(f"[Tab {tab_number}] Reload timeout, retrying...")
                        await asyncio.sleep(delay)
                        continue
                    
                    # Check if still logged in
                    logged_in = await is_logged_in()
                    
                    if logged_in:
                        print(f"[Tab {tab_number}] Check #{check_count}: Still logged in OK (URL: {page.url})")
                    else:
                        print(f"[Tab {tab_number}] Check #{check_count}: LOGGED OUT! Re-logging in...")
                        
                        # Re-login immediately
                        success = await do_login()
                        if success:
                            login_count += 1
                            print(f"[Tab {tab_number}] Re-login successful! (Total logins: {login_count})")
                        else:
                            print(f"[Tab {tab_number}] Re-login failed! Will retry on next check.")
                    
                    # Wait for the specified delay before next check
                    await asyncio.sleep(delay)
                    
                except Exception as e:
                    print(f"[Tab {tab_number}] Error during check cycle: {e}")
                    # Try to recover by checking URL
                    try:
                        logged_in = await is_logged_in()
                        if not logged_in:
                            print(f"[Tab {tab_number}] Attempting recovery login...")
                            await do_login()
                    except:
                        pass
                    await asyncio.sleep(delay)

        except PlaywrightTimeoutError as e:
            print(f"[Tab {tab_number}] Timeout error: {e}")
        except Exception as e:
            print(f"[Tab {tab_number}] Error: {e}")
        finally:
            # Keep browser open - don't close it
            print(f"[Tab {tab_number}] Stop signal received. Browser will remain open.")
            # Wait indefinitely until user manually closes
            while not stop_event.is_set():
                await asyncio.sleep(1)


def run_login_worker(email, password, delay, stop_event, tab_number, proxy_config):
    """Wrapper to run async function in thread"""
    asyncio.run(login_and_click(email, password, delay, stop_event, tab_number, proxy_config))


# ================= UI =================
class LoginApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SoundOn Login Keeper - Maintain Login State | Proxy Support")
        self.geometry("550x450")
        self.resizable(False, False)

        self.stop_event = threading.Event()
        self.login_threads = []
        self.is_running = False

        self.build_ui()

    def build_ui(self):
        # Title
        title_label = tk.Label(
            self,
            text="SoundOn Login Keeper",
            font=("Arial", 16, "bold"),
            pady=20
        )
        title_label.pack()

        # Input frame
        input_frame = tk.Frame(self)
        input_frame.pack(pady=10, padx=30, fill="x")

        # Email input
        tk.Label(input_frame, text="Email:", font=("Arial", 11), width=12, anchor="w").grid(
            row=0, column=0, sticky="w", pady=10
        )
        self.email_entry = tk.Entry(input_frame, font=("Arial", 11), width=33)
        self.email_entry.grid(row=0, column=1, pady=10, padx=5)

        # Password input
        tk.Label(input_frame, text="Password:", font=("Arial", 11), width=12, anchor="w").grid(
            row=1, column=0, sticky="w", pady=10
        )
        self.password_entry = tk.Entry(input_frame, font=("Arial", 11), width=33, show="*")
        self.password_entry.grid(row=1, column=1, pady=10, padx=5)

        # Check interval input
        tk.Label(input_frame, text="Check Every:", font=("Arial", 11), width=12, anchor="w").grid(
            row=2, column=0, sticky="w", pady=10
        )
        self.delay_entry = tk.Entry(input_frame, font=("Arial", 11), width=33)
        self.delay_entry.insert(0, "1")  # Default 1 second
        self.delay_entry.grid(row=2, column=1, pady=10, padx=5)

        # Number of tabs input
        tk.Label(input_frame, text="Tabs:", font=("Arial", 11), width=12, anchor="w").grid(
            row=3, column=0, sticky="w", pady=10
        )
        self.tabs_entry = tk.Entry(input_frame, font=("Arial", 11), width=33)
        self.tabs_entry.insert(0, "1")  # Default 1 tab
        self.tabs_entry.grid(row=3, column=1, pady=10, padx=5)

        # Proxy input
        tk.Label(input_frame, text="Proxy:", font=("Arial", 11), width=12, anchor="w").grid(
            row=4, column=0, sticky="w", pady=10
        )
        self.proxy_entry = tk.Entry(input_frame, font=("Arial", 11), width=33)
        self.proxy_entry.grid(row=4, column=1, pady=10, padx=5)
        
        # Proxy hint
        tk.Label(
            input_frame, 
            text="Format: ip:port or ip:port:user:pass (Optional)", 
            font=("Arial", 8), 
            fg="gray"
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=5)

        # Button frame
        button_frame = tk.Frame(self)
        button_frame.pack(pady=20)

        # Login button
        self.login_btn = tk.Button(
            button_frame,
            text="Start Login Keeper",
            font=("Arial", 12, "bold"),
            bg="#4CAF50",
            fg="white",
            width=18,
            height=2,
            command=self.start_login
        )
        self.login_btn.grid(row=0, column=0, padx=10)

        # Stop button
        self.stop_btn = tk.Button(
            button_frame,
            text="Stop",
            font=("Arial", 12, "bold"),
            bg="#f44336",
            fg="white",
            width=18,
            height=2,
            command=self.stop_login,
            state="disabled"
        )
        self.stop_btn.grid(row=0, column=1, padx=10)

        # Status label
        self.status_label = tk.Label(
            self,
            text="Ready to start",
            font=("Arial", 10),
            fg="gray"
        )
        self.status_label.pack(pady=10)

    def start_login(self):
        email = self.email_entry.get().strip()
        password = self.password_entry.get().strip()
        delay_str = self.delay_entry.get().strip()
        tabs_str = self.tabs_entry.get().strip()
        proxy_str = self.proxy_entry.get().strip()

        # Validate inputs
        if not email or not password:
            messagebox.showerror("Error", "Please enter both email and password!")
            return

        # Validate delay
        try:
            delay = float(delay_str)
            if delay <= 0:
                raise ValueError("Delay must be positive")
        except ValueError:
            messagebox.showerror("Error", "Check interval must be a positive number (e.g., 1, 0.5, 2.5)!")
            return

        # Validate number of tabs
        try:
            num_tabs = int(tabs_str)
            if num_tabs <= 0 or num_tabs > 10:
                raise ValueError("Tabs must be between 1 and 10")
        except ValueError:
            messagebox.showerror("Error", "Number of tabs must be between 1 and 10!")
            return

        # Parse proxy if provided
        proxy_config = None
        if proxy_str:
            proxy_config = parse_proxy(proxy_str)
            if not proxy_config:
                messagebox.showerror(
                    "Proxy Error",
                    "Invalid proxy format!\n\n"
                    "Valid formats:\n"
                    "• ip:port (e.g., 103.152.112.162:80)\n"
                    "• ip:port:user:pass (e.g., 1.2.3.4:8080:myuser:mypass)\n\n"
                    "Get free proxies from: proxyscrape.com"
                )
                return
            
            # Show confirmation for using proxy
            use_proxy = messagebox.askyesno(
                "Using Proxy",
                f"Proxy will be used:\n\n"
                f"Server: {proxy_config['server']}\n"
                f"Auth: {'Yes (' + proxy_config['username'] + ')' if proxy_config['username'] else 'No'}\n\n"
                f"Continue?"
            )
            
            if not use_proxy:
                return

        if self.is_running:
            messagebox.showwarning("Warning", "Login keeper is already running!")
            return

        # Update UI
        self.is_running = True
        self.login_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        
        status_text = f"Keeping {num_tabs} account(s) logged in... Checking every {delay}s"
        if proxy_config:
            status_text += f"\nUsing proxy: {proxy_config['server']}"
        
        self.status_label.config(text=status_text, fg="green")

        # Clear stop event
        self.stop_event.clear()

        # Clear previous threads
        self.login_threads.clear()

        # Start login in separate threads for each tab
        for i in range(num_tabs):
            thread = threading.Thread(
                target=run_login_worker,
                args=(email, password, delay, self.stop_event, i + 1, proxy_config),
                daemon=True
            )
            thread.start()
            self.login_threads.append(thread)

        info_msg = f"{num_tabs} browser tab(s) opened!\n\n"
        info_msg += "[OK] Maintaining login state\n"
        info_msg += f"[OK] Checking every {delay} second(s)\n"
        info_msg += "[OK] Auto re-login when logged out\n"
        
        if proxy_config:
            info_msg += f"\n[PROXY] {proxy_config['server']}\n"
        
        info_msg += "\nClick 'Stop' to stop the keeper."
        
        messagebox.showinfo("Started", info_msg)

    def stop_login(self):
        if not self.is_running:
            return

        # Signal stop
        self.stop_event.set()

        # Update UI
        self.is_running = False
        self.login_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status_label.config(text="Stopped", fg="red")

        messagebox.showinfo("Stopped", "Login keeper stopped.\nBrowser window remains open.")

    def on_closing(self):
        """Handle window close event"""
        if self.is_running:
            response = messagebox.askyesno(
                "Confirm Exit",
                "Login keeper is still running.\nDo you want to stop it and exit?"
            )
            if response:
                self.stop_event.set()
                self.destroy()
        else:
            self.destroy()


if __name__ == "__main__":
    app = LoginApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
