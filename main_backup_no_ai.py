import tkinter as tk
from tkinter import scrolledtext, END, Menu
from tkinter import simpledialog, messagebox
import subprocess
import pty
import os
import threading
import select
import re
import glob
import json # Added for config saving
import shutil # Added for finding ssh executable
from tkinter import ttk # Import ttk
from PIL import Image, ImageTk, ImageDraw, ImageFont # Add PIL for icon handling

CONFIG_FILE = "config.json" # Define config file name
HISTORY_DIR = ".history" # Directory for storing command histories
LOCAL_HISTORY_FILE = os.path.join(HISTORY_DIR, "local_history") # Local shell history
MAX_HISTORY_SIZE = 500 # Define max history size

class TerminalApp:
    def __init__(self, master):
        self.master = master
        master.title("AI Terminal")
        master.geometry("800x600")

        # Set window icon
        try:
            icon = Image.open("terminal_icon.png")
            self.icon = ImageTk.PhotoImage(icon)
            master.iconphoto(True, self.icon)
        except Exception as e:
            print(f"Warning: Could not set window icon: {e}")

        # Create history directory if it doesn't exist
        if not os.path.exists(HISTORY_DIR):
            os.makedirs(HISTORY_DIR)

        # Load config first
        self.config = self.load_config() # Load entire config dict
        self.background_color = self.config.get('background_color', 'black')
        self.font_family = self.config.get('font_family', 'monospace') # Load font family
        self.font_size = self.config.get('font_size', 10) # Load font size
        self.ssh_connections = self.config.get('ssh_connections', []) # Load or initialize ssh connections
        self.aliases = self.config.get('aliases', {}) # Load or initialize aliases

        # --- Command History ---
        self.command_history = [] # Local shell history
        self.ssh_histories = {} # Dictionary to store SSH session histories
        self.load_history() # Load local history from file
        self.history_index = -1 # -1 means current input, 0 is the last command, etc.
        # --- End Command History ---

        # Create font tuple
        self.terminal_font = (self.font_family, self.font_size)

        # --- Notebook Setup ---
        self.notebook = ttk.Notebook(master)
        self.notebook.pack(expand=True, fill=tk.BOTH, padx=5, pady=(5, 0))
        self.tabs = {} # Dictionary to store data for each tab {tab_id: data_dict}
        # --- End Notebook Setup ---

        # --- ANSI Pattern (Global) ---
        # Regex to find SGR codes (m), OSC title codes (]), bracketed paste codes (?2004h/l), and cursor movement
        self.ansi_escape_pattern = re.compile(r'''
            \x1b                    # ESC character
            (?:                     # Non-capturing group for CSI or OSC
                \[                  # Literal [ for CSI
                (?P<csi>            # Named group 'csi'
                    [^a-zA-Z]*      # Match parameters (digits, ;, etc.)
                    [a-zA-Z]        # Match the final letter indicating the command
                )                   # End 'csi' group
            |
                \]                  # Literal ] for OSC
                (?P<osc>            # Named group 'osc'
                    .*?             # Match anything non-greedily
                    (?:             # Non-capturing group for terminator
                        \x07        # BEL character (\a)
                    |
                        \x1b\\      # ESC followed by backslash (string terminator)
                    )               # End terminator group
                )                   # End 'osc' group
            )                       # End CSI/OSC group
        ''', re.VERBOSE)
        # --- End ANSI Pattern ---

        # --- ANSI Color and Style Handling Setup ---
        # This setup needs to be applied per-tab, but tags can be configured once
        self._define_ansi_colors()
        # self._configure_ansi_tags() # Configuration will happen in _create_new_tab

        # --- Global State (not per-tab) ---
        self.current_fg_tag = None # These might need rethinking if styles differ per tab output? For now, assume global context during processing? Or move inside _process_and_write_output? Let's move them.
        self.current_bg_tag = None
        self.is_bold = False
        # --- End ANSI Setup ---

        # self.input_frame = tk.Frame(master)
        # self.input_frame.pack(fill=tk.X, padx=5, pady=5)
        #
        # self.prompt_label = tk.Label(self.input_frame, text="$", font=self.terminal_font) # Use loaded/default font
        # self.prompt_label.pack(side=tk.LEFT)
        #
        # self.input_entry = tk.Entry(
        #     self.input_frame, bg='black', fg='white', insertbackground='white',
        #     font=self.terminal_font, # Use loaded/default font
        #     borderwidth=0
        # )
        # self.input_entry.pack(expand=True, fill=tk.X, side=tk.LEFT)
        # self.input_entry.bind("<Return>", self.run_command)
        # self.input_entry.bind("<Tab>", self.handle_tab_completion)
        # self.input_entry.bind("<Up>", self.recall_previous_command) # Add Up arrow binding
        # self.input_entry.bind("<Down>", self.recall_next_command) # Add Down arrow binding
        # self.input_entry.focus_set()
        # self.input_entry.icursor(END) # Move cursor to end

        # --- Menu Bar --- #
        self.menu_bar = Menu(master)
        master.config(menu=self.menu_bar)

        # --- File Menu ---
        file_menu = Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="New Tab", command=self._create_new_tab)
        file_menu.add_command(label="Close Tab", command=self._close_current_tab)
        file_menu.add_separator()
        # Add other file operations later if needed (e.g., Quit)
        # --- End File Menu ---

        # Options Menu
        options_menu = Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Options", menu=options_menu)
        options_menu.add_command(label="Set Background Color...", command=self.prompt_for_background_color)
        options_menu.add_command(label="Set Font...", command=self.prompt_for_font) # Add font option
        options_menu.add_separator() # Separator before aliases
        options_menu.add_command(label="Manage Aliases...", command=self.manage_aliases) # Add alias management option

        # --- SSH Connections Menu ---
        self.ssh_menu = Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="SSH Connections", menu=self.ssh_menu)
        self.populate_ssh_menu() # Populate the menu initially
        # --- End SSH Connections Menu ---

        # --- End Menu Bar --- #

        # Remove pty.openpty() - pty.fork() handles this
        # These will now be per-tab
        # self.master_fd = None # Initialize
        # self.child_pid = None
        # self.shell_process = None # This wasn't used, removing

        # --- Create the first tab ---
        self._create_new_tab()
        # --- End Create First Tab ---

        # self.start_shell() # Now happens per-tab
        # self.start_output_reader() # Now happens per-tab

        master.protocol("WM_DELETE_WINDOW", self.on_close)
        # --- Add Global Ctrl+C Binding ---
        self.master.bind_all("<Control-c>", self._send_interrupt_signal)
        # --- End Global Binding ---

    def _define_ansi_colors(self):
        """Define mappings from ANSI SGR codes to Tkinter color names."""
        self.ansi_fg_colors = {
            30: 'black', 31: 'red', 32: 'green', 33: 'yellow',
            34: 'blue', 35: 'magenta', 36: 'cyan', 37: 'lightgrey',
            90: 'darkgrey', 91: '#ff8080', 92: '#80ff80', 93: '#ffff80', # Brighter variations
            94: '#8080ff', 95: '#ff80ff', 96: '#80ffff', 97: 'white'
        }
        self.ansi_bg_colors = {
            40: 'black', 41: 'red', 42: 'green', 43: 'yellow',
            44: 'blue', 45: 'magenta', 46: 'cyan', 47: 'lightgrey',
            100: 'darkgrey', 101: '#ff8080', 102: '#80ff80', 103: '#ffff80',
            104: '#8080ff', 105: '#ff80ff', 106: '#80ffff', 107: 'white'
        }

    def _configure_ansi_tags(self, output_widget): # Takes output_widget as argument
        """Configure Tkinter tags for ANSI colors and styles on a specific widget."""
        for code, color in self.ansi_fg_colors.items():
            output_widget.tag_config(f"ansi_fg_{code}", foreground=color)
        for code, color in self.ansi_bg_colors.items():
            output_widget.tag_config(f"ansi_bg_{code}", background=color)
        # Configure bold tag (font details are set here and updated in set_font)
        output_widget.tag_config("ansi_bold", font=self.terminal_font + ('bold',))

    def _process_and_write_output(self, text, output_widget): # Takes output_widget as argument
        """Processes text containing ANSI codes and writes it to the specified output area."""
        # State variables moved here, potentially managed per call or per widget instance
        current_fg_tag = None
        current_bg_tag = None
        is_bold = False

        output_widget.configure(state='normal')

        # Clean up any carriage returns and normalize line endings
        text = text.replace('\r', '').replace('\r\n', '\n')

        # Check if this looks like a progress bar update
        # Progress bar updates typically have a pattern like: "filename... size speed time [progress] percent"
        is_progress_update = (
            'B/s' in text and  # Speed indicator
            '[' in text and ']' in text and  # Progress bar
            '%' in text and  # Percentage
            ('KiB' in text or 'MiB' in text or 'B' in text)  # Size indicator
        )

        if is_progress_update:
            # For progress updates, we want to update the last line
            # Get the last line's content
            last_line_start = output_widget.index("end-1c linestart")
            last_line_end = output_widget.index("end-1c lineend")
            last_line = output_widget.get(last_line_start, last_line_end)
            
            # If the last line looks like a progress update, replace it
            if 'B/s' in last_line and '[' in last_line and ']' in last_line:
                output_widget.delete(last_line_start, last_line_end)
                output_widget.mark_set("insert", last_line_start)
            else:
                # If the last line isn't a progress update, just append
                output_widget.insert(tk.END, '\n')
                output_widget.mark_set("insert", "end-1c")

            # Process the text as a single line, handling ANSI codes
            last_end = 0
            for match in self.ansi_escape_pattern.finditer(text):
                start, end = match.span()
                # Write text before the match with current style
                text_part = text[last_end:start]
                if text_part:
                    tags_to_apply = set()
                    if current_fg_tag: tags_to_apply.add(current_fg_tag)
                    if current_bg_tag: tags_to_apply.add(current_bg_tag)
                    if is_bold: tags_to_apply.add("ansi_bold")
                    output_widget.insert(tk.INSERT, text_part, tuple(tags_to_apply) or None)

                # Process the matched escape sequence
                csi = match.group('csi')
                osc = match.group('osc')

                if csi:
                    if csi.endswith('m'):
                        sgr_codes_str = csi[:-1]
                        try:
                            codes = [int(c) if c else 0 for c in sgr_codes_str.split(';')]
                        except ValueError:
                            codes = [0]
                        current_fg_tag, current_bg_tag, is_bold = self._update_style_from_sgr_codes(
                            codes, current_fg_tag, current_bg_tag, is_bold, output_widget
                        )

                last_end = end

            # Write any remaining text
            remaining_text = text[last_end:]
            if remaining_text:
                tags_to_apply = set()
                if current_fg_tag: tags_to_apply.add(current_fg_tag)
                if current_bg_tag: tags_to_apply.add(current_bg_tag)
                if is_bold: tags_to_apply.add("ansi_bold")
                output_widget.insert(tk.INSERT, remaining_text, tuple(tags_to_apply) or None)
        else:
            # For non-progress updates, process normally
            last_end = 0
            for match in self.ansi_escape_pattern.finditer(text):
                start, end = match.span()
                # Write text before the match with current style
                text_part = text[last_end:start]
                if text_part:
                    tags_to_apply = set()
                    if current_fg_tag: tags_to_apply.add(current_fg_tag)
                    if current_bg_tag: tags_to_apply.add(current_bg_tag)
                    if is_bold: tags_to_apply.add("ansi_bold")
                    output_widget.insert(tk.INSERT, text_part, tuple(tags_to_apply) or None)

                # Process the matched escape sequence
                csi = match.group('csi')
                osc = match.group('osc')

                if csi:
                    if csi.endswith('m'):
                        sgr_codes_str = csi[:-1]
                        try:
                            codes = [int(c) if c else 0 for c in sgr_codes_str.split(';')]
                        except ValueError:
                            codes = [0]
                        current_fg_tag, current_bg_tag, is_bold = self._update_style_from_sgr_codes(
                            codes, current_fg_tag, current_bg_tag, is_bold, output_widget
                        )

                last_end = end

            # Write any remaining text
            remaining_text = text[last_end:]
            if remaining_text:
                tags_to_apply = set()
                if current_fg_tag: tags_to_apply.add(current_fg_tag)
                if current_bg_tag: tags_to_apply.add(current_bg_tag)
                if is_bold: tags_to_apply.add("ansi_bold")
                output_widget.insert(tk.INSERT, remaining_text, tuple(tags_to_apply) or None)

        output_widget.see(tk.END)
        output_widget.configure(state='disabled')

    # Needs modification to handle state update correctly
    def _update_style_from_sgr_codes(self, codes, current_fg, current_bg, is_bold, output_widget): # Pass in current state and output widget
        """Updates the style state based on SGR codes and returns the new state."""
        # Work on copies or modify directly? Let's return new values.
        new_fg = current_fg
        new_bg = current_bg
        new_bold = is_bold

        code_idx = 0
        while code_idx < len(codes):
            code = codes[code_idx]
            if code == 0: # Reset
                new_fg = None
                new_bg = None
                new_bold = False
            elif code == 1: # Bold
                new_bold = True
            elif code == 22: # Normal intensity
                new_bold = False
            elif 30 <= code <= 37 or 90 <= code <= 97: # FG color
                tag = f"ansi_fg_{code}"
                if tag in output_widget.tag_names(): # Check tags on the specific widget
                    new_fg = tag
                else:
                    print(f"Warning: Unconfigured ANSI FG code {code}")
                    new_fg = None # Fallback
            elif code == 39: # Default FG color
                new_fg = None
            elif 40 <= code <= 47 or 100 <= code <= 107: # BG color
                tag = f"ansi_bg_{code}"
                if tag in output_widget.tag_names(): # Check tags on the specific widget
                    new_bg = tag
                else:
                    print(f"Warning: Unconfigured ANSI BG code {code}")
                    new_bg = None # Fallback
            elif code == 49: # Default BG color
                new_bg = None
            # --- Placeholder for more styles ---
            # ...
            # --- Skip extended colors ---
            elif code == 38 or code == 48:
                # ... (existing logic for skipping) ...
                if code_idx + 1 < len(codes):
                    color_type = codes[code_idx+1]
                    if color_type == 5 and code_idx + 2 < len(codes): code_idx += 2
                    elif color_type == 2 and code_idx + 4 < len(codes): code_idx += 4
                    else: code_idx += 1

            code_idx += 1
        return new_fg, new_bg, new_bold # Return the updated state

    def write_output_safe(self, text, output_widget): # Takes output_widget as argument
        """Safely schedules processing and writing output to the specified widget."""
        self.master.after(0, self._process_and_write_output, text, output_widget)

    def start_shell(self): # Will return pid, fd
        """Starts the default shell process in a pseudo-terminal. Returns (pid, fd)."""
        shell = os.environ.get('SHELL', '/bin/bash')
        pid, fd = -1, -1 # Initialize
        try:
            # pty.fork() creates the PTY and forks the process
            pid, fd = pty.fork()
        except OSError as e:
            # Need a way to display this error - maybe write to the specific tab's output?
            # For now, print and return error indicators
            print(f"Error forking pty: {e}")
            return None, None # Indicate failure

        if pid == 0:  # Child process
            # We are now in the child process.
            # The child inherits the pseudo-terminal's slave end as its stdin, stdout, stderr.
            # We don't need to, and shouldn't, close the master_fd here.

            # --- Change to home directory ---
            try:
                home_dir = os.path.expanduser('~')
                os.chdir(home_dir)
            except Exception as e:
                print(f"Child: Error changing to home directory '{home_dir}': {e}", file=os.sys.stderr)
                # Optionally exit if changing directory is critical
                os._exit(1)
            # --- End change directory ---

            # Execute the shell
            try:
                # --- Set TERM for color support ---
                child_env = os.environ.copy()
                child_env['TERM'] = 'xterm-256color'
                # ----------------------------------

                # Use execvpe for better path searching and environment handling
                # Pass the modified environment
                os.execvpe(shell, [shell], child_env)
            except FileNotFoundError:
                # Use print for child process errors as stdout/stderr are redirected
                print(f"Child: Shell not found: {shell}", file=os.sys.stderr)
                os._exit(1) # Use _exit in child after fork
            except Exception as e:
                print(f"Child: Error executing shell: {e}", file=os.sys.stderr)
                os._exit(1) # Use _exit in child after fork
        else:  # Parent process
             # We are in the parent process.
             # Set the master fd to non-blocking for use with select
             os.set_blocking(fd, False)
             return pid, fd # Return pid and fd to the caller

    def start_output_reader(self, master_fd, child_pid, output_widget): # Takes arguments for the specific tab
        """Starts a thread to continuously read output for a specific tab."""
        # We need unique thread names or another way to manage them if needed later
        reader_thread = threading.Thread(
            target=self.read_output,
            args=(master_fd, child_pid, output_widget), # Pass tab-specific info
            daemon=True
        )
        reader_thread.start()
        return reader_thread # Return the thread object

    def read_output(self, master_fd, child_pid, output_widget): # Takes arguments for the specific tab
        """Reads output from the specific master pty and displays it in the specific widget."""
        current_pid = child_pid # Local copy in case the tab is closed while reading
        while True:
            if current_pid is None: # Check if the process associated with this reader is gone
                break
            try:
                # Use select for non-blocking read with timeout
                r, _, _ = select.select([master_fd], [], [], 0.1)
                if master_fd in r:
                    try:
                        output = os.read(master_fd, 1024)
                        if not output:  # EOF, process likely exited
                            self.write_output_safe("Shell process exited.", output_widget)
                            # Attempt to clean up process state - might need main thread coordination
                            # We can't reliably modify self.tabs from here. Mark as exited?
                            # For now, just stop reading for this tab.
                            # How to signal the main thread? Maybe set a flag in the tab's data dict.
                            tab_data = self._get_tab_data_by_widget(output_widget) # Helper needed
                            if tab_data:
                                tab_data['child_pid'] = None # Mark as gone
                                tab_data['master_fd'] = None
                            break # Exit the reading loop

                        decoded_output = output.decode('utf-8', errors='replace')
                        self.write_output_safe(decoded_output, output_widget) # Write to correct widget
                    except OSError: # E.g., EIO when process exits
                        self.write_output_safe("Error reading from shell.", output_widget)
                        tab_data = self._get_tab_data_by_widget(output_widget)
                        if tab_data:
                            tab_data['child_pid'] = None # Mark as gone
                            tab_data['master_fd'] = None
                        break # Exit the reading loop
            except ValueError: # master_fd might become invalid if closed by main thread
                 print(f"Read thread for PID {current_pid}: file descriptor closed.")
                 break # Exit loop if fd is bad
            except Exception as e:
                # Catch potential select errors or other issues
                self.write_output_safe(f"Reader thread error: {e}", output_widget)
                tab_data = self._get_tab_data_by_widget(output_widget)
                if tab_data:
                    tab_data['child_pid'] = None # Mark as potentially gone
                    tab_data['master_fd'] = None
                break # Exit the loop on significant errors

    # --- Helper to find tab data by one of its widgets (needed for read_output) ---
    def _get_tab_data_by_widget(self, widget):
        for tab_id, data in self.tabs.items():
            if data['output_area'] == widget or data['input_entry'] == widget:
                return data
        return None
    # --- End Helper ---

    def run_command(self, event=None):
        """Sends the command from the current tab's input entry to its shell process."""
        tab_id = self.notebook.select()
        if not tab_id: return 'break' # No tab selected

        tab_data = self.tabs.get(tab_id)
        if not tab_data: return 'break' # Should not happen

        input_entry = tab_data['input_entry']
        master_fd = tab_data['master_fd']
        output_widget = tab_data['output_area'] # For writing errors

        command = input_entry.get()
        input_entry.delete(0, END)

        # Check for aliases before processing the command
        if command in self.aliases:
            command = self.aliases[command]

        # Check if this is an SSH session exit
        if tab_data.get('ssh_connection') and command.lower() in ('exit', 'logout', 'quit'):
            # Clear SSH connection info to revert to local history
            tab_data.pop('ssh_connection', None)
            self.write_output_safe("SSH session closed. Reverting to local shell.\n", output_widget)
            return 'break'

        # Check if the last command was sudo
        last_command_was_sudo = False
        current_history = self.command_history
        if tab_data.get('ssh_connection'):
            # This is an SSH session, use its specific history
            name = tab_data['ssh_connection'].get('name', 'Unnamed')
            if name not in self.ssh_histories:
                self.ssh_histories[name] = []
            current_history = self.ssh_histories[name]
            last_command_was_sudo = current_history and current_history[-1].strip().startswith('sudo ')
        else:
            # Local shell, use main history
            last_command_was_sudo = current_history and current_history[-1].strip().startswith('sudo ')

        # Check if this is a password prompt response
        is_password_prompt = False
        try:
            # Get the last few lines of output to check for password prompts
            output_text = output_widget.get("end-3l", "end-1c")  # Get last 3 lines
            is_password_prompt = any(
                prompt in output_text.lower() for prompt in [
                    "password:", 
                    "enter password", 
                    "password for",
                    "passphrase for key",
                    "verification code:",
                    "otp:",
                    "authentication code:",
                    "token:",
                    "secret:",
                    "key:",
                    "pin:",
                    "passcode:",
                    "verification:",
                    "confirm:"
                ]
            )
        except tk.TclError:
            pass  # Not enough text in the widget yet

        # Add command to history if it's not empty, not the same as the last one,
        # and not a sensitive input (like sudo password or package manager prompt)
        if command.strip(): # Don't save empty commands
            # Check if this looks like a sensitive input
            is_sensitive = (
                last_command_was_sudo or  # password input after sudo
                is_password_prompt or     # response to a password prompt
                command.startswith('Password:') or  # sudo password prompt
                command.startswith('password:') or  # other password prompts
                command.startswith('(y/N)') or      # package manager prompts
                command.startswith('[Y/n]') or      # package manager prompts
                command.startswith('(Y/n)') or      # package manager prompts
                command.startswith('(yes/no)') or   # package manager prompts
                command.startswith('(Yes/No)') or   # package manager prompts
                command.startswith('(y/n)') or      # package manager prompts
                command.startswith('(Y/N)') or      # package manager prompts
                # Additional password detection patterns
                command.startswith('Enter passphrase') or  # SSH key passphrase
                command.startswith('Enter password') or    # General password prompt
                command.startswith('Password for') or      # Another common password prompt
                command.startswith('Verification code:') or # 2FA codes
                command.startswith('OTP:') or              # One-time password
                command.startswith('Authentication code:') or # Auth codes
                command.startswith('Token:') or            # API tokens
                command.startswith('Secret:') or           # General secrets
                command.startswith('Key:') or              # API keys
                command.startswith('PIN:') or              # PIN codes
                command.startswith('Passcode:') or         # Passcodes
                command.startswith('Verification:') or     # Verification codes
                command.startswith('Confirm:') or          # Confirmation prompts
                command.startswith('Type yes to continue') # Dangerous operation confirmations
            )
            
            if not is_sensitive and (not current_history or current_history[-1] != command):
                current_history.append(command)
                self.save_history() # Save after each command to ensure persistence
        self.history_index = -1 # Reset history navigation index

        if master_fd is not None and tab_data.get('child_pid') is not None:
            # Ensure a single newline character is added
            full_command = command + "\n"
            try:
                os.write(master_fd, full_command.encode())
            except OSError as e:
                self.write_output_safe(f"Error writing to shell: {e}\n", output_widget)
                # Consider the shell dead if write fails
                tab_data['child_pid'] = None
                tab_data['master_fd'] = None # Or close it?
        else:
            self.write_output_safe("Shell process is not running for this tab.\n", output_widget)

        return 'break' # Prevents Tkinter from inserting a literal Tab character

    def handle_tab_completion(self, event=None):
        """Handles Tab key press for basic path completion in the current tab."""
        tab_id = self.notebook.select()
        if not tab_id: return 'break'
        tab_data = self.tabs.get(tab_id)
        if not tab_data: return 'break'

        input_entry = tab_data['input_entry']
        output_widget = tab_data['output_area']

        cursor_pos = input_entry.index(tk.INSERT)
        line = input_entry.get()
        
        # Find the start of the word before the cursor
        start_index = line.rfind(' ', 0, cursor_pos) + 1
        current_word = line[start_index:cursor_pos]

        if not current_word:
            return 'break' # Nothing to complete

        # Expand ~ to user's home directory
        if current_word.startswith('~'):
            current_word = os.path.expanduser(current_word)
            # Need to adjust start_index if expansion happened before cursor
            # Simple approach: just use the expanded word for globbing
            # More complex logic needed if we want to replace only the part after ~
            
        # Use glob to find matches
        matches = glob.glob(current_word + '*')

        if not matches:
            return 'break' # No matches

        if len(matches) == 1:
            # Single match: complete it
            match = matches[0]
            # If it's a directory and doesn't end with /, add it
            if os.path.isdir(match) and not match.endswith(os.path.sep):
                match += os.path.sep
            # Replace the current word with the match
            input_entry.delete(start_index, cursor_pos)
            input_entry.insert(start_index, match)
        else:
            # Multiple matches: find common prefix
            common_prefix = os.path.commonprefix(matches)
            if common_prefix and common_prefix != line[start_index:cursor_pos]:
                # If there's a common prefix longer than current word, complete it
                input_entry.delete(start_index, cursor_pos)
                input_entry.insert(start_index, common_prefix)
            
            # Print all matches to the output area for user reference
            # Get terminal width (approximate) for formatting
            try:
                term_cols = os.get_terminal_size().columns
            except OSError: # Fallback if not connected to a real terminal (e.g., IDE)
                term_cols = 80
            
            output_str = "\n" + '  '.join(os.path.basename(m) if os.path.isdir(m) else os.path.basename(m) for m in matches) # Show basenames
            # Simple wrap attempt (doesn't handle long names well)
            # wrapped_output = '\n'.join(output_str[i:i+term_cols] for i in range(0, len(output_str), term_cols))
            self.write_output_safe(output_str + "\n", output_widget) # Write to correct widget
            # Reprint prompt and current input line after showing matches
            # Avoid writing the prompt here if we just showed completions,
            # as the shell itself will likely reprint the prompt soon.
            # self.write_output_safe(f"${line}")

        return 'break' # Prevents Tkinter from inserting a literal Tab character

    def recall_previous_command(self, event=None):
        """Recalls the previous command from history into the current tab's input."""
        tab_id = self.notebook.select()
        if not tab_id: return 'break'
        tab_data = self.tabs.get(tab_id)
        if not tab_data: return 'break'
        input_entry = tab_data['input_entry']

        # Determine which history to use based on whether this is an SSH session
        current_history = self.command_history
        if tab_data.get('ssh_connection'):
            name = tab_data['ssh_connection'].get('name', 'Unnamed')
            current_history = self.ssh_histories.get(name, [])

        if not current_history:
            return 'break' # No history

        if self.history_index < len(current_history) - 1:
            self.history_index += 1
            input_entry.delete(0, END)
            # History is stored chronologically, so index 0 is oldest, -1 is newest
            # To go "up", we increase index from -1 towards len()-1
            # We access history from the end: -(index + 1)
            input_entry.insert(0, current_history[-(self.history_index + 1)])
            input_entry.icursor(END) # Move cursor to end

        return 'break' # Prevent default Up arrow behavior

    def recall_next_command(self, event=None):
        """Recalls the next command from history (or clears input) in the current tab."""
        tab_id = self.notebook.select()
        if not tab_id: return 'break'
        tab_data = self.tabs.get(tab_id)
        if not tab_data: return 'break'
        input_entry = tab_data['input_entry']

        # Determine which history to use based on whether this is an SSH session
        current_history = self.command_history
        if tab_data.get('ssh_connection'):
            name = tab_data['ssh_connection'].get('name', 'Unnamed')
            current_history = self.ssh_histories.get(name, [])

        if not current_history:
            return 'break' # No history

        if self.history_index >= 0:
            self.history_index -= 1
            input_entry.delete(0, END)
            if self.history_index == -1:
                # Reached the "current" command line, leave it empty
                pass
            else:
                # Access history from the end: -(index + 1)
                input_entry.insert(0, current_history[-(self.history_index + 1)])
                input_entry.icursor(END) # Move cursor to end
        else:
            # Already at the current input line, do nothing further back
            pass

        return 'break' # Prevent default Down arrow behavior

    def _send_interrupt_signal(self, event=None):
        """Sends SIGINT (Ctrl+C) to the process group of the currently active tab."""
        try:
            tab_id = self.notebook.select()
            if not tab_id: return 'break'
        except tk.TclError:
            return 'break' # No tab selected

        tab_data = self.tabs.get(tab_id)
        if not tab_data: return 'break'

        child_pid = tab_data.get('child_pid')
        output_widget = tab_data.get('output_area')

        if child_pid:
            try:
                # --- Send interrupt character to PTY --- #
                if tab_data.get('master_fd') is not None:
                    os.write(tab_data['master_fd'], b'\x03') # Write ETX (Ctrl+C)
                    # self.write_output_safe("^C\n", output_widget) # REMOVED - Shell will likely echo it
                else:
                    self.write_output_safe("Master FD not available.\n", output_widget)
                # pgid = os.getpgid(child_pid)
                # os.killpg(pgid, 2) # 2 is SIGINT -- REMOVED
                # self.write_output_safe("^C\n", output_widget) # Optionally echo ^C to the terminal -- MOVED
            except ProcessLookupError:
                # Process likely already finished
                self.write_output_safe("Process not found.\n", output_widget)
            except OSError as e:
                # Other OS errors (writing to fd, etc.)
                self.write_output_safe(f"Error sending interrupt: {e}\n", output_widget)
            except Exception as e:
                # Catch unexpected errors
                self.write_output_safe(f"Unexpected error sending interrupt: {e}\n", output_widget)
        else:
            # No active process for this tab
            self.write_output_safe("No process running in this tab.\n", output_widget)

        return 'break' # Prevent default Tkinter handling

    # --- History Loading/Saving ---
    def load_history(self):
        """Loads command history from the history file."""
        try:
            # Load local history
            if os.path.exists(LOCAL_HISTORY_FILE):
                with open(LOCAL_HISTORY_FILE, 'r') as f:
                    # Read lines, strip whitespace, filter out empty lines
                    self.command_history = [line.strip() for line in f if line.strip()]
                    # Keep only the most recent MAX_HISTORY_SIZE entries if file is larger
                    if len(self.command_history) > MAX_HISTORY_SIZE:
                        self.command_history = self.command_history[-MAX_HISTORY_SIZE:]

            # Load SSH session histories
            for conn in self.ssh_connections:
                name = conn.get('name', 'Unnamed')
                history_file = os.path.join(HISTORY_DIR, f"ssh_{name}.history")
                if os.path.exists(history_file):
                    with open(history_file, 'r') as f:
                        self.ssh_histories[name] = [line.strip() for line in f if line.strip()]
                        if len(self.ssh_histories[name]) > MAX_HISTORY_SIZE:
                            self.ssh_histories[name] = self.ssh_histories[name][-MAX_HISTORY_SIZE:]
        except IOError as e:
            print(f"Warning: Could not load command history: {e}")
        except Exception as e:
            print(f"Unexpected error loading history: {e}")

    def save_history(self):
        """Saves the command history to the history file, truncating if needed."""
        try:
            # Save local history
            with open(LOCAL_HISTORY_FILE, 'w') as f:
                # Determine the slice to save (last MAX_HISTORY_SIZE entries)
                start_index = max(0, len(self.command_history) - MAX_HISTORY_SIZE)
                history_to_save = self.command_history[start_index:]
                for command in history_to_save:
                    f.write(command + "\n")

            # Save SSH session histories
            for name, history in self.ssh_histories.items():
                history_file = os.path.join(HISTORY_DIR, f"ssh_{name}.history")
                with open(history_file, 'w') as f:
                    start_index = max(0, len(history) - MAX_HISTORY_SIZE)
                    history_to_save = history[start_index:]
                    for command in history_to_save:
                        f.write(command + "\n")
        except IOError as e:
            print(f"Warning: Could not save command history: {e}")
        except Exception as e:
            print(f"Unexpected error saving history: {e}")
    # --- End History Loading/Saving ---

    # --- Configuration Loading/Saving ---
    def load_config(self):
        """Loads configuration from the JSON file."""
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                # Basic validation
                if not isinstance(config, dict):
                    print(f"Warning: Config file '{CONFIG_FILE}' is not a valid JSON object. Using defaults.")
                    return {} # Return empty dict for defaults
                return config
        except FileNotFoundError:
            return {} # Config file doesn't exist yet, return empty dict for defaults
        except json.JSONDecodeError:
            print(f"Error reading config file '{CONFIG_FILE}'. Using defaults.")
            return {}
        # return 'black' # Default background color <-- Removed, handled by getter

    def save_config(self):
        """Saves the current configuration to the JSON file."""
        # Ensure the config dictionary exists
        if not hasattr(self, 'config'):
             self.config = {} # Initialize if it somehow doesn't exist

        # Update config dictionary with current values
        self.config['background_color'] = self.background_color
        self.config['font_family'] = self.font_family # Save font family
        self.config['font_size'] = self.font_size # Save font size
        self.config['ssh_connections'] = self.ssh_connections
        self.config['aliases'] = self.aliases # Save aliases
        # Add other settings here in the future

        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f, indent=4)
        except IOError as e:
            print(f"Error writing config file '{CONFIG_FILE}': {e}")
        except Exception as e:
            print(f"Unexpected error saving config: {e}")
    # --- End Configuration ---

    # --- SSH Connection Management ---
    def populate_ssh_menu(self):
        """Clears and repopulates the SSH connections menu."""
        # Clear existing entries (except the 'Add New' option)
        self.ssh_menu.delete(0, tk.END) # Clear all previous entries

        self.ssh_menu.add_command(label="Add New Connection...", command=self.prompt_add_ssh_connection)
        self.ssh_menu.add_separator()

        if not self.ssh_connections:
            self.ssh_menu.add_command(label="(No saved connections)", state=tk.DISABLED)
        else:
            # Sort connections by name for easier navigation
            sorted_connections = sorted(self.ssh_connections, key=lambda x: x.get('name', ''))
            for conn in sorted_connections:
                name = conn.get('name', 'Unnamed')
                # Use a lambda to capture the current connection details
                self.ssh_menu.add_command(
                    label=name,
                    command=lambda c=conn: self.prepare_ssh_command(c)
                )
        self.ssh_menu.add_separator() # Separator before delete option
        self.ssh_menu.add_command(label="Delete Connection...", command=self.prompt_delete_ssh_connection)

    def prompt_add_ssh_connection(self):
        """Uses dialogs to get SSH connection details from the user."""
        try:
            name = simpledialog.askstring("Add SSH Connection", "Enter a name for this connection:", parent=self.master)
            if not name: return # User cancelled

            host = simpledialog.askstring("Add SSH Connection", f"Enter hostname or IP for '{name}':", parent=self.master)
            if not host: return # User cancelled

            user = simpledialog.askstring("Add SSH Connection", f"Enter username for {host} (optional):", parent=self.master)
            # User can be empty

            port_str = simpledialog.askstring("Add SSH Connection", f"Enter port for {host} (optional, default 22):", parent=self.master)
            port = 22
            if port_str:
                try:
                    port = int(port_str)
                    if not (0 < port < 65536):
                        raise ValueError("Port out of range")
                except ValueError:
                    messagebox.showerror("Invalid Port", f"Invalid port number: '{port_str}'.\\nUsing default port 22.")
                    # print(f"Invalid port '{port_str}', using default 22.") # Keep console log? Optional.
                    port = 22 # Reset to default

            key_path = simpledialog.askstring("Add SSH Connection", f"Enter path to identity file (optional):", parent=self.master)
            # Key path can be empty

        except tk.TclError as e:
             messagebox.showerror("Dialog Error", f"An error occurred during input: {e}", parent=self.master)
             return # Abort adding connection

        # If we reach here, input was successful (or cancelled)
        if not name or not host: # Check again in case TclError happened after name/host but before others
             return

        new_connection = {
            "name": name,
            "host": host,
            "user": user if user else None, # Store None if empty
            "port": port,
            "key": key_path if key_path else None, # Store None if empty
        }

        # Check for duplicate name (optional but good practice)
        if any(c.get('name') == name for c in self.ssh_connections):
            # Handle duplicate name (e.g., prompt to overwrite or choose a different name)
            # For now, just overwrite silently or add anyway (simplest)
            print(f"Warning: A connection named '{name}' already exists. Overwriting/adding anyway.")
            # Remove existing if overwriting:
            # self.ssh_connections = [c for c in self.ssh_connections if c.get('name') != name]

        self.ssh_connections.append(new_connection)
        self.save_config()
        self.populate_ssh_menu() # Update the menu

    def prompt_delete_ssh_connection(self):
        """Prompts the user to enter the name of an SSH connection to delete."""
        if not self.ssh_connections:
            messagebox.showinfo("Delete SSH Connection", "There are no saved connections to delete.", parent=self.master)
            return

        # Create a list of names for the user to see (optional but helpful)
        connection_names = [conn.get('name', 'Unnamed') for conn in self.ssh_connections]
        prompt_text = "Enter the exact name of the connection to delete:\n\n" + '\n'.join(connection_names)

        name_to_delete = simpledialog.askstring("Delete SSH Connection", prompt_text, parent=self.master)

        if not name_to_delete: return # User cancelled

        # Find the connection by name
        initial_length = len(self.ssh_connections)
        # Case-sensitive comparison for deletion
        self.ssh_connections = [conn for conn in self.ssh_connections if conn.get('name') != name_to_delete]

        if len(self.ssh_connections) < initial_length:
            # Connection was found and removed
            self.save_config()
            self.populate_ssh_menu() # Update the menu
            messagebox.showinfo("Delete SSH Connection", f"Connection '{name_to_delete}' deleted successfully.", parent=self.master)
        else:
            # Connection not found
            messagebox.showwarning("Delete SSH Connection", f"Connection named '{name_to_delete}' not found.", parent=self.master)

    def prepare_ssh_command(self, connection_details):
        """Constructs the SSH command and sends it to the running shell."""
        ssh_executable = shutil.which("ssh") or "ssh" # Find ssh or default to 'ssh'

        cmd_parts = [ssh_executable]

        if connection_details.get('key'):
            cmd_parts.extend(["-i", connection_details['key']]) # Add identity file path

        if connection_details.get('port') and connection_details['port'] != 22:
             cmd_parts.extend(["-p", str(connection_details['port'])]) # Add port if non-default

        user = connection_details.get('user')
        host = connection_details.get('host')

        if user:
            cmd_parts.append(f"{user}@{host}")
        else:
            cmd_parts.append(host)

        command_str = " ".join(cmd_parts) # Simple space joining; quotes might be needed for paths with spaces

        # --- Execute the command directly in the current tab ---
        tab_id = self.notebook.select()
        if not tab_id:
             messagebox.showwarning("SSH Error", "No active terminal tab selected.", parent=self.master)
             return
        tab_data = self.tabs.get(tab_id)
        if not tab_data: return # Should not happen

        # Store SSH connection info in tab data
        tab_data['ssh_connection'] = connection_details

        output_widget = tab_data['output_area']
        master_fd = tab_data['master_fd']

        self.write_output_safe(f"\nAttempting SSH connection: {command_str}\n", output_widget) # Echo command to user

        if master_fd is not None and tab_data.get('child_pid') is not None:
            # Ensure a single newline character is added
            full_command = command_str + "\n"
            try:
                os.write(master_fd, full_command.encode())
            except OSError as e:
                self.write_output_safe(f"Error writing to shell: {e}\n", output_widget)
                # Consider the shell dead if write fails
                tab_data['child_pid'] = None
                tab_data['master_fd'] = None
        else:
            self.write_output_safe("Shell process is not running. Cannot initiate SSH connection.\n", output_widget)
        # --- End execution ---

    def prompt_for_background_color(self):
        """Prompts the user to enter a background color."""
        color = simpledialog.askstring("Background Color",
                                       "Enter color name or hex value (e.g., black, #333333):",
                                       parent=self.master)
        if color: # User didn't cancel
            try:
                # Test the color first by attempting to configure
                # A dummy widget or directly on the output area is fine.
                # This raises TclError if the color is invalid.
                # Apply to a sample widget first, maybe the first tab's output?
                # Or maybe just try setting the variable and apply in set_background_color
                # For now, let's assume the color string is valid and proceed
                # self.output_area.configure(bg=color) # Test apply - Cannot test on non-existent widget
                self.set_background_color(color) # Call the method that also saves
            except tk.TclError: # This might not catch it here anymore
                # Error handled by Tkinter/Tcl, but provide user feedback
                print(f"Invalid color specified: {color}") # Log to console
                messagebox.showerror("Invalid Color", f"Could not set background to '{color}'. Please use a valid Tk color name or hex code.", parent=self.master)
                # Revert - not needed as it wasn't applied yet

    def set_background_color(self, color):
        """Sets the background color of all terminal output areas."""
        try:
            # Validate the color by applying it temporarily to the root window
            # This isn't perfect but better than nothing
            original_bg = self.master.cget("bg") # Get original background
            self.master.config(bg=color) # Try applying
            self.master.config(bg=original_bg) # Revert immediately

            self.background_color = color
            # Apply to all existing tabs
            for tab_id in self.notebook.tabs():
                tab_data = self.tabs.get(tab_id)
                if tab_data and tab_data.get('output_area'):
                    tab_data['output_area'].configure(bg=self.background_color)
            self.save_config() # Save after successful change
        except tk.TclError:
            print(f"Invalid color specified: {color}")
            messagebox.showerror("Invalid Color", f"Could not set background to '{color}'. Please use a valid Tk color name or hex code.", parent=self.master)

    # --- Font Management --- #
    def prompt_for_font(self):
        """Prompts the user to enter font family and size."""
        # Prompt for font family
        family = simpledialog.askstring("Font Family",
                                        "Enter font family (e.g., monospace, Courier, Consolas):",
                                        initialvalue=self.font_family,
                                        parent=self.master)
        if not family: return # User cancelled

        # Prompt for font size
        size = simpledialog.askinteger("Font Size",
                                       "Enter font size (e.g., 10, 12):",
                                       initialvalue=self.font_size,
                                       minvalue=6, maxvalue=72, # Reasonable limits
                                       parent=self.master)
        if size is None: return # User cancelled

        # Try setting the font
        self.set_font(family, size)

    def set_font(self, family, size):
        """Sets the font for terminal widgets and saves config."""
        new_font = (family, size)
        new_font_bold = new_font + ('bold',) # Create bold font tuple
        try:
            # Test the font on a temporary label before applying everywhere
            test_label = tk.Label(self.master, font=new_font)
            test_label.destroy() # Clean up immediately

            # If the above didn't raise an error, the font is likely valid
            self.font_family = family
            self.font_size = size
            self.terminal_font = new_font

            # Apply to all existing tabs and configure bold tag globally (if needed)
            for tab_id in self.notebook.tabs():
                 tab_data = self.tabs.get(tab_id)
                 if tab_data:
                     if tab_data.get('output_area'):
                         tab_data['output_area'].configure(font=self.terminal_font)
                         # Reconfigure bold tag for this specific output area
                         tab_data['output_area'].tag_configure("ansi_bold", font=new_font_bold)
                     if tab_data.get('prompt_label'):
                         tab_data['prompt_label'].configure(font=self.terminal_font)
                     if tab_data.get('input_entry'):
                         tab_data['input_entry'].configure(font=self.terminal_font)

            self.save_config() # Save the new font settings

        except tk.TclError as e:
            messagebox.showerror("Invalid Font",
                               f"Could not apply font: '{family}' size {size}. Error: {e}",
                               parent=self.master)
            # No need to revert as changes weren't fully applied

    # --- End Font Management --- #

    def on_close(self):
        """Handles window closing event, cleaning up all tabs."""
        # Iterate through all tabs and clean them up
        # Make a copy of keys because we might modify the dict during iteration if closing fails early
        tab_ids = list(self.tabs.keys())
        for tab_id in tab_ids:
            tab_data = self.tabs.get(tab_id)
            if not tab_data: continue

            child_pid = tab_data.get('child_pid')
            master_fd = tab_data.get('master_fd')

            if child_pid:
                try:
                    # Try to terminate the child process group gracefully
                    os.killpg(os.getpgid(child_pid), 15) # SIGTERM
                    # Give it a moment, then force kill if needed (optional)
                    try:
                        os.waitpid(child_pid, os.WNOHANG) # Check if already exited
                    except ChildProcessError:
                        pass # Already gone
                except ProcessLookupError:
                    pass # Process already gone
                except Exception as e:
                    print(f"Error during tab cleanup (PID {child_pid}): {e}")

            if master_fd is not None:
                try:
                    os.close(master_fd)
                except OSError:
                     pass # Already closed or invalid

        # Now that processes are killed / FDs closed, clear the tab data
        self.tabs.clear()

        self.save_history() # Save history on close
        self.master.destroy()

    # Add the new method placeholder
    def _create_new_tab(self):
        """Creates a new terminal tab with its own shell process."""
        tab_frame = tk.Frame(self.notebook) # Master is the notebook

        # --- Output Area for this tab ---
        output_area = scrolledtext.ScrolledText(
            tab_frame, wrap=tk.WORD, state='disabled',
            bg=self.background_color, fg='lightgrey',
            font=self.terminal_font
        )
        output_area.pack(expand=True, fill=tk.BOTH, padx=0, pady=0) # No padding within tab frame
        self._configure_ansi_tags(output_area) # Configure tags for this specific widget

        # --- Input Frame for this tab ---
        input_frame = tk.Frame(tab_frame)
        input_frame.pack(fill=tk.X, padx=0, pady=0)

        prompt_label = tk.Label(input_frame, text="$", font=self.terminal_font)
        prompt_label.pack(side=tk.LEFT)

        input_entry = tk.Entry(
            input_frame, bg='black', fg='white', insertbackground='white',
            font=self.terminal_font,
            borderwidth=0
        )
        input_entry.pack(expand=True, fill=tk.X, side=tk.LEFT)

        # Bind events to the instance methods (they will figure out the current tab)
        input_entry.bind("<Return>", self.run_command)
        input_entry.bind("<Tab>", self.handle_tab_completion)
        input_entry.bind("<Up>", self.recall_previous_command)
        input_entry.bind("<Down>", self.recall_next_command)

        # --- Start Shell Process for this tab ---
        child_pid, master_fd = self.start_shell()

        reader_thread = None
        if child_pid is not None and master_fd is not None:
            # Start reader thread only if shell started successfully
            reader_thread = self.start_output_reader(master_fd, child_pid, output_area)
        else:
            output_area.configure(state='normal')
            output_area.insert(tk.END, "Failed to start shell for this tab.\n")
            output_area.configure(state='disabled')
            # Consider disabling input if shell fails?
            input_entry.configure(state='disabled')


        # --- Add Tab to Notebook and Store Data ---
        tab_count = len(self.tabs) + 1
        # Add the frame to the notebook
        self.notebook.add(tab_frame, text=f'Terminal {tab_count}')
        # The tab_id IS the widget path of tab_frame
        tab_id = str(tab_frame) # Use the widget path as the key

        # Store tab-specific data using the correct ID
        self.tabs[tab_id] = {
            'frame': tab_frame,
            'output_area': output_area,
            'input_frame': input_frame,
            'prompt_label': prompt_label,
            'input_entry': input_entry,
            'child_pid': child_pid,
            'master_fd': master_fd,
            'reader_thread': reader_thread,
            # Add other per-tab state if needed later (e.g., current directory)
        }

        # --- Select and Focus ---
        self.notebook.select(tab_id) # Select the new tab using its actual ID
        input_entry.focus_set()

    def _close_current_tab(self):
        """Closes the currently selected terminal tab."""
        try:
            selected_tab_id = self.notebook.select()
        except tk.TclError: # No tab selected (e.g., if all are closed)
            return

        if not selected_tab_id:
            return # Should not happen if select() didn't raise error, but check anyway

        tab_data = self.tabs.pop(selected_tab_id, None) # Remove from dict and get data

        if not tab_data:
            print(f"Warning: Data for tab {selected_tab_id} not found during close.")
            # Attempt to remove the tab from notebook anyway if it exists
            try:
                self.notebook.forget(selected_tab_id)
            except tk.TclError:
                 pass # Tab might already be gone
            return

        # --- Cleanup Process and FD --- #
        child_pid = tab_data.get('child_pid')
        master_fd = tab_data.get('master_fd')

        if child_pid:
            print(f"Closing tab, terminating PID: {child_pid}")
            try:
                os.killpg(os.getpgid(child_pid), 15) # SIGTERM group
                os.waitpid(child_pid, os.WNOHANG) # Try to reap immediately
            except ProcessLookupError:
                pass # Already gone
            except OSError as e:
                print(f"Error terminating process group {child_pid}: {e}")
            except Exception as e:
                print(f"Unexpected error during process termination: {e}")

        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError as e:
                print(f"Error closing master_fd {master_fd}: {e}")

        # --- Remove from Notebook --- #
        try:
            self.notebook.forget(selected_tab_id)
        except tk.TclError as e:
            print(f"Warning: Could not remove tab {selected_tab_id} from notebook: {e}")

        print(f"Tab {selected_tab_id} closed.")

        # Optional: Close window if last tab is closed?
        # if not self.tabs:
        #     self.on_close()

    def manage_aliases(self):
        """Opens a window to manage terminal aliases."""
        # Create a new top-level window
        alias_window = tk.Toplevel(self.master)
        alias_window.title("Manage Aliases")
        alias_window.geometry("600x400")
        
        # Create a frame for the list of aliases
        list_frame = tk.Frame(alias_window)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create a scrollable listbox
        scrollbar = tk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        alias_list = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
        alias_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=alias_list.yview)
        
        # Populate the listbox with current aliases
        for alias, command in self.aliases.items():
            alias_list.insert(tk.END, f"{alias} = {command}")
        
        # Create buttons frame
        button_frame = tk.Frame(alias_window)
        button_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Add buttons
        add_button = tk.Button(button_frame, text="Add Alias", command=lambda: self._add_alias(alias_list))
        add_button.pack(side=tk.LEFT, padx=5)
        
        edit_button = tk.Button(button_frame, text="Edit Alias", command=lambda: self._edit_alias(alias_list))
        edit_button.pack(side=tk.LEFT, padx=5)
        
        delete_button = tk.Button(button_frame, text="Delete Alias", command=lambda: self._delete_alias(alias_list))
        delete_button.pack(side=tk.LEFT, padx=5)
        
        close_button = tk.Button(button_frame, text="Close", command=alias_window.destroy)
        close_button.pack(side=tk.RIGHT, padx=5)
    
    def _add_alias(self, alias_list):
        """Adds a new alias."""
        alias = simpledialog.askstring("Add Alias", "Enter alias name (e.g., 'll'):", parent=alias_list.master)
        if not alias: return
        
        command = simpledialog.askstring("Add Alias", 
            f"Enter command for alias '{alias}' (e.g., 'ls -la'):\n\n"
            "Note: The command will be executed exactly as entered when you type the alias.",
            parent=alias_list.master)
        if not command: return
        
        # Check if alias already exists
        if alias in self.aliases:
            if not messagebox.askyesno("Alias Exists", 
                f"Alias '{alias}' already exists. Do you want to overwrite it?",
                parent=alias_list.master):
                return
        
        self.aliases[alias] = command
        self.save_config()
        
        # Update the listbox
        for i in range(alias_list.size()):
            if alias_list.get(i).startswith(f"{alias} = "):
                alias_list.delete(i)
                break
        alias_list.insert(tk.END, f"{alias} = {command}")
        
        # Show confirmation
        messagebox.showinfo("Alias Added", f"Alias '{alias}' set to: {command}", parent=alias_list.master)
    
    def _edit_alias(self, alias_list):
        """Edits an existing alias."""
        selection = alias_list.curselection()
        if not selection: return
        
        current = alias_list.get(selection[0])
        alias = current.split(" = ")[0]
        
        command = simpledialog.askstring("Edit Alias", 
            f"Enter new command for alias '{alias}':\n\n"
            "Note: The command will be executed exactly as entered when you type the alias.",
            initialvalue=self.aliases[alias], parent=alias_list.master)
        if not command: return
        
        self.aliases[alias] = command
        self.save_config()
        alias_list.delete(selection[0])
        alias_list.insert(selection[0], f"{alias} = {command}")
        
        # Show confirmation
        messagebox.showinfo("Alias Updated", f"Alias '{alias}' updated to: {command}", parent=alias_list.master)
    
    def _delete_alias(self, alias_list):
        """Deletes an existing alias."""
        selection = alias_list.curselection()
        if not selection: return
        
        current = alias_list.get(selection[0])
        alias = current.split(" = ")[0]
        
        # Get the parent window (alias management window)
        parent_window = alias_list.master
        
        if messagebox.askyesno("Delete Alias", f"Are you sure you want to delete alias '{alias}'?", parent=parent_window):
            del self.aliases[alias]
            self.save_config()
            alias_list.delete(selection[0])

if __name__ == "__main__":
    root = tk.Tk()
    app = TerminalApp(root)
    root.mainloop() 