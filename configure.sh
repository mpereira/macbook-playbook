#!/usr/bin/env bash

# Resources:
# - http://www.defaults-write.com

readonly COMPUTER_NAME='balloon'
readonly DESKTOP_PICTURE="\
/Library\
/Desktop Pictures\
/Solid Colors\
/Solid Gray Pro Ultra Dark.png"
readonly LOGIN_WINDOW_TEXT="This computer is attached to an Apple iCloud account \
and is valueless if lost.\nPlease return it by emailing murilo@murilopereira.com."

# info() {
#   echo "$(tput setaf 2)•$(tput sgr0) ${1}"
# }

# request() { # output a message and open an app
#   local message="${1}"
#   local app="${2}"
#   shift 2

#   echo "$(tput setaf 5)•$(tput sgr0) ${message}"
#   open -Wa "${app}" --args "$@" # don't continue until app closes
# }

# # Open 'System Preferences' in specified pane.
# preferences_pane() {
#   osascript -e "tell application \"System Preferences\"
#     reveal pane \"${1}\"
#     activate
#   end tell" &> /dev/null
# }

# # Open 'System Preferences' in specified pane and tab.
# preferences_pane_anchor() {
#   osascript -e "tell application \"System Preferences\"
#     reveal anchor \"${1}\" of pane \"${2}\"
#     activate
#   end tell" &> /dev/null
# }

# # 'request' for System Preferences.
# request_preferences() {
#   request "${1}" 'System Preferences'
# }

# Set computer name.
sudo scutil --set ComputerName "${COMPUTER_NAME}"
sudo scutil --set HostName "${COMPUTER_NAME}"
sudo scutil --set LocalHostName "${COMPUTER_NAME}"
sudo defaults write /Library/Preferences/SystemConfiguration/com.apple.smb.server NetBIOSName -string "${COMPUTER_NAME}"

# Set desktop background picture.
sqlite3 ~/Library/Application\ Support/Dock/desktoppicture.db "update data set value = '${DESKTOP_PICTURE}'"

sudo defaults write /Library/Preferences/com.apple.loginwindow LoginwindowText "$(echo -e "${LOGIN_WINDOW_TEXT}")"

# Fade transition between workspaces instead of using movement.
defaults write com.apple.universalaccess reduceMotion -bool true

# Minimization effect: 'genie', 'scale', 'suck'
defaults write com.apple.dock mineffect -string 'scale'

# Don't animate opening applications.
defaults write com.apple.dock launchanim -bool false

# Disable the sound effects on boot.
sudo nvram SystemAudioVolume=" "

# Restart automatically if the computer freezes.
sudo systemsetup -setrestartfreeze on

# New Finder windows open at $HOME
defaults write com.apple.finder NewWindowTarget -string "PfLo"
defaults write com.apple.finder NewWindowTargetPath -string "file://${HOME}"

# Keep desktop icons hidden.
defaults write com.apple.finder CreateDesktop -bool false

# Dark menu bar and dock.
defaults write NSGlobalDomain AppleInterfaceStyle -string "Dark"

# Automatically hide and show the Dock.
defaults write com.apple.dock autohide -bool true

# Make Dock icons of hidden applications translucent.
defaults write com.apple.dock showhidden -bool true

# Move dock to the left.
defaults write com.apple.dock orientation left

# Wipe all (default) app icons from the Dock.
defaults write com.apple.dock persistent-apps -array

# Don't rearrange spaces by most recently used.
defaults write com.apple.dock mru-spaces -bool false

# Remove the auto-hiding Dock delay.
# defaults write com.apple.dock autohide-delay -float 0

# Remove the animation when hiding/showing the Dock.
# defaults write com.apple.dock autohide-time-modifier -float 0.5

# Make fish the default shell.
defaults write com.apple.Terminal Shell -string "/usr/local/bin/fish"

# Make "Homebrew" be the default theme.
defaults write com.apple.Terminal "Startup Window Settings" -string "Homebrew"
defaults write com.apple.Terminal "Default Window Settings" -string "Homebrew"

# Hide scroll bar.
defaults write com.apple.Terminal AppleShowScrollBars -string WhenScrolling

# Menu bar clock format.
defaults write com.apple.menuextra.clock DateFormat -string "EEE MMM d  H:mm"

# Hide mirroring options in the menu bar.
defaults write com.apple.airplay showInMenuBarIfPresent -bool false

# Disable creation of metadata files on network volumes.
defaults write com.apple.desktopservices DSDontWriteNetworkStores -bool true

# Disable autocorrect.
# defaults write -g NSAutomaticSpellingCorrectionEnabled -bool false

# Enable remote login.
sudo systemsetup -setremotelogin on

# Quit printer app after print jobs complete.
defaults write com.apple.print.PrintingPrefs "Quit When Finished" -bool true

killall Dock 2>&1 > /dev/null
killall Finder 2>&1 > /dev/null
killall SystemUIServer 2>&1 > /dev/null
killall Terminal 2>&1 > /dev/null
