#!/usr/bin/env bash

# Resources:
# - http://www.defaults-write.com
# - https://gist.github.com/MatthewEppelsheimer/2269385
# - https://gist.github.com/vraravam/5e28ca1720c9dddacdc0e6db61e093fe
# - https://github.com/MartinHarding/macOSuckless
# - https://github.com/ddelange/new-mac-setup
# - https://github.com/herrbischoff/awesome-macos-command-line
# - https://github.com/mathiasbynens/dotfiles/blob/master/.macos
# - https://www.cs.rutgers.edu/~pxk/cs/macos-setup.html
# - https://www.learningosx.com/101-ways-to-tweak-os-x-using-terminal/

set -euxo pipefail

email_address="${1}"
hostname="${2}"

# Close open System Preferences panes, to prevent them from overriding settings.
osascript -e 'tell application "System Preferences" to quit'

login_window_text="This computer is attached to an Apple iCloud account \
and is valueless if lost.\nPlease return it by emailing ${email_address}."

# Set computer name.
sudo scutil --set ComputerName "${hostname}"
sudo scutil --set HostName "${hostname}"
sudo scutil --set LocalHostName "${hostname}"
sudo defaults write \
     /Library/Preferences/SystemConfiguration/com.apple.smb.server \
     NetBIOSName \
     -string "${hostname}"

# NOTE: disabled setting a desktop background picture because I haven't figured
# out how to set it to a dynamic background picture.
#
# desktop_picture="/Library/Desktop Pictures/Solid Colors/Solid Gray Pro Ultra Dark.png"
#
# Set desktop background picture.
# desktop_pictures_db="~/Library/Application Support/Dock/desktoppicture.db"
# sqlite3 "${desktop_pictures_db}" "update data set value = '${desktop_picture}'"

sudo defaults write \
     /Library/Preferences/com.apple.loginwindow \
     LoginwindowText \
     "$(echo -e "${login_window_text}")"

# Fade transition between workspaces instead of using movement.
defaults write com.apple.Accessibility ReduceMotionEnabled -bool true

readonly icloud_directory="${HOME}/Library/Mobile Documents/com~apple~CloudDocs"

# Save screenshots to the desktop.
defaults write \
         com.apple.screencapture \
         location \
         -string "${icloud_directory}/Screenshots"

################################################################################
# Dock #########################################################################
################################################################################

# Show only open applications in the Dock.
defaults write com.apple.dock static-only -bool true

# Minimization effect: 'genie', 'scale', 'suck'.
defaults write com.apple.dock mineffect -string 'scale'

# Don't animate opening applications. I don't open applications from the dock
# anyway...
defaults write com.apple.dock launchanim -bool false

# Automatically hide and show the Dock.
defaults write com.apple.dock autohide -bool true

# Make Dock icons of hidden applications translucent.
# NOTE: this doesn't seem to make a difference.
defaults write com.apple.dock showhidden -bool true

# Move dock to the left.
defaults write com.apple.dock orientation left

# Wipe all (default) app icons from the Dock.
defaults write com.apple.dock persistent-apps -array

# Don't rearrange spaces by most recently used.
defaults write com.apple.dock mru-spaces -bool false

# Remove the auto-hiding Dock delay.
# NOTE: this doesn't seem to make a difference.
defaults write com.apple.dock autohide-delay -float 0

# Remove the animation when hiding/showing the Dock.
defaults write com.apple.dock autohide-time-modifier -float 0.5

################################################################################
# Services #####################################################################
################################################################################

# Enable file sharing.
sudo launchctl load -w /System/Library/LaunchDaemons/com.apple.smbd.plist

# Disable Notification Center and remove the menu bar icon.
# FIXME: Operation not permitted while System Integrity Protection is engaged
# launchctl \
#   unload -w \
#   /System/Library/LaunchAgents/com.apple.notificationcenterui.plist \
#   2> /dev/null

################################################################################
# Zoom #########################################################################
################################################################################

zoom_plist="${HOME}/Library/Preferences/ZoomChat.plist"

defaults write "${zoom_plist}" ZoomEnterFullscreenWhenViewShare -bool false
defaults write "${zoom_plist}" ZoomEnterMaxWndWhenViewShare -bool false
defaults write "${zoom_plist}" ZMEnableShowUserName -bool true
defaults write "${zoom_plist}" ZoomEnableShow49WallViewKey -bool true
defaults write "${zoom_plist}" ZoomShowIconInMenuBar -bool false

################################################################################
# Finder #######################################################################
################################################################################

# Show hidden files.
defaults write com.apple.finder AppleShowAllFiles true

# Avoid creating .DS_Store files on network or USB volumes.
defaults write com.apple.desktopservices DSDontWriteNetworkStores -bool true
defaults write com.apple.desktopservices DSDontWriteUSBStores -bool true

# Automatically open a new Finder window when a volume is mounted.
defaults write com.apple.frameworks.diskimages auto-open-ro-root -bool true
defaults write com.apple.frameworks.diskimages auto-open-rw-root -bool true
defaults write com.apple.finder OpenWindowForNewRemovableDisk -bool true

# Use list view in all Finder windows by default.
# Icon View:   "icnv"
# List View:   "Nlsv"
# Column View: "clmv"
# Cover Flow:  "Flwv"
defaults write com.apple.finder FXPreferredViewStyle -string "Nlsv"

# Show bar with current directory path at the bottom.
defaults write com.apple.finder ShowPathbar -bool true

