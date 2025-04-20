// js/websocket.js
import { WS_URL, Elements } from './config.js';
import { addLogMessage } from './utils.js';
import * as state from './state.js'; // Import state module
import { handleServerMessage } from './messageHandler.js'; // We'll create this next

/** Establishes WebSocket connection */
export function connectWebSocket() {
    addLogMessage("<strong>System:</strong> Attempting connection...", "system");
    if (state.websocket && state.websocket.readyState !== WebSocket.CLOSED) {
        addLogMessage("<strong>System:</strong> Connection already open or connecting.", "system");
        return;
    }

    const ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        addLogMessage("<strong>System:</strong> Connected! Please enter your name.", "system");
        Elements.nameModal.style.display = 'flex';
        Elements.nameInput.focus();
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            console.log("Received:", data);
            handleServerMessage(data); // Delegate message handling
        } catch (e) {
            console.error("Failed to parse message or handle:", e);
            addLogMessage("<strong>System:</strong> Error processing server message.", "error");
        }
    };

    // Make onclose async to handle dynamic imports with await
    ws.onclose = async (event) => {
        // Import necessary functions dynamically inside the async function
        // This helps avoid potential circular dependency issues at module load time
        const { disableAllActions } = await import('./actions.js');
        const { clearTable } = await import('./ui.js');

        const reason = event.reason ? ` Reason: ${event.reason}` : '';
        addLogMessage(`<strong>System:</strong> Disconnected.${reason} Retrying in 5s...`, "system");
        disableAllActions();
        clearTable();
        state.setMyPlayerId(null);
        state.setPlayerMap({});
        state.setWebSocket(null); // Clear the state's reference
        setTimeout(connectWebSocket, 5000);
    };

    ws.onerror = (error) => {
        console.error("WebSocket Error:", error);
        addLogMessage("<strong>System:</strong> WebSocket connection error.", "error");
    };

    state.setWebSocket(ws); // Update the shared state
}

/** Sends a message to the WebSocket server */
export function sendMessage(type, payload) {
    if (state.websocket && state.websocket.readyState === WebSocket.OPEN) {
        const message = JSON.stringify({ type, payload });
        console.log("Sending:", message);
        state.websocket.send(message);
    } else {
        addLogMessage("<strong>System:</strong> Cannot send message: Not connected.", "error");
    }
}