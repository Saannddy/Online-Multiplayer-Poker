// js/ui.js
import { Elements, WINNER_DISPLAY_DURATION } from './config.js';
import { createCardElement, addLogMessage } from './utils.js';
import * as state from './state.js'; // Import state for playerMap etc.
import { disableAllActions } from './actions.js'; // Import action functions

// Store the last known hand ranks from showdown to display persistently
let lastHandRanks = {};

/** Clears player areas, community cards, and resets pot display */
export function clearTable() {
    Elements.pokerTable.querySelectorAll('.player-area').forEach(area => area.remove());
    Elements.communityCardArea.innerHTML = '<span class="waiting-message">Disconnected</span>';
    Elements.potArea.textContent = 'Pot: $?';
    hideWinnerDisplay();
    lastHandRanks = {}; // Clear ranks on disconnect/clear
}

/** Updates the entire game UI based on the state received from the server */
export function updateUI(serverState) {
    console.log("Updating UI with state:", serverState);
    state.setPlayerMap(serverState.players || {}); // Update local player map via state module

    // If the game stage indicates a new hand is starting, clear old ranks
    if (['idle', 'starting', 'preflop'].includes(serverState.game_stage)) {
        lastHandRanks = {};
    }

    Elements.pokerTable.querySelectorAll('.player-area').forEach(area => area.remove());

    const playerIds = Object.keys(state.playerMap);
    const myIdIndex = playerIds.findIndex(id => parseInt(id, 10) === state.myPlayerId);

    let displayOrderIds = [];
    if (myIdIndex !== -1) {
        displayOrderIds = [...playerIds.slice(myIdIndex), ...playerIds.slice(0, myIdIndex)];
    } else {
        displayOrderIds = playerIds; // Should only happen if player isn't in map yet
    }

    displayOrderIds.forEach((playerId, displayIndex) => {
        const player = state.playerMap[playerId];
        if (player) {
            // Pass game stage to createPlayerArea
            createPlayerArea(player, displayIndex, serverState.current_player_id, serverState.dealer_id, serverState.game_stage);
        }
    });

    updateCommunityCards(serverState.community_cards, serverState.game_stage);
    Elements.potArea.textContent = `Pot: $${serverState.pot || 0}`;

    // Ensure actions are hidden if game state dictates (e.g., showdown, hand_over)
    // or if it's not the player's turn
    if (['showdown', 'hand_over'].includes(serverState.game_stage) ||
        (serverState.current_player_id !== state.myPlayerId && Elements.actionArea.style.display === 'flex'))
    {
        disableAllActions();
    }
}

/** Creates and appends a player area div to the table */
function createPlayerArea(playerData, displayIndex, currentPlayerId, dealerId, gameStage) {
    const area = document.createElement('div');
    area.id = `player-area-${playerData.id}`;
    area.className = `player-area player-pos-${displayIndex}`;

    const nameDiv = document.createElement('div');
    nameDiv.className = 'player-name';
    const displayName = playerData.name || `Player ${playerData.id}`;
    nameDiv.textContent = displayName + (playerData.id === state.myPlayerId ? ' (You)' : '');
    nameDiv.title = displayName; // Tooltip for long names
    area.appendChild(nameDiv);

    const chipsDiv = document.createElement('div');
    chipsDiv.className = 'player-chips';
    chipsDiv.textContent = `$${playerData.stack ?? '?'}`;
    area.appendChild(chipsDiv);

    const cardsDiv = document.createElement('div');
    cardsDiv.className = 'player-cards';
    // Hand display logic is handled in get_state_for_player on server
    if (playerData.hand && playerData.hand.length > 0) {
        playerData.hand.forEach(cardStr => {
            // cardStr will be '??' if hand shouldn't be shown
            cardsDiv.appendChild(createCardElement(cardStr, cardStr === '??'));
        });
    } else if (playerData.status === 'folded') {
         // Optionally display something specific for folded hands if needed
         // cardsDiv.innerHTML = '<span class="folded-text">Folded</span>';
    }
    area.appendChild(cardsDiv);

    const statusDiv = document.createElement('div');
    statusDiv.className = 'player-status';
    // Pass gameStage and last known rank to status text function
    statusDiv.textContent = getPlayerStatusText(playerData, gameStage, lastHandRanks[playerData.id]);
    area.appendChild(statusDiv);

    // Apply styling based on player state
    if (playerData.id === parseInt(currentPlayerId, 10)) area.classList.add('player-current');
    if (playerData.id === parseInt(dealerId, 10)) area.classList.add('player-dealer');
    if (playerData.status === 'folded') area.classList.add('folded');
    else area.classList.remove('folded'); // Ensure folded class is removed if not folded

    Elements.pokerTable.appendChild(area);
}

