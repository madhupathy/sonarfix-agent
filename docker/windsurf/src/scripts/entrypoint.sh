#!/bin/bash
set -e
WINDSURF_PATH="~/.config/Windsurf"
LOGS_PATH=$WINDSURF_PATH/logs
RESOURCES_PATH=/home/ubuntu

WORKSPACE_PATH=/home/ubuntu/workspace
WORKSPACE_WINDSURF_PATH=$WORKSPACE_PATH/.windsurf
WORKSPACE_WORKFLOWS_PATH=$WORKSPACE_WINDSURF_PATH/workflows
INSTRUCTIONS_FILE=$WORKSPACE_PATH/windsurf-instructions.txt
OUTPUT_FILE=$WORKSPACE_PATH/windsurf-output.txt

FINALIZATION_MARKER="WORK-COMPLETED"

SCREENSHOTS_PATH=/home/ubuntu/screenshots

export DISPLAY=":1"

## Util functions
n=0
function captureStep() {
    n=$((n+1))
    mkdir -p $SCREENSHOTS_PATH
    xwd -display :1 -root -silent | convert xwd:- png:/$SCREENSHOTS_PATH/screenshot-$n.png
}

function log() {
    local message="$1"
    echo "ENTRYPOINT $(date +'%Y-%m-%d %H:%M:%S') - $message"
}

function pause() {
    pause_seconds=${1:-5}
    sleep $pause_seconds
}

function longpause() {
    pause_seconds=15
    sleep $pause_seconds
}

function waitForWindow() {
    log "Waiting for Windsurf window to appear..."
    local attempts=0
    while [ $attempts -lt 60 ]; do
        WINDSURF_WID=$(xdotool search --name "Visual Studio Code" 2>/dev/null | head -1)
        if [ -z "$WINDSURF_WID" ]; then
            WINDSURF_WID=$(xdotool search --name "Windsurf" 2>/dev/null | head -1)
        fi
        if [ -z "$WINDSURF_WID" ]; then
            WINDSURF_WID=$(xdotool search --onlyvisible --name "" 2>/dev/null | head -1)
        fi
        if [ -n "$WINDSURF_WID" ]; then
            log "Window detected! WID=$WINDSURF_WID"
            export WINDSURF_WID
            return 0
        fi
        attempts=$((attempts+1))
        sleep 2
    done
    log "WARNING: No window detected after 120s, continuing anyway..."
    return 0
}

function focusWindow() {
    if [ -n "$WINDSURF_WID" ]; then
        xdotool windowactivate --sync $WINDSURF_WID 2>/dev/null || true
        xdotool windowfocus --sync $WINDSURF_WID 2>/dev/null || true
        sleep 1
    fi
}

function waitForWindsurfReady() {
    log "Waiting for Windsurf to fully initialize..."
    local attempts=0
    while [ $attempts -lt 60 ]; do
        if grep -q "CommandService#executeCommand" ~/windsurf-ui.log 2>/dev/null; then
            log "Windsurf UI is responsive!"
            return 0
        fi
        attempts=$((attempts+1))
        sleep 2
    done
    log "WARNING: Windsurf may not be fully loaded after 120s, continuing..."
    return 0
}

function guiTypeLine () {
    local line="$1"
    xdotool type "$line"
    xdotool key "Return"
}

function guiRunEditorCommand() {
    local command="$1"
    log "Running editor command: $command"
    focusWindow
    xdotool key --window $WINDSURF_WID "ctrl+shift+p" 2>/dev/null || xdotool key "ctrl+shift+p"
    sleep 3
    focusWindow
    xdotool type --clearmodifiers --delay 30 "$command"
    sleep 1
    xdotool key "Return"
}

function dismissWelcomeScreen() {
    log "Dismissing Welcome screen (clicking Get Started)..."
    focusWindow
    # The 'Get Started' button is roughly centered horizontally and at ~367px vertically
    # on a 1920x1080 screen. Click it.
    xdotool mousemove --window $WINDSURF_WID 512 367 2>/dev/null || xdotool mousemove 512 367
    sleep 1
    xdotool click 1
    sleep 3
    # Also try pressing Enter in case the button is focused
    xdotool key "Return"
    sleep 2
    # Press Escape to dismiss any follow-up dialogs
    xdotool key "Escape"
    sleep 1
    xdotool key "Escape"
    sleep 1
    log "Welcome screen dismissed"
}

