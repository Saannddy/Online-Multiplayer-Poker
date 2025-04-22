// js/messageHandler.js
import { addLogMessage } from './utils.js';
import * as state from './state.js';
import { updateUI, handleShowdownReveal, handlePotAwarded, hideWinnerDisplay } from './ui.js';
import { enableActions, disableAllActions } from './actions.js';
import { Elements } from './config.js'; // For the game_state check


/** Handles messages received from the server */
export function handleServerMessage(data) {
    // Hide winner display on most messages, show it specifically on pot awarded
    if (data.type !== 'pot_awarded') {
        hideWinnerDisplay();
    }

    switch (data.type) {
        case 'assign_id':
            state.setMyPlayerId(data.payload.playerId);
            addLogMessage(`System: Assigned Player ID: ${state.myPlayerId}.`, "system");
            break;
        case 'game_state':
            updateUI(data.payload); // Delegate UI update
            // Disable actions if it's not our turn based on this state update
            if (Elements.actionArea.style.display === 'flex' && data.payload.current_player_id !== state.myPlayerId) {
                 console.log("GameState update: Disabling actions as it's not my turn.");
                 disableAllActions();
            }
            break;
        case 'player_turn':
            console.log(`Player turn message for P${data.payload.playerId}. My ID: ${state.myPlayerId}.`);
            if (data.payload.playerId === state.myPlayerId) {
                state.setCurrentTurnOptions(data.payload); // Store options in state
                enableActions(data.payload); // Delegate enabling actions
                addLogMessage("Game: It's YOUR turn!", "game");
            } else {
                disableAllActions();
                const currentPlayer = state.playerMap[data.payload.playerId];
                const playerName = currentPlayer?.name || `Player ${data.payload.playerId}`;
                addLogMessage(`Game: Waiting for ${playerName}...`, "game");
            }
            break;
        case 'game_message':
            addLogMessage(`Game: ${data.payload.message}`, "game");
            break;
        case 'player_action':
             const actor = state.playerMap[data.payload.playerId];
             const name = actor?.name || `Player ${data.payload.playerId}`;
             let actionMsg = `${data.payload.action}`;
             if (data.payload.amount != null) {
                 actionMsg += ` $${data.payload.amount}`;
             }
             addLogMessage(`${name}: ${actionMsg}`, "action");
             break;
         case 'pot_awarded':
             handlePotAwarded(data.payload); // Delegate
             break;
        case 'showdown':
             handleShowdownReveal(data.payload); // Delegate
             break;
        case 'error':
            addLogMessage(`Error: ${data.payload.message}`, "error");
            if (data.payload.message && data.payload.message.toLowerCase().includes("not your turn")) {
                disableAllActions();
            }
            break;
        default:
            console.warn("Unknown message type received:", data.type);
            addLogMessage(`System: Received unknown message type '${data.type}'`, "system");
    }
}