/** Determines the status text for a player, prioritizing hand rank during showdown/end */
function getPlayerStatusText(playerData, gameStage, lastRank) {
    // If it's showdown/hand_over and we have a rank for this player, show it
    if (['showdown', 'hand_over'].includes(gameStage) && lastRank) {
        return lastRank;
    }

    // Otherwise, use the standard status logic
    switch (playerData.status) {
        case 'folded': return 'Folded';
        case 'all-in':
             // Show total bet amount when all-in for clarity
             return `All-in ($${playerData.total_bet_this_hand ?? playerData.current_bet ?? 0})`;
        case 'waiting': return 'Waiting...';
        case 'active':
        default:
            if (playerData.current_bet > 0) return `Bet: $${playerData.current_bet}`;
            // Show last action if it wasn't just a blind/ante
            if (playerData.last_action && playerData.last_action !== 'blind' && playerData.last_action !== 'ante') {
                 // Capitalize first letter
                 return playerData.last_action.charAt(0).toUpperCase() + playerData.last_action.slice(1);
            }
            return ''; // Default empty status
    }
}

/** Updates the community card display area */
function updateCommunityCards(cards, stage) {
     Elements.communityCardArea.innerHTML = ''; // Clear previous cards/message
    if (cards && cards.length > 0) {
        cards.forEach(cardStr => Elements.communityCardArea.appendChild(createCardElement(cardStr)));
    } else if (stage && !['idle', 'starting', 'hand_over', 'showdown'].includes(stage)) {
        // Display stage name while waiting for cards
        const stageText = stage.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase());
        Elements.communityCardArea.innerHTML = `<span class="waiting-message">Waiting for ${stageText}...</span>`;
    } else if (stage === 'showdown' || stage === 'hand_over') {
         // Don't show "Waiting for deal" during showdown/end phase if no cards were dealt
         if (!cards || cards.length === 0) {
            Elements.communityCardArea.innerHTML = `<span class="waiting-message">-</span>`; // Or empty
         }
    }
    else {
        // Default message before the hand starts
        Elements.communityCardArea.innerHTML = '<span class="waiting-message">Waiting for deal...</span>';
    }
}

/** Handles the display of revealed cards and hand ranks during showdown */
export function handleShowdownReveal(payload) {
     addLogMessage(`<strong>Game:</strong> --- Showdown ---`, "game");
     lastHandRanks = payload.handRanks || {}; // Store the received ranks

      if (payload.allHands) {
         Object.entries(payload.allHands).forEach(([playerId, hand]) => {
             const area = document.getElementById(`player-area-${playerId}`);
             if (area) {
                 const cardsContainer = area.querySelector('.player-cards');
                 const statusContainer = area.querySelector('.player-status');
                 const playerIntId = parseInt(playerId, 10); // Ensure ID is integer for map lookup

                 // Update cards display
                 if (cardsContainer) {
                    cardsContainer.innerHTML = ''; // Clear previous cards ('??')
                    if (hand && hand.length > 0) {
                         hand.forEach(cardStr => cardsContainer.appendChild(createCardElement(cardStr)));
                    } else {
                        // This case shouldn't happen if allHands only includes contenders
                        cardsContainer.innerHTML = '<span class="waiting-message">- Folded -</span>';
                    }
                 }

                 // Update status display with hand rank
                 if(statusContainer && lastHandRanks[playerId]) {
                    statusContainer.textContent = lastHandRanks[playerId]; // Display rank directly
                    statusContainer.title = lastHandRanks[playerId]; // Add tooltip
                 } else if (statusContainer) {
                     // If no rank provided (e.g., error), clear status or show default
                     const playerState = state.playerMap[playerIntId];
                     statusContainer.textContent = getPlayerStatusText(playerState, 'showdown', null); // Fallback status
                 }

                 // Ensure player area is not styled as folded during showdown reveal
                 area.classList.remove('folded');
             } else {
                 console.warn(`Showdown reveal: Player area not found for ID ${playerId}`);
             }
         });
     } else {
        console.warn("Showdown message received without 'allHands' data.");
        addLogMessage("<strong>System:</strong> Showdown occurred, but card data missing.", "system");
     }
     disableAllActions(); // Ensure actions remain disabled
}

