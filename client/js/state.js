// js/state.js

export let websocket = null;
export let myPlayerId = null;
export let playerMap = {}; // Stores player data received from server { id: { name, stack, ... } }
export let currentTurnOptions = null; // Stores actions available for the current player
export let winnerDisplayTimeout = null;

// Functions to update state (optional, but can be good practice)
export function setWebSocket(ws) {
    websocket = ws;
}
export function setMyPlayerId(id) {
    myPlayerId = id;
}
export function setPlayerMap(map) {
    playerMap = map;
}
export function setCurrentTurnOptions(options) {
    currentTurnOptions = options;
}
export function setWinnerDisplayTimeout(timeoutId) {
    winnerDisplayTimeout = timeoutId;
}
export function clearWinnerDisplayTimeout() {
    if (winnerDisplayTimeout) {
        clearTimeout(winnerDisplayTimeout);
        winnerDisplayTimeout = null;
    }
}