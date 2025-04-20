// js/actions.js
import { Elements, MAX_NAME_LENGTH } from './config.js';
import { addLogMessage } from './utils.js';
import * as state from './state.js';
import { sendMessage } from './websocket.js';

/** Disables all player action buttons and hides the action area */
export function disableAllActions() {
    console.log("Disabling actions.");
    Elements.foldButton.disabled = true;
    Elements.checkButton.disabled = true;
    Elements.callButton.disabled = true;
    Elements.betButton.disabled = true;
    Elements.raiseButton.disabled = true;
    Elements.betSlider.disabled = true;
    Elements.betSliderContainer.style.display = 'none';
    Elements.actionArea.style.display = 'none';
    state.setCurrentTurnOptions(null); // Clear stored options in state
}

/** Enables relevant action buttons based on server options */
export function enableActions(options) {
    console.log("Enabling actions with options:", options);
    Elements.actionArea.style.display = 'flex';

    const availableActions = options.actions || [];
    const canFold = availableActions.includes('fold');
    const canCheck = availableActions.includes('check');
    const canCall = availableActions.includes('call');
    const canBet = availableActions.includes('bet');
    const canRaise = availableActions.includes('raise');
    const callAmount = options.callAmount || 0;
    const minBetRaise = options.minRaise || 0;
    const maxBetRaise = options.maxRaise || minBetRaise;
    const playerStack = options.stack || 0;
    const bigBlind = options.bigBlind || 10;

    Elements.foldButton.disabled = !canFold;
    Elements.checkButton.disabled = !canCheck;
    Elements.checkButton.style.display = canCheck ? 'inline-block' : 'none';
    Elements.callButton.disabled = !canCall;
    const displayCallAmount = Math.min(callAmount, playerStack);
    Elements.callButton.textContent = `Call $${displayCallAmount}`;
    Elements.callButton.style.display = canCall ? 'inline-block' : 'none';
    Elements.betButton.style.display = canBet ? 'inline-block' : 'none';
    Elements.betButton.disabled = !canBet;
    Elements.raiseButton.style.display = canRaise ? 'inline-block' : 'none';
    Elements.raiseButton.disabled = !canRaise;

    if (canBet || canRaise) {
        Elements.betSliderContainer.style.display = 'flex';
        Elements.betSlider.disabled = false;
        const minVal = Math.max(0, minBetRaise);
        const maxVal = Math.max(minVal, maxBetRaise);
        const stepVal = Math.max(1, Math.min(bigBlind, Math.floor(maxVal / 10) || 1));

         if (typeof minVal !== 'number' || isNaN(minVal) || typeof maxVal !== 'number' || isNaN(maxVal) || typeof stepVal !== 'number' || isNaN(stepVal) || stepVal <= 0) {
             console.error("Invalid slider range/step values:", { minVal, maxVal, stepVal });
             addLogMessage("<strong>Error:</strong> Invalid bet range received.", "error");
             Elements.betSlider.min = 0; Elements.betSlider.max = 0; Elements.betSlider.step = 1; Elements.betSlider.value = 0;
             Elements.betSlider.disabled = true; Elements.betButton.disabled = true; Elements.raiseButton.disabled = true;
             Elements.betAmountLabel.textContent = "$??";
         } else {
            Elements.betSlider.min = minVal; Elements.betSlider.max = maxVal; Elements.betSlider.step = stepVal; Elements.betSlider.value = minVal;
            Elements.betAmountLabel.textContent = `$${minVal}`;
            updateBetRaiseButtonText(minVal, canBet, canRaise);
         }
    } else {
        Elements.betSliderContainer.style.display = 'none';
        Elements.betSlider.disabled = true;
    }
}

/** Updates the text on Bet/Raise buttons based on the slider value */
export function updateBetRaiseButtonText(amount, canBet, canRaise) {
     const numAmount = parseInt(amount, 10);
     if (isNaN(numAmount)) return;
     if (canBet && Elements.betButton.style.display !== 'none') Elements.betButton.textContent = `Bet $${numAmount}`;
     if (canRaise && Elements.raiseButton.style.display !== 'none') Elements.raiseButton.textContent = `Raise to $${numAmount}`;
}

/** Validates the bet/raise amount against current turn options */
export function validateBetRaiseAmount(amount) {
    if (!state.currentTurnOptions || isNaN(amount)) {
        console.error("Validation failed: No turn options or invalid amount", { turnOptions: state.currentTurnOptions, amount });
        return false;
    }
    const minValid = state.currentTurnOptions.minRaise || 0;
    const maxValid = state.currentTurnOptions.maxRaise || 0;

    if (amount >= minValid && amount <= maxValid) {
        return true;
    } else {
        addLogMessage(`<strong>System:</strong> Invalid amount ($${amount}). Min: $${minValid}, Max: $${maxValid}`, "error");
        console.error("Client-side validation failed:", { amount, minValid, maxValid });
        return false;
    }
}

// --- Action Event Handlers (called from main.js) ---
export function handleFoldClick() {
    if (!Elements.foldButton.disabled) {
        sendMessage("player_action", { action: "fold" });
        disableAllActions();
    }
}
export function handleCheckClick() {
     if (!Elements.checkButton.disabled) {
        sendMessage("player_action", { action: "check" });
        disableAllActions();
    }
}
export function handleCallClick() {
     if (!Elements.callButton.disabled) {
        sendMessage("player_action", { action: "call" });
        disableAllActions();
    }
}
export function handleBetClick() {
    if (!Elements.betButton.disabled) {
        const amount = parseInt(Elements.betSlider.value, 10);
        if (validateBetRaiseAmount(amount)) {
            sendMessage("player_action", { action: "bet", amount: amount });
            disableAllActions();
        }
    }
}
export function handleRaiseClick() {
    if (!Elements.raiseButton.disabled) {
        const amount = parseInt(Elements.betSlider.value, 10);
         if (validateBetRaiseAmount(amount)) {
            sendMessage("player_action", { action: "raise", amount: amount });
            disableAllActions();
        }
    }
}
export function handleSliderInput() {
    const amount = parseInt(Elements.betSlider.value, 10);
    if (!isNaN(amount)) {
        Elements.betAmountLabel.textContent = `$${amount}`;
        if (state.currentTurnOptions) {
             updateBetRaiseButtonText(amount,
                state.currentTurnOptions.actions.includes('bet'),
                state.currentTurnOptions.actions.includes('raise')
            );
        }
    }
}

export function handleNameSubmit() {
    const name = Elements.nameInput.value.trim().substring(0, MAX_NAME_LENGTH);
    if (name && state.myPlayerId) {
        sendMessage("set_name", { name: name });
        addLogMessage(`<strong>System:</strong> Name '${name}' submitted. Waiting for game...`, "system");
        Elements.nameModal.style.display = 'none';
    } else if (!name) {
        alert("Please enter a name.");
    } else {
        addLogMessage("<strong>System:</strong> Cannot set name - connection issue or no Player ID.", "error");
    }
}