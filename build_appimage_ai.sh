#!/bin/bash

# Exit on error
set -e

# Create AppDir structure
mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/applications
mkdir -p AppDir/usr/share/icons/hicolor/256x256/apps

# Copy application files
cp main.py AppDir/usr/bin/ai-terminal
cp terminal_icon.png AppDir/usr/share/icons/hicolor/256x256/apps/ai-terminal.png

# Copy desktop entry to both locations (root and applications directory)
cp ai-terminal.desktop AppDir/usr/share/applications/
cp ai-terminal.desktop AppDir/ai-terminal.desktop  # Required in root of AppDir

# Create virtual environment in AppDir
python -m venv AppDir/usr/venv
source AppDir/usr/venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install open-interpreter  # Ensure AI functionality is available

# Create AppRun script
cat > AppDir/AppRun << 'EOL'
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin/:${HERE}/usr/venv/bin/:${HERE}/usr/sbin/:$PATH"
export PYTHONPATH="${HERE}/usr/venv/lib/python3.11/site-packages:$PYTHONPATH"
export LD_LIBRARY_PATH="${HERE}/usr/lib/:$LD_LIBRARY_PATH"

# Execute the Python script
exec "${HERE}/usr/venv/bin/python" "${HERE}/usr/bin/ai-terminal" "$@"
EOL

chmod +x AppDir/AppRun

# Also copy the icon to the root of AppDir (often required)
cp terminal_icon.png AppDir/.DirIcon
cp terminal_icon.png AppDir/ai-terminal.png

# Download AppImage tool if not present
if [ ! -f "appimagetool-x86_64.AppImage" ]; then
    wget -c "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x appimagetool-x86_64.AppImage
fi

# Clean up any existing AppImage
rm -f AI_Terminal_Full-x86_64.AppImage  # Different name to avoid conflict with non-AI version

# Build AppImage
ARCH=x86_64 ./appimagetool-x86_64.AppImage AppDir AI_Terminal_Full-x86_64.AppImage 