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

CONFIG_FILE = "config.json" # Define config file name
HISTORY_FILE = ".command_history" # Define history file name
MAX_HISTORY_SIZE = 500 # Define max history size

class TerminalApp:
    def __init__(self, master):
        self.master = master
        master.title("AI Terminal")
        master.geometry("800x600")

        # Load config first
        self.config = self.load_config() # Load entire config dict
        self.background_color = self.config.get('background_color', 'black')
        self.font_family = self.config.get('font_family', 'monospace') # Load font family
        self.font_size = self.config.get('font_size', 10) # Load font size
        self.ssh_connections = self.config.get('ssh_connections', []) # Load or initialize ssh connections

        # --- Command History ---
        self.command_history = []
        self.load_history() # Load history from file
        self.history_index = -1 # -1 means current input, 0 is the last command, etc.
        # --- End Command History ---

        # Create font tuple
        self.terminal_font = (self.font_family, self.font_size)

        self.output_area = scrolledtext.ScrolledText(
            master, wrap=tk.WORD, state='disabled', bg=self.background_color, fg='lightgrey',
            font=self.terminal_font # Use loaded/default font
        )
        self.output_area.pack(expand=True, fill=tk.BOTH, padx=5, pady=(5, 0))

        # --- ANSI Color and Style Handling Setup ---
        # Regex to find SGR codes (m), OSC title codes (]), and bracketed paste codes (?2004h/l)
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
        self._define_ansi_colors()
        self._configure_ansi_tags()

        # State variables for current text style
        self.current_fg_tag = None
        self.current_bg_tag = None
        self.is_bold = False
        # --- End ANSI Setup ---

        self.input_frame = tk.Frame(master)
        self.input_frame.pack(fill=tk.X, padx=5, pady=5)

        self.prompt_label = tk.Label(self.input_frame, text="$", font=self.terminal_font) # Use loaded/default font
        self.prompt_label.pack(side=tk.LEFT)

        self.input_entry = tk.Entry(
            self.input_frame, bg='black', fg='white', insertbackground='white',
            font=self.terminal_font, # Use loaded/default font
            borderwidth=0
        )
        self.input_entry.pack(expand=True, fill=tk.X, side=tk.LEFT)
        self.input_entry.bind("<Return>", self.run_command)
        self.input_entry.bind("<Tab>", self.handle_tab_completion)
        self.input_entry.bind("<Up>", self.recall_previous_command) # Add Up arrow binding
        self.input_entry.bind("<Down>", self.recall_next_command) # Add Down arrow binding
        self.input_entry.focus_set()
        self.input_entry.icursor(END) # Move cursor to end

        # --- Menu Bar --- #
        self.menu_bar = Menu(master)
        master.config(menu=self.menu_bar)

        # Options Menu
        options_menu = Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="Options", menu=options_menu)
        options_menu.add_command(label="Set Background Color...", command=self.prompt_for_background_color)
        options_menu.add_command(label="Set Font...", command=self.prompt_for_font) # Add font option

        # --- SSH Connections Menu ---
        self.ssh_menu = Menu(self.menu_bar, tearoff=0)
        self.menu_bar.add_cascade(label="SSH Connections", menu=self.ssh_menu)
        self.populate_ssh_menu() # Populate the menu initially
        # --- End SSH Connections Menu ---

        # --- End Menu Bar --- #

        # Remove pty.openpty() - pty.fork() handles this
        self.master_fd = None # Initialize
        self.child_pid = None
        # self.shell_process = None # This wasn't used, removing

        self.start_shell()
        self.start_output_reader()

        master.protocol("WM_DELETE_WINDOW", self.on_close)

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

    def _configure_ansi_tags(self):
        """Configure Tkinter tags for ANSI colors and styles."""
        for code, color in self.ansi_fg_colors.items():
            self.output_area.tag_config(f"ansi_fg_{code}", foreground=color)
        for code, color in self.ansi_bg_colors.items():
            self.output_area.tag_config(f"ansi_bg_{code}", background=color)
        # Configure bold tag (font details are set here and updated in set_font)
        self.output_area.tag_config("ansi_bold", font=self.terminal_font + ('bold',))

    def _process_and_write_output(self, text):
        """Processes text containing ANSI codes and writes it to the output area with appropriate tags."""
        self.output_area.configure(state='normal')

        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        last_end = 0
        # Use the new comprehensive pattern
        for match in self.ansi_escape_pattern.finditer(text):
            start, end = match.span()
            # Write text before the match with current style
            text_part = text[last_end:start]
            if text_part:
                tags_to_apply = set()
                if self.current_fg_tag: tags_to_apply.add(self.current_fg_tag)
                if self.current_bg_tag: tags_to_apply.add(self.current_bg_tag)
                if self.is_bold: tags_to_apply.add("ansi_bold")
                self.output_area.insert(tk.INSERT, text_part, tuple(tags_to_apply) or None) # Pass None if no tags

            # Process the matched escape sequence based on type
            csi = match.group('csi') # Captured content *after* \x1b[
            osc = match.group('osc') # Captured content *after* \x1b]

            if csi:
                # Check if it's an SGR sequence (ends with 'm')
                if csi.endswith('m'):
                    # Pass only the numeric part to the SGR parser
                    sgr_codes_str = csi[:-1] # Remove the trailing 'm'
                    try:
                        codes = [int(c) if c else 0 for c in sgr_codes_str.split(';')]
                    except ValueError:
                        codes = [0] # Treat invalid code sequence as reset
                    self._update_current_style_from_sgr_codes(codes) # Call renamed function
                # else: it's another CSI sequence (like bracketed paste) - ignore it

            elif osc:
                pass # Ignore OSC sequences (like title setting) for now

            last_end = end

        # Write any remaining text after the last match
        remaining_text = text[last_end:]
        if remaining_text:
            tags_to_apply = set()
            if self.current_fg_tag: tags_to_apply.add(self.current_fg_tag)
            if self.current_bg_tag: tags_to_apply.add(self.current_bg_tag)
            if self.is_bold: tags_to_apply.add("ansi_bold")
            self.output_area.insert(tk.INSERT, remaining_text, tuple(tags_to_apply) or None)

        self.output_area.see(tk.END)
        self.output_area.configure(state='disabled')

    # Rename to be more specific
    def _update_current_style_from_sgr_codes(self, codes):
        """Updates the current style state based on a list of SGR codes."""
        code_idx = 0
        while code_idx < len(codes):
            code = codes[code_idx]
            if code == 0: # Reset
                self.current_fg_tag = None
                self.current_bg_tag = None
                self.is_bold = False
            elif code == 1: # Bold
                self.is_bold = True
            elif code == 22: # Normal intensity
                self.is_bold = False
            elif 30 <= code <= 37 or 90 <= code <= 97: # FG color
                tag = f"ansi_fg_{code}"
                # Check if the tag was actually configured (handles potential future gaps in codes)
                if tag in self.output_area.tag_names():
                    self.current_fg_tag = tag
                else:
                    print(f"Warning: Unconfigured ANSI FG code {code}") # Optional warning
                    self.current_fg_tag = None # Fallback to default
            elif code == 39: # Default FG color
                self.current_fg_tag = None
            elif 40 <= code <= 47 or 100 <= code <= 107: # BG color
                tag = f"ansi_bg_{code}"
                if tag in self.output_area.tag_names():
                    self.current_bg_tag = tag
                else:
                    print(f"Warning: Unconfigured ANSI BG code {code}") # Optional warning
                    self.current_bg_tag = None # Fallback to default
            elif code == 49: # Default BG color
                self.current_bg_tag = None
            # --- Placeholder for more styles (italic, underline, etc.) ---
            # elif code == 3: self.is_italic = True
            # elif code == 4: self.is_underline = True
            # elif code == 23: self.is_italic = False
            # elif code == 24: self.is_underline = False
            # --- Skip extended colors (38, 48) for now ---
            elif code == 38 or code == 48:
                if code_idx + 1 < len(codes):
                    color_type = codes[code_idx+1]
                    if color_type == 5 and code_idx + 2 < len(codes): code_idx += 2
                    elif color_type == 2 and code_idx + 4 < len(codes): code_idx += 4
                    else: code_idx += 1
                # Malformed sequence: just advance past the 38/48 code

            code_idx += 1 # Move to the next code in the sequence

    def write_output_safe(self, text):
        """Safely schedules processing and writing output from other threads."""
        self.master.after(0, self._process_and_write_output, text)

    def start_shell(self):
        """Starts the default shell process in a pseudo-terminal."""
        shell = os.environ.get('SHELL', '/bin/bash')
        try:
            # pty.fork() creates the PTY and forks the process
            pid, fd = pty.fork()
        except OSError as e:
            self.write_output_safe(f"Error forking pty: {e}\n")
            return

        if pid == 0:  # Child process
            # We are now in the child process.
            # The child inherits the pseudo-terminal's slave end as its stdin, stdout, stderr.
            # We don't need to, and shouldn't, close the master_fd here.

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
             self.child_pid = pid
             self.master_fd = fd
             # Set the master fd to non-blocking for use with select
             os.set_blocking(self.master_fd, False)

    def start_output_reader(self):
        """Starts a thread to continuously read output from the master pty."""
        self.reader_thread = threading.Thread(target=self.read_output, daemon=True)
        self.reader_thread.start()

    def read_output(self):
        """Reads output from the master pty and displays it."""
        while True:
            try:
                # Use select for non-blocking read with timeout
                r, _, _ = select.select([self.master_fd], [], [], 0.1)
                if self.master_fd in r:
                    try:
                        output = os.read(self.master_fd, 1024)
                        if not output:  # EOF, process likely exited
                            self.write_output_safe("\nShell process exited.\n")
                            # Attempt to clean up process state
                            try:
                                os.waitpid(self.child_pid, os.WNOHANG)
                            except ChildProcessError: # Already reaped?
                                pass
                            self.child_pid = None
                            # Optionally, restart the shell or close the app
                            # For now, we'll just stop reading
                            break # Exit the reading loop

                        # Explicitly decode as UTF-8
                        decoded_output = output.decode('utf-8', errors='replace')

                        # Pass the raw decoded output with potential ANSI codes
                        self.write_output_safe(decoded_output)
                    except OSError: # E.g., EIO when process exits
                        self.write_output_safe("\nError reading from shell.\n")
                        # Attempt to clean up process state
                        if self.child_pid:
                            try:
                                os.waitpid(self.child_pid, os.WNOHANG)
                            except ChildProcessError: # Already reaped?
                                pass
                        self.child_pid = None
                        break # Exit the reading loop
            except Exception as e:
                # Catch potential select errors or other issues
                self.write_output_safe(f"\nReader thread error: {e}\n")
                break # Exit the loop on significant errors

    def run_command(self, event=None):
        """Sends the command from the input entry to the shell process."""
        command = self.input_entry.get()
        self.input_entry.delete(0, END)

        # Add command to history if it's not empty and not the same as the last one
        if command.strip(): # Don't save empty commands
            if not self.command_history or self.command_history[-1] != command:
                self.command_history.append(command)
        self.history_index = -1 # Reset history navigation index

        if self.child_pid is not None:
            # Ensure a single newline character is added
            full_command = command + "\n"
            try:
                os.write(self.master_fd, full_command.encode())
            except OSError as e:
                self.write_output_safe(f"Error writing to shell: {e}\n")
                # Consider the shell dead if write fails
                self.child_pid = None
        else:
            self.write_output_safe("Shell process is not running.\n")

        return 'break' # Prevents Tkinter from inserting a literal Tab character

    def handle_tab_completion(self, event=None):
        """Handles Tab key press for basic path completion."""
        cursor_pos = self.input_entry.index(tk.INSERT)
        line = self.input_entry.get()
        
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
            self.input_entry.delete(start_index, cursor_pos)
            self.input_entry.insert(start_index, match)
        else:
            # Multiple matches: find common prefix
            common_prefix = os.path.commonprefix(matches)
            if common_prefix and common_prefix != line[start_index:cursor_pos]:
                # If there's a common prefix longer than current word, complete it
                self.input_entry.delete(start_index, cursor_pos)
                self.input_entry.insert(start_index, common_prefix)
            
            # Print all matches to the output area for user reference
            # Get terminal width (approximate) for formatting
            try:
                term_cols = os.get_terminal_size().columns
            except OSError: # Fallback if not connected to a real terminal (e.g., IDE)
                term_cols = 80
            
            output_str = "\n" + '  '.join(os.path.basename(m) if os.path.isdir(m) else os.path.basename(m) for m in matches) # Show basenames
            # Simple wrap attempt (doesn't handle long names well)
            # wrapped_output = '\n'.join(output_str[i:i+term_cols] for i in range(0, len(output_str), term_cols))
            self.write_output_safe(output_str + "\n") # Add extra newline for clarity
            # Reprint prompt and current input line after showing matches
            # Avoid writing the prompt here if we just showed completions,
            # as the shell itself will likely reprint the prompt soon.
            # self.write_output_safe(f"${line}")

        return 'break' # Prevents Tkinter from inserting a literal Tab character

    # --- Command History Navigation ---
    def recall_previous_command(self, event=None):
        """Recalls the previous command from history."""
        if not self.command_history:
            return 'break' # No history

        if self.history_index < len(self.command_history) - 1:
            self.history_index += 1
            self.input_entry.delete(0, END)
            # History is stored chronologically, so index 0 is oldest, -1 is newest
            # To go "up", we increase index from -1 towards len()-1
            # We access history from the end: -(index + 1)
            self.input_entry.insert(0, self.command_history[-(self.history_index + 1)])
            self.input_entry.icursor(END) # Move cursor to end

        return 'break' # Prevent default Up arrow behavior

    def recall_next_command(self, event=None):
        """Recalls the next command from history (or clears input)."""
        if not self.command_history:
            return 'break' # No history

        if self.history_index >= 0:
            self.history_index -= 1
            self.input_entry.delete(0, END)
            if self.history_index == -1:
                # Reached the "current" command line, leave it empty
                pass
            else:
                # Access history from the end: -(index + 1)
                self.input_entry.insert(0, self.command_history[-(self.history_index + 1)])
                self.input_entry.icursor(END) # Move cursor to end
        else:
            # Already at the current input line, do nothing further back
            pass

        return 'break' # Prevent default Down arrow behavior
    # --- End Command History Navigation ---

    # --- History Loading/Saving ---
    def load_history(self):
        """Loads command history from the history file."""
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r') as f:
                    # Read lines, strip whitespace, filter out empty lines
                    self.command_history = [line.strip() for line in f if line.strip()]
                    # Keep only the most recent MAX_HISTORY_SIZE entries if file is larger
                    if len(self.command_history) > MAX_HISTORY_SIZE:
                        self.command_history = self.command_history[-MAX_HISTORY_SIZE:]
        except IOError as e:
            print(f"Warning: Could not load command history from '{HISTORY_FILE}': {e}")
        except Exception as e:
            print(f"Unexpected error loading history: {e}")

    def save_history(self):
        """Saves the command history to the history file, truncating if needed."""
        try:
            with open(HISTORY_FILE, 'w') as f:
                # Determine the slice to save (last MAX_HISTORY_SIZE entries)
                start_index = max(0, len(self.command_history) - MAX_HISTORY_SIZE)
                history_to_save = self.command_history[start_index:]
                for command in history_to_save:
                    f.write(command + "\n")
        except IOError as e:
            print(f"Warning: Could not save command history to '{HISTORY_FILE}': {e}")
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
        except Exception as e:
            print(f"Unexpected error loading config: {e}")
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

        # --- Execute the command directly --- 
        self.write_output_safe(f"\nAttempting SSH connection: {command_str}\n") # Echo command to user

        if self.child_pid is not None:
            # Ensure a single newline character is added
            full_command = command_str + "\n"
            try:
                os.write(self.master_fd, full_command.encode())
            except OSError as e:
                self.write_output_safe(f"Error writing to shell: {e}\n")
                # Consider the shell dead if write fails
                self.child_pid = None 
        else:
            self.write_output_safe("Shell process is not running. Cannot initiate SSH connection.\n")
        # --- End execution --- 

        # # Clear current input and insert the command
        # self.input_entry.delete(0, tk.END)
        # self.input_entry.insert(0, command_str)
        # self.input_entry.focus_set() # Set focus back to input
        # self.write_output_safe(f"\nPrepared command: {command_str}\nPress Enter to run.\n$") # Inform user

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
                self.output_area.configure(bg=color) # Test apply
                # If configure didn't raise error, color is valid
                self.set_background_color(color) # Call the method that also saves
            except tk.TclError:
                # Error handled by Tkinter/Tcl, but provide user feedback
                print(f"Invalid color specified: {color}") # Log to console
                # Optionally show a messagebox
                # from tkinter import messagebox
                # messagebox.showerror("Invalid Color", f"Could not set background to '{color}'.", parent=self.master)
                # Revert to the current valid color (redundant if configure failed, but safe)
                self.output_area.configure(bg=self.background_color)

    def set_background_color(self, color):
        """Sets the background color of the terminal output area."""
        self.background_color = color
        self.output_area.configure(bg=self.background_color)
        self.save_config() # Save after successful change

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
            # Test the font on a widget before applying everywhere
            self.output_area.configure(font=new_font)
            # Also test/update the bold tag's font
            self.output_area.tag_configure("ansi_bold", font=new_font_bold)

            # If the above lines didn't raise an error, the font is likely valid
            self.font_family = family
            self.font_size = size
            self.terminal_font = new_font

            # Apply to all relevant widgets
            self.prompt_label.configure(font=self.terminal_font)
            self.input_entry.configure(font=self.terminal_font)
            # Output area already configured during the test

            self.save_config() # Save the new font settings

        except tk.TclError as e:
            messagebox.showerror("Invalid Font",
                               f"Could not apply font: '{family}' size {size}.\nError: {e}",
                               parent=self.master)
            # Revert the test change on output_area if it failed
            # Ensure we revert to the *last known good* font
            good_font = (self.font_family, self.font_size)
            good_font_bold = good_font + ('bold',)
            self.output_area.configure(font=good_font)
            self.output_area.tag_configure("ansi_bold", font=good_font_bold) # Revert bold tag font too
    # --- End Font Management --- #

    def on_close(self):
        """Handles window closing event."""
        if self.child_pid:
            try:
                # Try to terminate the child process group gracefully
                os.killpg(os.getpgid(self.child_pid), 15) # SIGTERM
                # Give it a moment, then force kill if needed
                try:
                    os.waitpid(self.child_pid, os.WNOHANG) # Check if already exited
                except ChildProcessError:
                    pass # Already gone
                else:
                    # Could add a small sleep and check again before SIGKILL
                    # os.killpg(os.getpgid(self.child_pid), 9) # SIGKILL
                    pass # For now, just rely on SIGTERM
            except ProcessLookupError:
                pass # Process already gone
            except Exception as e:
                print(f"Error during cleanup: {e}") # Log error

        if hasattr(self, 'master_fd') and self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                 pass # Already closed or invalid

        self.save_history() # Save history on close
        self.master.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = TerminalApp(root)
    root.mainloop() 