## Steps definitions
function checkTokenIsPresent() {
    # If the environment variable WINDSURF_TOKEN is not set or empty
    if [ -z "$WINDSURF_TOKEN" ]; then
        log "WINDSURF_TOKEN needs to be passed as an environment variable."
        exit 1
    fi
}

function windsurfLogin() {
    log "Logging in to Windsurf with token"
    local max_retries=3
    local attempt=0

    while [ $attempt -lt $max_retries ]; do
        attempt=$((attempt+1))
        log "Login attempt $attempt/$max_retries"

        focusWindow
        guiRunEditorCommand "Provide Auth Token"
        sleep 5

        focusWindow
        # Type token character by character with delays
        xdotool type --clearmodifiers --delay 30 "$WINDSURF_TOKEN"
        sleep 2
        xdotool key "Return"
        sleep 8

        # Check if login succeeded by looking for API key errors
        if grep -q "api_key: value length must be at least 1" ~/windsurf-ui.log 2>/dev/null; then
            log "Login attempt $attempt may have failed, checking..."
            xdotool key "Escape"
            sleep 2
        else
            log "Login appears successful!"
            break
        fi
    done

    xdotool key "Escape" # Close any remaining dialogs
}

function startWindowManager() {
    log "Starting Xvfb with screen 1920x1080x24"
    Xvfb $DISPLAY -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
    disown
    sleep 2
    i3 2>/dev/null 1>/dev/null &
    disown
    log "Xvfb is ready!"
}

function startWindsurf() {
    cd $WORKSPACE_PATH
    # Make sure the workflow is present in the workspace
    mkdir -p $WORKSPACE_WORKFLOWS_PATH
    cp /home/ubuntu/entry-workflow.md $WORKSPACE_WORKFLOWS_PATH
    log "Starting Windsurf editor at DISPLAY=$DISPLAY"
    #windsurf --no-sandbox --user-data-dir /home/ubuntu &
    windsurf --disable-workspace-trust --disable-gpu --disable-dev-shm-usage --no-sandbox --verbose . 2>&1 > ~/windsurf-ui.log &
    disown
}

function waitUnitFinished() {
    touch $OUTPUT_FILE # Ensure the output file exists
    tail -f $OUTPUT_FILE | grep -q "$FINALIZATION_MARKER"
    log "Workflow completed successfully!"
}

function guiStartWorkflow() {
    focusWindow
    xdotool key --window $WINDSURF_WID "ctrl+l" 2>/dev/null || xdotool key "ctrl+l"
    sleep 3
    focusWindow
    xdotool type --clearmodifiers --delay 30 "/entry-workflow"
    sleep 2
    xdotool key "Return"
    sleep 2
    xdotool key "Return"
}

# TODO: This function is not currently used because it needs to be reviewed.
#       As an idea, it is a more robust alternative to pauses
#function waitUntilWindsurfIsReady() {
#    local readyMsg="LS lspClient started successfully"
#
#    logs_dir=$LOGS_PATH/$(ls -t1 $LOGS_PATH | head -n 1)
#    log "Waiting for Windsurf to be ready..."
#    
#    windsurf_log_file="$logs_dir/window1/exthost/codeium.windsurf/Windsurf.log"
#
#    tail -f $windsurf_log_file | grep -q 'LS lspClient started successfully'
#    log "Windsurf is ready!"
#}

## Steps execution

checkTokenIsPresent

startWindowManager
pause 3
startWindsurf
waitForWindow
waitForWindsurfReady
longpause
captureStep
dismissWelcomeScreen
captureStep
windsurfLogin
log "Waiting for login to settle..."
longpause
captureStep
guiStartWorkflow
captureStep
log "Waiting for workflow to complete..."
waitUnitFinished
captureStep
log "All done!"