/** Handles the display of pot winner information */
 export function handlePotAwarded(payload) {
     console.log("Pot awarded payload:", payload);
     if (payload.winners && payload.winners.length > 0) {
         // Show the winner popup display
         showWinnerDisplay(payload.winners, payload.isUncontested);

         // Log winner messages
         payload.winners.forEach(winner => {
              // Use name from payload if available, otherwise lookup in state
              const winnerName = winner.playerName || state.playerMap[winner.playerId]?.name || `Player ${winner.playerId}`;
              let winMessage = `<strong>${winnerName}</strong> wins $${winner.amount}`;
              // Add hand details if it wasn't an uncontested win AND rank is available
              if (!payload.isUncontested && winner.handRank) {
                  winMessage += ` with ${winner.handRank}`;
                  // Optionally show the specific 5 winning cards if available
                  if(winner.winningHand && winner.winningHand.length > 0) {
                     const actualCards = winner.winningHand.filter(c => c !== '??'); // Filter placeholder
                     if(actualCards.length > 0) winMessage += `: ${actualCards.join(', ')}`;
                  }
              } else if (payload.isUncontested) {
                 winMessage += ` (Uncontested)`;
              }
              addLogMessage(winMessage, "game");
         });
     } else {
         addLogMessage("<strong>Game:</strong> Pot awarded, but no winner information received.", "system");
     }
     addLogMessage(`<strong>Game:</strong> --- Hand Over ---`, "game");
     disableAllActions(); // Actions should already be disabled, but ensure it
     state.setCurrentTurnOptions(null); // Clear any lingering turn options
 }

/** Shows the winner display element with winner details */
 export function showWinnerDisplay(winnersData, isUncontested) {
    if (!Elements.winnerDisplay) return;
    let htmlContent = '';
     if (winnersData && winnersData.length > 0) {
         if (winnersData.length > 1) htmlContent = '<p>Split Pot!</p>'; // Indicate split pot
         winnersData.forEach(winner => {
             const winnerName = winner.playerName || state.playerMap[winner.playerId]?.name || `Player ${winner.playerId}`;
             htmlContent += `<p>${winnerName} wins $${winner.amount}`;
             // Show hand rank in popup only if not uncontested
             if (!isUncontested && winner.handRank) {
                 htmlContent += ` <span class="hand-rank">(${winner.handRank})</span>`;
             }
             htmlContent += `</p>`;
         });
     } else {
         htmlContent = '<p>Error: No winner info available</p>'; // Fallback message
     }
     Elements.winnerDisplay.innerHTML = htmlContent;
     Elements.winnerDisplay.style.display = 'block'; // Make it visible

     state.clearWinnerDisplayTimeout(); // Clear any existing timeout before setting a new one
     // Set a timeout to automatically hide the display after the specified duration
     const timeoutId = setTimeout(hideWinnerDisplay, WINNER_DISPLAY_DURATION);
     state.setWinnerDisplayTimeout(timeoutId); // Store the new timeout ID in state
 }

/** Hides the winner display element */
 export function hideWinnerDisplay() {
     if (Elements.winnerDisplay) Elements.winnerDisplay.style.display = 'none'; // Hide the element
     state.clearWinnerDisplayTimeout(); // Clear the timeout if hidden manually or automatically
 }