# Allow quitting via ⌘ + Q; doing so will also hide desktop icons.
defaults write com.apple.finder QuitMenuItem -bool true

# Disable window animations and Get Info animations.
defaults write com.apple.finder DisableAllAnimations -bool true

# New Finder windows open at $HOME.
# Computer:       "PfCm"
# Volume:         "PfVo"
# $HOME:          "PfHm"
# Desktop:        "PfDe"
# Documents:      "PfDo"
# All My Files:   "PfAF"
# Other:          "PfLo"
defaults write com.apple.finder NewWindowTarget -string "PfHm"
defaults write com.apple.finder NewWindowTargetPath -string "file://${HOME}"

# Don't show file icons in the desktop.
defaults write com.apple.finder CreateDesktop -bool false

# When performing a search, search the current folder by default.
# This Mac:       "SCev"
# Current Folder: "SCcf"
# Previous Scope: "SCsp"
defaults write com.apple.finder FXDefaultSearchScope -string "SCcf"

# Display full POSIX path as Finder window title.
defaults write com.apple.finder _FXShowPosixPathInTitle -bool true

# Keep folders on top when sorting files by name.
defaults write com.apple.finder _FXSortFoldersFirst -bool true

# Show expanded save panel by default.
# https://www.defaults-write.com/expand-save-panel-default/
defaults write NSGlobalDomain NSNavPanelExpandedStateForSaveMode -bool true
defaults write NSGlobalDomain NSNavPanelExpandedStateForSaveMode2 -bool true

# Show file extensions.
defaults write NSGlobalDomain AppleShowAllExtensions -bool true

################################################################################
# Menu bar #####################################################################
################################################################################

defaults write com.apple.menuextra.clock DateFormat -string "EEE d MMM HH:mm"

defaults write com.apple.systemuiserver menuExtras '(
  "/System/Library/CoreServices/Menu Extras/Clock.menu",
  "/System/Library/CoreServices/Menu Extras/Battery.menu",
  "/System/Library/CoreServices/Menu Extras/Volume.menu",
  "/System/Library/CoreServices/Menu Extras/Bluetooth.menu",
  "/System/Library/CoreServices/Menu Extras/VPN.menu"
)'

################################################################################
# Language #####################################################################
################################################################################

# Set language and text formats.
defaults write NSGlobalDomain AppleLanguages -array "en-US" "pt-BR" "de-DE"
defaults write NSGlobalDomain AppleLocale -string "en_DE"
defaults write NSGlobalDomain AppleMeasurementUnits -string "Centimeters"
defaults write NSGlobalDomain AppleMetricUnits -bool true

################################################################################
# Terminal #####################################################################
################################################################################

terminal_theme="Pro"

# Set default theme.
defaults write com.apple.Terminal "Startup Window Settings" -string "${terminal_theme}"

defaults write com.apple.Terminal "Default Window Settings" -string "${terminal_theme}"

# Make bash the default shell in Terminal.
defaults write com.apple.Terminal Shell -string "$(which bash)"

################################################################################
################################################################################
################################################################################

# Disable the sound effects on boot.
sudo nvram SystemAudioVolume=" "

# Disable sound effects: trash emptying, screenshot taking, file moving, etc.
defaults write "Apple Global Domain" com.apple.sound.uiaudio.enabled -int 0

# MacBookPro 2019 16"
# $ sudo pmset -g live
# System-wide power settings:
#  DestroyFVKeyOnStandby		0
# Currently in use:
#  standbydelaylow      10800
#  standby              1
#  halfdim              1
#  hibernatefile        /var/vm/sleepimage
#  proximitywake        0
#  gpuswitch            2
#  powernap             0
#  disksleep            10
#  standbydelayhigh     86400
#  sleep                1 (sleep prevented by coreaudiod)
#  hibernatemode        3
#  ttyskeepawake        1
#  displaysleep         2
#  tcpkeepalive         1
#  highstandbythreshold 50
#  acwake               0
#  lidwake              1

# MacBook Pro 2017 15" on 2021-03
# $ sudo pmset -g live
# System-wide power settings:
# Currently in use:
#  lidwake              1
#  autopoweroff         1
#  standbydelayhigh     86400
#  autopoweroffdelay    28800
#  standbydelaylow      10800
#  standby              1
#  proximitywake        1
#  ttyskeepawake        1
#  hibernatemode        3
#  powernap             0
#  gpuswitch            2
#  hibernatefile        /var/vm/sleepimage
#  highstandbythreshold 50
#  displaysleep         60
#  womp                 1
#  networkoversleep     0
#  sleep                0 (sleep prevented by nfsd, coreaudiod, mds_stores)
#  tcpkeepalive         1
#  halfdim              1
#  acwake               0
#  disksleep            10

# Dark menu bar and dock.
defaults write NSGlobalDomain AppleInterfaceStyle -string "Dark"

# Hide mirroring options in the menu bar.
defaults write com.apple.airplay showInMenuBarIfPresent -bool false

# Enable remote login.
# NOTE: this requires Full Disk Access privileges.
sudo systemsetup -setremotelogin on

# Quit printer app after print jobs complete.
defaults write com.apple.print.PrintingPrefs "Quit When Finished" -bool true

killall Dock > /dev/null 2>&1
killall Finder > /dev/null 2>&1
killall SystemUIServer > /dev/null 2>&1
killall Terminal > /dev/null 2>&1
