// js/main.js
import { Elements, MAX_NAME_LENGTH } from './config.js';
import { addLogMessage } from './utils.js';
import * as state from './state.js'; // Access shared state
import { connectWebSocket, sendMessage } from './websocket.js';
import { hideWinnerDisplay } from './ui.js';
import {
    disableAllActions,
    handleFoldClick,
    handleCheckClick,
    handleCallClick,
    handleBetClick,
    handleRaiseClick,
    handleSliderInput,
    handleNameSubmit
 } from './actions.js';


// --- Event Listeners Setup ---
function setupEventListeners() {
    Elements.nameSubmit.addEventListener('click', handleNameSubmit);
    Elements.nameInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            handleNameSubmit();
        }
    });

    Elements.foldButton.addEventListener('click', handleFoldClick);
    Elements.checkButton.addEventListener('click', handleCheckClick);
    Elements.callButton.addEventListener('click', handleCallClick);
    Elements.betButton.addEventListener('click', handleBetClick);
    Elements.raiseButton.addEventListener('click', handleRaiseClick);
    Elements.betSlider.addEventListener('input', handleSliderInput);
}

// --- Initialization ---
function initializeGame() {
    addLogMessage("System: Client initialized.", "system");
    disableAllActions();
    hideWinnerDisplay();
    Elements.nameModal.style.display = 'none'; // Start hidden
    setupEventListeners();
    connectWebSocket(); // Start connection
}

// Start the application
document.addEventListener('DOMContentLoaded', initializeGame);