-- tell application "System Preferences"
-- 	activate
-- 	set current pane to pane "com.apple.preference.keyboard"
-- end tell

-- delay 1

-- tell application "System Events"
-- 	tell process "System Preferences"
-- 		get properties

-- 		click button "Modifier Keys…" of tab group 1 of window "Keyboard"
-- 		tell sheet 1 of window "Keyboard"
-- 			click pop up button 2
-- 			click menu item 2 of menu 1 of pop up button 2
-- 			click button "OK"
-- 		end tell
-- 	end tell

-- 	tell application "System Preferences" to quit
-- end tell


-- -- Set the new keycode for "caps lock"
-- set capsLockKeycode to 0

-- -- Set the new keycode for "control"
-- set controlKeycode to 59

-- -- Get the current settings for "caps lock"
-- tell application "System Preferences"
--     activate
--     reveal anchor "keyboardTab" of pane "com.apple.preference.keyboard"
-- end tell

-- tell application "System Events"
--     tell application process "System Preferences"
--         click checkbox 1 of tab group 1 of window 1
--     end tell
-- end tell

-- open location "x-apple.systempreferences:com.apple.Keyboard-Settings.extension"

-- tell application "System Events" to tell process "System Settings"
--     tell window 1
--         -- # example window title: "Keyboard – ￼86%", so "begins with" is needed
--         repeat until window begins with "Keyboard" exists
--         end repeat

--         -- # wait until Keyboard window is the main window of the application and is accessible
--         -- repeat until exists of (1st window whose value of attribute "AXMain" is true)
--         -- end repeat

--         -- # wait until the group is displayed (needed else fails on Apple M2 Pro)
--         -- repeat until exists group 1 of group 2 of splitter group 1 of group 1 of window 1
--         -- end repeat

--         -- # "Keyboard Shortcuts..." Button
--         click button "Keyboard Shortcuts…" of group 2 of scroll area 1 of group 1 of group 2 of splitter group 1 of group 1

--         -- repeat until sheet 1 of window 1 exists
--         -- end repeat
--     end tell
-- end tell

tell application "System Preferences"
    activate
    tell application "System Events"
        tell application process "System Settings"
            tell splitter group 1 of group 1
                tell scroll area 1 of group 1 of group 2
                    click button "Keyboard Shortcuts…"
                end tell
            end tell
        end tell
    end tell
end tell

-- Remap the "caps lock" key to "control"
-- tell application "System Preferences"
--     activate
--     set current pane to pane "com.apple.preference.keyboard"
--     tell application "System Events"
--         tell process "System Preferences"
--             click button "Keyboard Shortcuts…" of tab group 1 of window "Keyboard"
--             delay 0.5
--             -- tell sheet 1 of window "Keyboard"
--             --     tell pop up button 1
--             --         click
--             --         delay 0.5
--             --         click menu item "⌃ Control" of menu 1
--             --     end tell
--             --     click button "OK"
--             -- end tell
--         end tell
--     end tell
--     quit
-- end tell

-- Tell the user that the remapping is complete
-- display dialog "The 'caps lock' key has been remapped to 'control'." buttons {"OK"} default button "OK"
